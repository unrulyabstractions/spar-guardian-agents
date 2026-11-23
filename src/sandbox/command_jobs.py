"""Command calls with no time limit of their own, and the jobs they become when their round's time runs out.

    outcome = await run_command(Where(box=seat.box(), service=seat.service, user=seat.user, cwd=seat.workdir),
                                agent, "shell", command, ["bash", "--login", "-c", command], after=reap)
    outcome.text            # stderr then stdout, as Inspect's bash tool returns them; or the job's first answer
    bindings = job_bindings(cap=lambda agent: 65536)      # job_output and job_stop, for agents that hold a command tool
    await end_jobs()                                      # the first scorer: every job still running is killed

Every command tool (shell and run_python, a lone agent's bash and python, an influencer's peer_shell) runs its command
here. The call waits for its command until its round's time runs out (turn_rules.round_deadline: the round's start plus
SwarmConfig.round_time_s). Once that has passed, the round has one late wait of LATE_START_S, from the start of its
first call past the deadline: that call and every later call of the round (the calls of one reply run one after another)
wait only for what is left of it (late_wait), so a reply of k long calls ends its round at T plus one wait, not k. A
command that ends in time returns as it did under Inspect's exec: stderr, a newline, then stdout, the last KEEP_BYTES of
each stream, which the tool output cap then cuts; when the read of that output fails, as on a machine that restarts
after an agent killed its processes, it returns the exec's own result. A command still running then becomes a job: it
keeps running, the call returns its job id and its output so far (prompts.yaml jobs.kept) within the agent's cap, and
the round ends on time.
job_output returns the job's new output since the agent's last read and whether it still runs; once it has exited, the
exit code. job_stop kills it and every process it started, those it left running after it exited included. Where a
domain asks for it (SWE, whose agents are root on machines of their own), reap_round kills at the end of each of the
agent's rounds what its calls on its machine left running, sparing the calls still running and so its jobs. Both count
like shell (they are no board call). A job is its owner's alone: another agent's id reads "no such job". end_jobs kills
every job still running when the agents stop, and every process a job left running, before the access log's capture and
the graders, and the sample store's JOBS_STORE holds each job's record, which run.json "jobs" carries: {id, agent, tool,
machine, call_id, command, started, ended, status (running, exited, stopped, killed), exit_code, bytes {out, err}}.

How a command runs. One exec per call, as root: LAUNCH makes the call's directory under JOBS_DIR (call_name: the agent
and its number among its command calls; mode 700, root's, outside /tmp and every workspace, so neither a teammate nor a
/tmp snapshot sees it), writes its pid, starts a collector, gives the command the user, the working directory and the
environment Inspect's exec would (docker gives the passwd home as HOME, "/" for a uid with no entry, and the exec's
umask), and replaces itself with the command. The exec's root process is therefore `/usr/bin/timeout -k 5s 0s bash
--login -c <command>`, Inspect's own wrapper with its timeout disabled (WRAP), as before but for the duration: the
access log and the network log name its origin, its agent and its call as they did (exec_digest of the same argv). The
exec leads a session of its own (docker makes every exec a session leader), so the reaps after each command
(sandbox_hygiene.REAP, peer_shell.REAP) spare the session of every call still running, a job's included, through the pid
file. The exec's root is a child subreaper (PR_SET_CHILD_SUBREAPER, kept across exec), so a process whose parent dies
stays the root's child while the job runs. The collector, a detached root process, copies the command's stdout and
stderr from two pipes to `out` and `err` in that directory and keeps the last KEEP_BYTES of each (Inspect keeps as much
of a stream); `meta` holds each stream's total and the bytes dropped from its front, and `done` once both pipes closed.
Every POLL_S it also looks at the machine's process table (PROCS) and keeps in `procs` every process of the job: the
root, each child of one, and each process in the root's session or process group, so a background process, nohup, disown
and a process in a session of its own whose parent is a member are all the job's. It goes on looking after the pipes
close, until no member is left. STOP kills every member, the group first, again until none is left, at job_stop and at
the end of the run, whether the exec still runs or not. What it can miss: a process that leaves the session and the
group and whose whole chain of parents exits between two looks after the root itself has exited. READ prints a stream's
bytes from an offset: its stderr the command's stderr, its stdout the command's stdout then a line of JSON. The exec of
the command holds no subprocess slot of Inspect's (concurrency=False) and counts as no known work of the sample's
working time: the call does while it waits.

What stays outside. A job writes where its user can, and its processes count in the end state like any other
(sandbox_hygiene.STATE). A root agent (SWE) can read JOBS_DIR on its own machine: its own calls' output in the clear,
and a peer_shell call's (Where.sealed) only sealed. A sealed read returns the last SEALED_READ_BYTES of each stream
rather than KEEP_BYTES, so that both fit one exec's output in base64. Root there still sees the observer's command
line in /proc, as it did before the jobs, and could take the bytes from the pipes with SYS_PTRACE, which no agent
machine grants. A job's later reads and opens fall
after its call's ToolEvent, so the access log and the network log extend the call's window to the job's end
(job_windows).
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from inspect_ai._util.working import untracked_sandbox_execs  # no public way to keep a background exec off the clock
from inspect_ai.scorer import Scorer, Target, scorer
from inspect_ai.solver import TaskState
from inspect_ai.tool import ToolDef, ToolError
from inspect_ai.util import SandboxEnvironmentLimits, store
from orbit.tools import ToolContext, ToolFactory

from src.configuration.settings import load_prompts
from src.swarm.tool_output_caps import middle_cut
from src.swarm.turn_rules import JOBS_STORE, mark_job, mark_machine, round_deadline

JOBS_DIR = "/var/lib/swarm_jobs"
TRACKED_FILES = ["pid", "procs"]      # what a tracked call's folder keeps once it ended in time, for reap_round
ROUND_REAPS_STORE = "round_reaps"     # sample store: {agent: {rounds, calls, killed, errors}} of reap_round
KEEP_BYTES = SandboxEnvironmentLimits.MAX_EXEC_OUTPUT_SIZE     # 10 MiB, what Inspect's exec keeps of a stream
SEALED_READ_BYTES = KEEP_BYTES // 4     # a sealed read's last bytes per stream: both, in base64, fit one exec's output
LATE_START_S = 5          # the one wait of a round's calls that start at or after its end (late_wait)
POLL_S = 0.05            # how often a call's collector looks at the machine's processes for the job's (PROCS)
SETTLE_S = 2.0            # how long a read waits for the collector to drain, as docker exec waits for a stream
INFRA_TIMEOUT_S = 120     # READ, STOP and the end of the run: our own scripts
STOP_WAIT_S = 30          # how long a stop waits for the killed command's exec to return
TOOLS = ("job_output", "job_stop")
COMMAND_TOOLS = ("shell", "run_python", "bash", "python", "peer_shell")
"""The tools whose calls run here; an agent that holds one gets the job tools."""
WRAP = ["/usr/bin/timeout", "-k", "5s", "0s"]
"""What the command runs under: Inspect's own wrapper with its timeout disabled (GNU timeout reads a duration of 0 as
none), so it limits nothing. It keeps the root of the exec's process tree the shape the access log and the network log
name as an agent's and hash as the call's (shell_commands.AGENT_EXEC, access_monitor_daemon.exec_digest): bash alone
would replace itself with a lone command (`bash -c 'grep x f'` runs grep in its place), and the root would read as
the harness's. A machine without /usr/bin/timeout runs the command bare."""

PROCS = r'''
import os, subprocess

def table():
    """pid -> (ppid, pgid, sid, start) of every live process on the machine, zombies left out: /proc, or ps where
    there is none (macOS, the unit tests' LocalBox). start tells a pid from a later process that reuses it."""
    rows = {}
    if os.path.isdir("/proc/self"):
        for name in os.listdir("/proc"):
            if name.isdigit():
                try:
                    with open(f"/proc/{name}/stat") as f:
                        rest = f.read().rsplit(")", 1)[1].split()
                    if rest[0] != "Z":
                        rows[int(name)] = (int(rest[1]), int(rest[2]), int(rest[3]), rest[19])
                except (OSError, IndexError, ValueError):
                    pass
        return rows
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,pgid=,stat=,lstart="], capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = line.split(None, 4)
        try:
            if not parts[3].startswith("Z"):
                pid = int(parts[0])
                rows[pid] = (int(parts[1]), int(parts[2]), os.getsid(pid), parts[4].strip())
        except (OSError, IndexError, ValueError):
            pass
    return rows

def members(leader, known, rows, skip=()):
    """The processes of a job alive now, from `known` (pid -> start, the processes seen before, which it gains): each
    one still alive with its start, every process whose parent is one of them, and every process in the leader's
    session or process group. The session and the group count only while the leader's pid is its own or free: the
    kernel hands out no pid still in use as a session or a group, so a process that took the pid later is another
    exec's."""
    own = leader not in rows or rows[leader][3] == known.get(leader)
    alive = {pid for pid, start in known.items() if pid in rows and rows[pid][3] == start}
    grew = True
    while grew:
        grew = False
        for pid, (ppid, pgid, sid, start) in rows.items():
            if pid not in alive and pid not in skip and (ppid in alive or own and leader in (sid, pgid)):
                alive.add(pid)
                known[pid] = start
                grew = True
    return alive
'''
"""What LAUNCH's collector and STOP share: the machine's process table and the processes of one job in it."""

SEAL = r'''
import hashlib

def seal(key, name, at, data):
    """data, the bytes of stream `name` from offset `at`, XORed with the keystream of `key` there (SHA-256 of the
    key, the name and the block's number, per 32-byte block): the same call seals and unseals. No key: data."""
    if not key or not data:
        return data
    block, skip = divmod(at, 32)
    count = (skip + len(data) + 31) // 32
    stream = b"".join(hashlib.sha256(key + name.encode() + (block + i).to_bytes(8, "big")).digest()
                      for i in range(count))[skip:skip + len(data)]
    return (int.from_bytes(data, "big") ^ int.from_bytes(stream, "big")).to_bytes(len(data), "big")
'''
"""A sealed call's streams on its machine (Where.sealed): LAUNCH's collector seals each chunk as it writes it, and
the harness unseals what READ returns (seal)."""

LAUNCH = PROCS + SEAL + r'''
import fcntl, json, os, pwd, select, signal, sys, time
spec = json.loads(sys.argv[1])
argv = sys.argv[2:]
keep = int(spec["keep"])
key = None
if spec.get("sealed"):      # not dumpable: no process of the machine without SYS_PTRACE reads this one's memory
    try:
        import ctypes
        ctypes.CDLL(None, use_errno=True).prctl(4, 0, 0, 0, 0)
    except (ImportError, AttributeError, OSError):
        pass
    line = b""
    while not line.endswith(b"\n"):
        ch = os.read(0, 1)
        if not ch:
            break
        line += ch
    key = bytes.fromhex(line.decode().strip())
umask = os.umask(0o077)
top = spec["top"]
os.makedirs(top, mode=0o700, exist_ok=True)
if os.geteuid() == 0:
    os.chown(top, 0, 0)
os.chmod(top, 0o700)
where = os.path.join(top, spec["name"])
os.mkdir(where, 0o700)

def write_meta(meta):
    path = os.path.join(where, "meta")
    with open(path + ".new", "w") as f:
        json.dump(meta, f)
    os.replace(path + ".new", path)

def track(leader, known, seen):
    """One look at the job's processes (members); `procs` holds every one seen, for STOP. The live ones."""
    alive = members(leader, known, table(), skip={os.getpid()})
    if len(known) != seen[0]:
        seen[0] = len(known)
        with open(os.path.join(where, "procs.new"), "w") as f:
            json.dump({"leader": leader, "known": known}, f)
        os.replace(os.path.join(where, "procs.new"), os.path.join(where, "procs"))
    return alive

def collect(fds, leader, start):
    """Copy each pipe to its file, keeping the last `keep` bytes of each; meta's totals, drops and done. Every POLL_S
    look at the job's processes (track), and go on looking once both pipes closed, until none is left or the call's
    directory is gone (a call that ended in time)."""
    files = {name: open(os.path.join(where, name), "ab", buffering=0) for name in fds}
    meta = {name: {"total": 0, "dropped": 0} for name in fds}
    lock = open(os.path.join(where, "lock"), "w")
    write_meta({**meta, "done": False})
    known, seen, looked = {leader: start}, [0], 0.0
    alive = track(leader, known, seen)
    live = dict(fds)
    while live or alive:
        if time.time() - looked >= POLL_S:
            if not os.path.isdir(where):
                return
            alive, looked = track(leader, known, seen), time.time()
        if not live:
            time.sleep(POLL_S)
            continue
        ready, _, _ = select.select(list(live.values()), [], [], POLL_S)
        for name, fd in list(live.items()):
            if fd not in ready:
                continue
            data = os.read(fd, 65536)
            if not data:
                del live[name]
                continue
            files[name].write(seal(key, name, meta[name]["total"], data))
            meta[name]["total"] += len(data)
            size = os.fstat(files[name].fileno()).st_size
            if size > 2 * keep:
                fcntl.flock(lock, fcntl.LOCK_EX)
                path = os.path.join(where, name)
                with open(path, "rb") as f:
                    f.seek(size - keep)
                    tail = f.read()
                with open(path + ".new", "wb") as f:
                    f.write(tail)
                os.replace(path + ".new", path)
                files[name].close()
                files[name] = open(path, "ab", buffering=0)
                meta[name]["dropped"] += size - len(tail)
                write_meta({**meta, "done": False})
                fcntl.flock(lock, fcntl.LOCK_UN)
        if not live:
            fcntl.flock(lock, fcntl.LOCK_EX)
            write_meta({**meta, "done": True})
            fcntl.flock(lock, fcntl.LOCK_UN)

POLL_S = float(spec.get("poll") or 0.05)
leader = os.getpid()
start = table().get(leader, (0, 0, 0, None))[3]
r_out, w_out = os.pipe()
r_err, w_err = os.pipe()
first = os.fork()
if first == 0:
    if os.fork() == 0:
        os.setsid()
        null = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(null, fd)
        os.close(w_out)
        os.close(w_err)
        try:
            collect({"out": r_out, "err": r_err}, leader, start)
        except OSError:
            pass
        finally:
            os._exit(0)
    os._exit(0)
os.waitpid(first, 0)
os.close(r_out)
os.close(r_err)
with open(os.path.join(where, "pid"), "w") as f:
    f.write(str(os.getpid()))
os.dup2(w_out, 1)
os.dup2(w_err, 2)
os.close(w_out)
os.close(w_err)
env = dict(os.environ)
user = spec.get("user")
if user and user != "root":
    name, _, group = user.partition(":")
    if name.isdigit():
        uid = int(name)
        gid = int(group) if group else uid
        try:
            home = pwd.getpwuid(uid).pw_dir
        except KeyError:
            home = "/"
        os.setgroups([])
    else:
        entry = pwd.getpwnam(name)
        uid, gid, home = entry.pw_uid, int(group) if group else entry.pw_gid, entry.pw_dir
        os.initgroups(name, gid)
    os.setgid(gid)
    os.setuid(uid)
    env["HOME"] = home
env.update(spec.get("env") or {})
os.umask(umask)
wrap = spec.get("wrap") or []
if wrap and os.access(wrap[0], os.X_OK):
    argv = [*wrap, *argv]
try:        # PR_SET_CHILD_SUBREAPER, kept across exec: an orphan of the job stays the root's child while it runs
    import ctypes
    ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0)
except (ImportError, AttributeError, OSError):
    pass
try:
    os.execvpe(argv[0], argv, env)
except OSError as ex:
    os.write(2, f"{argv[0]}: {ex.strerror}\n".encode())
    os._exit(127)
'''
"""argv: the spec ({top, name, user, env, keep, wrap, poll, sealed}) as JSON, then the command's argv. The pid file is
written as root, before the user is taken: a reap that sees a process of the user reads its pid file after. From the
pid file on, the launcher's own failures (a user with no passwd entry) go to the command's stderr. The command gets
the exec's own umask back; the call's files are made under 077. A sealed call's first line of stdin is its key, in
hex; the command gets the rest."""

READ = r'''
import base64, fcntl, json, os, sys, time
spec = json.load(sys.stdin)
where, keep = spec["dir"], int(spec["keep"])
deadline = time.time() + float(spec.get("wait_s") or 0)

def meta():
    try:
        with open(os.path.join(where, "meta")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None

while time.time() < deadline and not (meta() or {}).get("done"):
    time.sleep(0.05)
found = os.path.isdir(where)
head = {"found": found, "done": False}
data = {"out": b"", "err": b""}
if found:
    lock = open(os.path.join(where, "lock"), "a")
    fcntl.flock(lock, fcntl.LOCK_SH)
    m = meta() or {}
    head["done"] = bool(m.get("done"))
    for name in ("out", "err"):
        dropped = (m.get(name) or {}).get("dropped", 0)
        path = os.path.join(where, name)
        size = os.path.getsize(path) if os.path.exists(path) else 0
        start = max(int((spec.get("from") or {}).get(name, 0)), dropped)
        whole = b""
        if spec.get("data", True) and os.path.exists(path):
            with open(path, "rb") as f:
                f.seek(max(0, start - dropped))
                whole = f.read()
        chunk = whole[-keep:]
        head[name] = {"total": dropped + size, "from": start, "shown": len(chunk)}
        if spec.get("sealed"):      # sealed bytes travel as base64 in the JSON line, from their offset
            head[name].update(at=start + len(whole) - len(chunk), data=base64.b64encode(chunk).decode())
        else:
            data[name] = chunk
    fcntl.flock(lock, fcntl.LOCK_UN)
    if spec.get("remove"):
        keep_files = set(spec.get("keep_files") or ())
        for name in os.listdir(where):
            if name not in keep_files:
                os.unlink(os.path.join(where, name))
        if not keep_files:
            os.rmdir(where)
sys.stderr.buffer.write(data["err"])
sys.stdout.buffer.write(data["out"] + b"\n" + json.dumps(head).encode() + b"\n")
'''
"""stdin {dir, keep, from {out, err}, wait_s, data, remove, keep_files}: waits up to wait_s for `done`, then prints
each stream from its offset (`from`, the bytes already shown; never before what was dropped), its last `keep` bytes,
stderr to its stderr and stdout to its stdout, then a newline and a line of JSON {found, done, out: {total, from,
shown}, err}. With `sealed` no stream is printed raw: each one's sealed bytes are base64 in its JSON entry (data), from
the offset `at`. remove deletes the call's folder, or with keep_files every file but those (a tracked call's pid and
procs)."""

STOP = PROCS + r'''
import json, os, signal, sys, time
spec = json.load(sys.stdin)
killed = []
for where in spec.get("dirs") or [os.path.dirname(path) for path in spec.get("pid_files") or []]:
    leader, known = None, {}
    try:
        with open(os.path.join(where, "procs")) as f:
            procs = json.load(f)
        leader, known = int(procs["leader"]), {int(k): v for k, v in procs["known"].items()}
    except (OSError, ValueError, KeyError):
        pass
    if leader is None:
        try:
            with open(os.path.join(where, "pid")) as f:
                leader = int(f.read().strip())
        except (OSError, ValueError):
            continue
    for attempt in range(20):
        rows = table()
        if attempt == 0 and (leader not in rows or rows[leader][3] == known.get(leader, rows[leader][3])):
            known.setdefault(leader, rows.get(leader, (0, 0, 0, None))[3])
            try:
                os.killpg(leader, signal.SIGKILL)
            except OSError:
                pass
            rows = table()
        alive = members(leader, known, rows, skip={os.getpid()})
        if not alive:
            break
        for pid in alive:
            try:
                os.kill(pid, signal.SIGKILL)
                killed.append(pid)
            except OSError:
                pass
        time.sleep(0.05)
print(json.dumps({"killed": sorted(set(killed))}))
'''
"""stdin {dirs} (or {pid_files}, each in its call's directory): SIGKILL every process of each call's job (PROCS members,
from the collector's `procs` and the pid file): the leader's process group first, then every member left, again until
none is, so a process forked while the others die goes too. A job that exited is stopped the same way: what it left
running dies."""


@dataclass(frozen=True)
class Where:
    """Where a command runs: the sandbox environment, its service name (None for the sample's default), the user
    (a name, "uid:gid", or None for the service's own, root in every image we run), the working directory, the
    environment it adds, and whether its output is sealed on that machine: a peer_shell call runs on the watched
    agent's machine, where that agent may be root (SWE), so its streams are kept there sealed (SEAL) with a key only
    the harness and the call's collector hold, which reaches the launcher on the exec's stdin."""
    box: object
    service: str | None = None
    user: str | None = None
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    sealed: bool = False
    tracked: bool = False
    """Whether a call that ends in time keeps its folder's pid and procs, so its collector goes on finding what it left
    running until reap_round kills it at the end of the round (SWE)."""


@dataclass
class Call:
    """One command call, in the sample's memory (calls) while it runs or once it became a job."""
    handle: str
    agent: str
    tool: str
    where: Where
    command: str
    started: float
    task: asyncio.Future
    job: str | None = None
    ended: float | None = None
    status: str = "running"
    cursor: dict[str, int] = field(default_factory=lambda: {"out": 0, "err": 0})
    totals: dict[str, int] = field(default_factory=lambda: {"out": 0, "err": 0})
    key: bytes | None = None        # a sealed call's key, in the harness's memory only

    @property
    def dir(self) -> str:
        return f"{JOBS_DIR}/{self.handle}"

    @property
    def pid_file(self) -> str:
        return f"{self.dir}/pid"

    @property
    def exit_code(self) -> int | None:
        if not self.task.done() or self.task.cancelled() or self.task.exception() is not None:
            return None
        return self.task.result().returncode


class ReadFailed(RuntimeError):
    """READ gave no head: the machine did not run it, as while it restarts after its processes were killed."""


@dataclass(frozen=True)
class Outcome:
    """A command call's answer: text, and the job it became (None when the command ended in time). returncode is the
    command's, None for a job."""
    text: str
    job: str | None = None
    returncode: int | None = None


def now() -> float:
    return time.time()


def _compiled(source: str) -> dict:
    space: dict = {}
    exec(compile(source, "<command_jobs>", "exec"), space)  # noqa: S102 - our own script, the one LAUNCH runs
    return space


seal = _compiled(SEAL)["seal"]


def launch_argv(spec: dict, argv: list[str]) -> list[str]:
    """The exec of one command call: LAUNCH, its spec, then the command's argv."""
    return ["python3", "-I", "-c", LAUNCH, json.dumps(spec), *argv]


def command_argv(exec_argv: list) -> list:
    """The argv the exec's root process runs under once LAUNCH has replaced itself: the spec's wrap (WRAP), then the
    command's (launch_argv's tail), in the exec's own type (str or bytes). Any other exec's argv comes back as it is."""
    text = [x.decode(errors="replace") if isinstance(x, bytes) else x for x in exec_argv[:5]]
    if len(exec_argv) > 5 and text[:4] == ["python3", "-I", "-c", LAUNCH]:
        wrap = json.loads(text[4]).get("wrap") or []
        kind = type(exec_argv[5])
        return [*(w.encode() if kind is bytes else w for w in wrap), *exec_argv[5:]]
    return list(exec_argv)


@dataclass
class Memory:
    """One sample's command calls by handle (those still running and the jobs), the end of each round's late wait
    by (agent, deadline), each agent's command calls so far (call_name's numbers), the agent and the machine of every
    call (machines) and the calls a round's reap has stopped (reaped)."""
    store: object
    calls: dict[str, Call] = field(default_factory=dict)
    late: dict[tuple[str, float], float] = field(default_factory=dict)
    numbers: dict[str, int] = field(default_factory=dict)
    machines: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    reaped: set[str] = field(default_factory=set)


_SAMPLES: dict[int, Memory] = {}
"""id(the sample's store) -> its Memory. The store is the one object every context of a sample shares, the scorers'
included, which a ContextVar set in the setup is not."""


def memory() -> Memory:
    st = store()
    held = _SAMPLES.get(id(st))
    if held is None or held.store is not st:
        held = _SAMPLES[id(st)] = Memory(st)
    return held


def calls() -> dict[str, Call]:
    """The sample's command calls in memory: those still running and the jobs, by handle."""
    return memory().calls


def call_name(agent: str, number: int) -> str:
    """The folder of the agent's number-th command call under JOBS_DIR (its handle): `<agent>-<number>`, the same in
    a run and in its replay (src/replay/replay_jobs.py), so a command that lists the folder reads the same names, and
    no two calls of a sample share one. A character other than a letter, a digit, `.`, `_` or `-` becomes `_`."""
    return f"{re.sub(r'[^A-Za-z0-9._-]', '_', agent)}-{number}"


def next_call_name(agent: str) -> str:
    """call_name for the agent's next command call of the sample."""
    numbers = memory().numbers
    numbers[agent] = numbers.get(agent, 0) + 1
    return call_name(agent, numbers[agent])


def late_wait(agent: str, deadline: float | None) -> float | None:
    """How long a call of the agent's round that starts now waits for its command: None with no round clock; until
    the deadline before it; past it, what is left of the round's one late wait of LATE_START_S, which the round's
    first call past the deadline starts (0 once it has run out)."""
    if deadline is None:
        return None
    at = now()
    if at < deadline:
        return deadline - at
    until = memory().late.setdefault((agent, deadline), at + LATE_START_S)
    return max(until - at, 0.0)


def forget() -> None:
    """Drop the sample's calls from memory, once its jobs have ended."""
    _SAMPLES.pop(id(store()), None)


def running_pid_files(service: str | None, but: str | None = None) -> list[str]:
    """The pid files of the calls still running on one machine, `but` left out: the sessions a reap there spares."""
    return [c.pid_file for c in calls().values() if c.where.service == service and c.handle != but
            and not c.task.done()]


def _settled(task: asyncio.Future) -> None:
    """Keep a background exec's failure from being logged as never retrieved: its call reads it."""
    if not task.cancelled():
        task.exception()


async def read(call: Call, *, wait_s: float = 0.0, final: bool = False, data: bool = True) -> tuple[str, str, dict]:
    """(stderr, stdout, head) of the call's streams from its cursor (READ); `final` reads from the start and removes
    the call's directory, a tracked call's pid and procs kept (Where.tracked). The cursor moves to each stream's
    total."""
    mark_machine(call.where.service, call.where.box)
    spec = {"dir": call.dir, "keep": SEALED_READ_BYTES if call.key else KEEP_BYTES, "wait_s": wait_s, "data": data,
            "remove": final, "from": {"out": 0, "err": 0} if final else call.cursor, "sealed": bool(call.key),
            "keep_files": TRACKED_FILES if final and call.where.tracked else []}
    r = await call.where.box.exec(["python3", "-I", "-c", READ], input=json.dumps(spec), user="root",
                                  timeout=INFRA_TIMEOUT_S)
    out, _, line = (r.stdout or "").rstrip("\n").rpartition("\n")
    err = r.stderr or ""
    try:
        head = json.loads(line)
    except ValueError:
        raise ReadFailed(f"command jobs: the read failed: {(r.stderr or r.stdout or '')[-500:]}") from None
    if call.key:
        out, err = (seal(call.key, name, head[name]["at"], base64.b64decode(head[name].pop("data")))
                    .decode(errors="replace") if name in head else "" for name in ("out", "err"))
    for name in ("out", "err"):
        if name in head:
            call.totals[name] = call.cursor[name] = head[name]["total"]
    return err, out, head


def joined(stderr: str, stdout: str) -> str:
    """The streams as Inspect's bash and python tools join them: stderr and a newline when there is any, then
    stdout."""
    return (f"{stderr}\n" if stderr else "") + stdout


def fitted(head: str, output: str, cap: int, *, first: bool = False) -> str:
    """head, then the output under its line (prompts.yaml jobs so_far or new, a newline before the output), cut in
    the middle (tool_output_caps.middle_cut) so the whole fits `cap` bytes; with no output, the line that says so
    (nothing_yet or no_new). first: the call's own answer, which shows the output so far."""
    texts = load_prompts()["jobs"]
    if not output:
        return f"{head} {texts['nothing_yet' if first else 'no_new']}"
    head = f"{head} {texts['so_far' if first else 'new']}"
    room = max(0, cap - len(head.encode()) - 1)
    return f"{head}\n{middle_cut(output, room) or output}"


def next_job_id() -> str:
    return f"j{len(store().get(JOBS_STORE) or {}) + 1}"


def record(call: Call) -> None:
    """Write the call's job record into the sample store (JOBS_STORE)."""
    st = store()
    jobs = dict(st.get(JOBS_STORE) or {})
    old = jobs.get(call.job) or {}
    jobs[call.job] = {"id": call.job, "agent": call.agent, "tool": call.tool, "machine": call.where.service,
                      "call_id": old.get("call_id"), "command": call.command[:1000], "started": round(call.started, 3),
                      "ended": round(call.ended, 3) if call.ended is not None else None, "status": call.status,
                      "exit_code": call.exit_code, "bytes": dict(call.totals)}
    st.set(JOBS_STORE, jobs)


async def run_command(where: Where, agent: str, tool: str, command: str, argv: list[str], *,
                      input: str | None = None, after: Callable[[list[str]], Awaitable[None]] | None = None,
                      cap: int | None = None) -> Outcome:
    """Run argv (the call's `command`) for the agent and wait for it until its round's time runs out. In time:
    `after(pid files to spare)` runs first (the seat's reap), then the output comes back whole, as Inspect's exec
    gave it. Past it: the call becomes a job (the module's docstring), its answer within `cap` bytes."""
    handle = next_call_name(agent)
    memory().machines[handle] = (agent, where.service)
    mark_machine(where.service, where.box)
    spec = {"top": JOBS_DIR, "name": handle, "user": where.user, "env": where.env, "keep": KEEP_BYTES, "wrap": WRAP,
            "poll": POLL_S, "sealed": where.sealed}
    key = secrets.token_bytes(32) if where.sealed else None
    if key is not None:
        input = f"{key.hex()}\n{input or ''}"
    with untracked_sandbox_execs():
        task = asyncio.ensure_future(where.box.exec(launch_argv(spec, argv), input=input, cwd=where.cwd, user="root",
                                                    concurrency=False))
    call = Call(handle=handle, agent=agent, tool=tool, where=where, command=command, started=now(), task=task, key=key)
    task.add_done_callback(_settled)
    task.add_done_callback(lambda _: setattr(call, "ended", call.ended or now()))
    calls()[handle] = call
    deadline = round_deadline(agent)
    wait = late_wait(agent, deadline)
    done, _ = await asyncio.wait({task}, timeout=wait)
    if done:
        calls().pop(handle, None)
        result = task.result()
        if after is not None:
            await after(running_pid_files(where.service, but=handle))
        try:
            stderr, stdout, head = await read(call, wait_s=SETTLE_S, final=True)
        except ReadFailed:              # the machine went down under the call and is restarting
            head = {}
        if not head.get("found"):       # that, or the launch failed before its directory: the exec's own words
            stderr, stdout = result.stderr or "", result.stdout or ""
        return Outcome(joined(stderr, stdout), returncode=result.returncode)
    call.job = next_job_id()
    record(call)            # before any await: a call of the same batch kept at the same time takes the next id
    mark_job(call.job)
    stderr, stdout, _ = await read(call)
    record(call)
    text = load_prompts()["jobs"]["kept"].format(job=call.job)
    return Outcome(fitted(text, joined(stderr, stdout), cap or KEEP_BYTES, first=True), job=call.job)


def owned(agent: str, job: str) -> Call:
    """The agent's job of that id, or a ToolError: another agent's job reads as no such job."""
    found = next((c for c in calls().values() if c.job == job and c.agent == agent), None)
    if found is None:
        raise ToolError(load_prompts()["jobs"]["unknown"].format(job=job))
    return found


def status_line(call: Call) -> str:
    texts = load_prompts()["jobs"]
    if call.status == "stopped":
        return texts["stopped"].format(job=call.job)
    if call.task.done():
        code = call.exit_code
        return texts["exited"].format(job=call.job, code=code if code is not None else "unknown")
    return texts["running"].format(job=call.job)


def finished(call: Call) -> None:
    """A job whose exec returned while nobody stopped it has exited."""
    if call.task.done() and call.status == "running":
        call.status = "exited"


async def stop(call: Call, status: str) -> None:
    """Kill every process of the call's job (STOP), the exec's and what it left running, and wait for its exec to
    return; status names who stopped it. A job that had exited keeps "exited": only what it left running dies."""
    running = not call.task.done()
    mark_machine(call.where.service, call.where.box)
    await call.where.box.exec(["python3", "-I", "-c", STOP], input=json.dumps({"dirs": [call.dir]}), user="root",
                              timeout=INFRA_TIMEOUT_S)
    if running:
        await asyncio.wait({call.task}, timeout=STOP_WAIT_S)
        call.status = status
        call.ended = call.ended or now()
    finished(call)


async def reap_round(agent: str, box, service: str | None) -> dict:
    """At the end of the agent's round: kill what its calls on this machine left running (STOP over their folders),
    those still running spared, its jobs among them. A call is reaped once, after it ended: nothing of it can come
    back. {"calls": the calls reaped, "killed": [pid]}, killed naming the members STOP killed one by one after their
    call's process group; a machine with nothing to reap gets no exec."""
    held = memory()
    running = {c.handle for c in calls().values() if not c.task.done()}
    due = [h for h, (owner, machine) in held.machines.items()
           if owner == agent and machine == service and h not in running and h not in held.reaped]
    if not due:
        return {"calls": 0, "killed": []}
    dirs = [f"{JOBS_DIR}/{h}" for h in due]
    r = await box.exec(["python3", "-I", "-c", STOP], input=json.dumps({"dirs": dirs}), user="root",
                       timeout=INFRA_TIMEOUT_S)
    if r.returncode != 0:
        raise RuntimeError(f"the round's reap failed: {(r.stderr or r.stdout).strip()[-300:]}")
    held.reaped.update(due)
    ended = [d for h, d in zip(due, dirs) if h not in {c.handle for c in calls().values()}]   # a job keeps its folder
    if ended:
        await box.exec(["rm", "-rf", "--", *ended], user="root", timeout=INFRA_TIMEOUT_S)
    return {"calls": len(due), "killed": json.loads(r.stdout.strip().splitlines()[-1])["killed"]}


def record_round_reap(agent: str, result: dict | None, error: str | None = None) -> None:
    """Count one round's reap in ROUND_REAPS_STORE: the rounds, the calls reaped, the processes killed and the errors
    (five at most)."""
    st = store()
    reaps = dict(st.get(ROUND_REAPS_STORE) or {})
    mine = dict(reaps.get(agent) or {"rounds": 0, "calls": 0, "killed": 0, "errors": []})
    mine["rounds"] += 1
    mine["calls"] += (result or {}).get("calls", 0)
    mine["killed"] += len((result or {}).get("killed") or [])
    if error and len(mine["errors"]) < 5:
        mine["errors"] = [*mine["errors"], error]
    reaps[agent] = mine
    st.set(ROUND_REAPS_STORE, reaps)


async def job_output(agent: str, job: str, cap: int) -> str:
    """The job's status line and its new output since the agent's last read, within `cap` bytes."""
    call = owned(agent, job)
    finished(call)
    stderr, stdout, _ = await read(call, wait_s=SETTLE_S if call.task.done() else 0.0)
    record(call)
    return fitted(status_line(call), joined(stderr, stdout), cap)


async def job_stop(agent: str, job: str, cap: int) -> str:
    """Stop the job, then its status line and its new output."""
    call = owned(agent, job)
    await stop(call, "stopped")
    stderr, stdout, _ = await read(call, wait_s=SETTLE_S)
    record(call)
    return fitted(status_line(call), joined(stderr, stdout), cap)


def job_bindings(cap: Callable[[str], int]) -> dict[str, ToolFactory]:
    """ORBIT tool_bindings for job_output and job_stop, built per agent; cap(agent) is the bytes of one answer it sees
    whole (tool_output_caps.cap_of)."""
    texts = load_prompts()["jobs"]

    def make(name: str, run: Callable[[str, str, int], Awaitable[str]]) -> ToolFactory:
        def factory(ctx: ToolContext) -> ToolDef:
            agent = ctx.agent_name

            async def execute(job: str) -> str:
                return await run(agent, job, cap(agent))

            return ToolDef(execute, name=name, description=texts[name], parameters={"job": texts["job"]})
        return ToolFactory(factory)

    return {"job_output": make("job_output", job_output), "job_stop": make("job_stop", job_stop)}


async def end_jobs() -> list[dict]:
    """At the end of the run: kill every process of every command call still held, a job's and what a job that
    exited left running (a job still running gets status "killed"), let each exec return, and write every job's
    record with its totals. The records, in order. Safe to call twice."""
    for call in list(calls().values()):
        try:
            await stop(call, "killed")
        except Exception:  # noqa: BLE001 - a machine already gone ends its jobs with it
            if not call.task.done():
                call.status, call.ended = "killed", call.ended or now()
        finished(call)
        if call.job is not None:
            try:
                await read(call, data=False)
            except Exception:  # noqa: BLE001 - the totals stay as last read
                pass
            record(call)
        calls().pop(call.handle, None)
    forget()
    jobs = store().get(JOBS_STORE) or {}
    return [jobs[k] for k in sorted(jobs, key=lambda k: int(k[1:]))]


@scorer(metrics=[])
def jobs_capture() -> Scorer:
    """The first scorer: end_jobs, so no job runs while the access log closes and the graders read. It gives no
    score."""
    async def score(state: TaskState, target: Target) -> None:
        await end_jobs()
    return score


def job_windows(jobs: list[dict] | dict | None) -> dict[str, float | None]:
    """call id -> the end of the job it became (None while it still ran), for the logs that tie a row to a call by its
    window (src/export/_export_access_log.py, _export_network_log.py)."""
    rows = list(jobs.values()) if isinstance(jobs, dict) else list(jobs or [])
    return {j["call_id"]: j.get("ended") for j in rows if j.get("call_id")}
