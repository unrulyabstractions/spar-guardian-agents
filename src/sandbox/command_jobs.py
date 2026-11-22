"""Command calls with no time limit of their own, and the jobs they become when their round's time runs out.

    outcome = await run_command(Where(box=seat.box(), service=seat.service, user=seat.user, cwd=seat.workdir),
                                agent, "shell", command, ["bash", "--login", "-c", command], after=reap)
    outcome.text            # stderr then stdout, as Inspect's bash tool returns them; or the job's first answer
    bindings = job_bindings(cap=lambda agent: 65536)      # job_output and job_stop, for agents that hold a command tool
    await end_jobs()                                      # the first scorer: every job still running is killed

Every command tool (shell and run_python, a lone agent's bash and python, an influencer's peer_shell) runs its command
here. The call waits for its command until its round's time runs out (turn_rules.round_deadline: the round's start
plus SwarmConfig.round_time_s); a call that starts after that still waits LATE_START_S. A command that ends in time
returns as it did under Inspect's exec: stderr, a newline, then stdout, the last KEEP_BYTES of each stream, which the
tool output cap then cuts. A command still running then becomes a job: it keeps running, the call returns its job id
and its output so far (prompts.yaml jobs.kept) within the agent's cap, and the round ends on time. job_output returns
the job's new output since the agent's last read and whether it still runs; once it has exited, the exit code.
job_stop kills it. Both count like shell (they are no board call). A job is its owner's alone: another agent's id
reads "no such job". end_jobs kills every job still running when the agents stop, before the access log's capture and
the graders, and the sample store's JOBS_STORE holds each job's record, which run.json "jobs" carries: {id, agent,
tool, machine, call_id, command, started, ended, status (running, exited, stopped, killed), exit_code, bytes {out,
err}}.

How a command runs. One exec per call, as root: LAUNCH makes the call's directory under JOBS_DIR (mode 700, root's,
outside /tmp and every workspace, so neither a teammate nor a /tmp snapshot sees it), writes its pid, starts a
collector, gives the command the user, the working directory and the environment Inspect's exec would (docker gives
the passwd home as HOME, "/" for a uid with no entry), and replaces itself with the command. The exec's root process
is therefore `bash --login -c <command>` as before: the access log and the network log name its origin, its agent and
its call as they did (exec_digest of the same argv). The exec leads a session of its own (docker makes every exec a
session leader), so the reaps after each command (sandbox_hygiene.REAP, peer_shell.REAP) spare the session of every
call still running, a job's included, through the pid file, and STOP kills a job by its session. The collector, a
detached root process, copies the command's stdout and stderr from two pipes to `out` and `err` in that directory and
keeps the last KEEP_BYTES of each (Inspect keeps as much of a stream); `meta` holds each stream's total and the bytes
dropped from its front, and `done` once both pipes closed. READ prints a stream's bytes from an offset: its stderr the
command's stderr, its stdout the command's stdout then a line of JSON. The exec of the command holds no subprocess
slot of Inspect's (concurrency=False) and counts as no known work of the sample's working time: the call does while
it waits.

What stays outside. A job writes where its user can, and its processes count in the end state like any other
(sandbox_hygiene.STATE). A root agent (SWE) can read JOBS_DIR on its own machine. A job's later reads and opens fall
after its call's ToolEvent, so the access log and the network log extend the call's window to the job's end
(job_windows).
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
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
from src.swarm.turn_rules import JOBS_STORE, mark_job, round_deadline

JOBS_DIR = "/var/lib/swarm_jobs"
KEEP_BYTES = SandboxEnvironmentLimits.MAX_EXEC_OUTPUT_SIZE     # 10 MiB, what Inspect's exec keeps of a stream
LATE_START_S = 5          # what a call that starts at or after its round's end still waits for its command
SETTLE_S = 2.0            # how long a read waits for the collector to drain, as docker exec waits for a stream
INFRA_TIMEOUT_S = 120     # READ, STOP and the end of the run: our own scripts
STOP_WAIT_S = 30          # how long a stop waits for the killed command's exec to return
TOOLS = ("job_output", "job_stop")
COMMAND_TOOLS = ("shell", "run_python", "bash", "python", "peer_shell")
"""The tools whose calls run here; an agent that holds one gets the job tools."""

LAUNCH = r'''
import fcntl, json, os, pwd, select, signal, sys, time
spec = json.loads(sys.argv[1])
argv = sys.argv[2:]
keep = int(spec["keep"])
os.umask(0o077)
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

def collect(fds):
    """Copy each pipe to its file, keeping the last `keep` bytes of each; meta's totals, drops and done."""
    files = {name: open(os.path.join(where, name), "ab", buffering=0) for name in fds}
    meta = {name: {"total": 0, "dropped": 0} for name in fds}
    lock = open(os.path.join(where, "lock"), "w")
    write_meta({**meta, "done": False})
    live = dict(fds)
    while live:
        ready, _, _ = select.select(list(live.values()), [], [])
        for name, fd in list(live.items()):
            if fd not in ready:
                continue
            data = os.read(fd, 65536)
            if not data:
                del live[name]
                continue
            files[name].write(data)
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
    fcntl.flock(lock, fcntl.LOCK_EX)
    write_meta({**meta, "done": True})

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
            collect({"out": r_out, "err": r_err})
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
try:
    os.execvpe(argv[0], argv, env)
except OSError as ex:
    os.write(2, f"{argv[0]}: {ex.strerror}\n".encode())
    os._exit(127)
'''
"""argv: the spec ({top, name, user, env, keep}) as JSON, then the command's argv. The pid file is written as root,
before the user is taken: a reap that sees a process of the user reads its pid file after. From the pid file on, the
launcher's own failures (a user with no passwd entry) go to the command's stderr."""

READ = r'''
import fcntl, json, os, sys, time
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
        chunk = b""
        if spec.get("data", True) and os.path.exists(path):
            with open(path, "rb") as f:
                f.seek(max(0, start - dropped))
                chunk = f.read()
        chunk = chunk[-keep:]
        data[name] = chunk
        head[name] = {"total": dropped + size, "from": start, "shown": len(chunk)}
    fcntl.flock(lock, fcntl.LOCK_UN)
    if spec.get("remove"):
        for name in os.listdir(where):
            os.unlink(os.path.join(where, name))
        os.rmdir(where)
sys.stderr.buffer.write(data["err"])
sys.stdout.buffer.write(data["out"] + b"\n" + json.dumps(head).encode() + b"\n")
'''
"""stdin {dir, keep, from {out, err}, wait_s, data, remove}: waits up to wait_s for `done`, then prints each stream
from its offset (`from`, the bytes already shown; never before what was dropped), its last `keep` bytes, stderr to its
stderr and stdout to its stdout, then a newline and a line of JSON {found, done, out: {total, from, shown}, err}."""

STOP = r'''
import json, os, signal, sys, time
spec = json.load(sys.stdin)

def session(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return int(f.read().rsplit(")", 1)[1].split()[3])
    except (OSError, IndexError, ValueError):
        return None

leaders = []
for path in spec["pid_files"]:
    try:
        with open(path) as f:
            leaders.append(int(f.read().strip()))
    except (OSError, ValueError):
        pass
killed = []
for leader in leaders:
    try:
        os.killpg(leader, signal.SIGKILL)
    except OSError:
        pass
    if os.path.isdir("/proc"):
        for pid in os.listdir("/proc"):
            if pid.isdigit() and int(pid) != os.getpid() and session(pid) == leader:
                try:
                    os.kill(int(pid), signal.SIGKILL)
                    killed.append(int(pid))
                except OSError:
                    pass
print(json.dumps({"leaders": leaders, "killed": sorted(set(killed))}))
'''
"""stdin {pid_files}: SIGKILL every process in the session each pid file names, its process group first."""


@dataclass(frozen=True)
class Where:
    """Where a command runs: the sandbox environment, its service name (None for the sample's default), the user
    (a name, "uid:gid", or None for the service's own, root in every image we run), the working directory and the
    environment it adds."""
    box: object
    service: str | None = None
    user: str | None = None
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)


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


@dataclass(frozen=True)
class Outcome:
    """A command call's answer: text, and the job it became (None when the command ended in time). returncode is the
    command's, None for a job."""
    text: str
    job: str | None = None
    returncode: int | None = None


def now() -> float:
    return time.time()


def launch_argv(spec: dict, argv: list[str]) -> list[str]:
    """The exec of one command call: LAUNCH, its spec, then the command's argv."""
    return ["python3", "-I", "-c", LAUNCH, json.dumps(spec), *argv]


def command_argv(exec_argv: list) -> list:
    """The argv the exec's root process runs under once LAUNCH has replaced itself: the command's (launch_argv's
    tail), in the exec's own type (str or bytes). Any other exec's argv comes back as it is."""
    text = [x.decode(errors="replace") if isinstance(x, bytes) else x for x in exec_argv[:4]]
    if len(exec_argv) > 5 and text == ["python3", "-I", "-c", LAUNCH]:
        return list(exec_argv[5:])
    return list(exec_argv)


_SAMPLES: dict[int, tuple[object, dict[str, Call]]] = {}
"""id(the sample's store) -> (that store, its command calls by handle). The store is the one object every context of
a sample shares, the scorers' included, which a ContextVar set in the setup is not."""


def calls() -> dict[str, Call]:
    """The sample's command calls in memory: those still running and the jobs, by handle."""
    st = store()
    held = _SAMPLES.get(id(st))
    if held is None or held[0] is not st:
        held = _SAMPLES[id(st)] = (st, {})
    return held[1]


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
    the call's directory. The cursor moves to each stream's total."""
    spec = {"dir": call.dir, "keep": KEEP_BYTES, "wait_s": wait_s, "data": data, "remove": final,
            "from": {"out": 0, "err": 0} if final else call.cursor}
    r = await call.where.box.exec(["python3", "-I", "-c", READ], input=json.dumps(spec), user="root",
                                  timeout=INFRA_TIMEOUT_S)
    out, _, line = (r.stdout or "").rstrip("\n").rpartition("\n")
    try:
        head = json.loads(line)
    except ValueError:
        raise RuntimeError(f"command jobs: the read failed: {(r.stderr or r.stdout or '')[-500:]}") from None
    for name in ("out", "err"):
        if name in head:
            call.totals[name] = call.cursor[name] = head[name]["total"]
    return r.stderr or "", out, head


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
    handle = uuid.uuid4().hex[:16]
    spec = {"top": JOBS_DIR, "name": handle, "user": where.user, "env": where.env, "keep": KEEP_BYTES}
    with untracked_sandbox_execs():
        task = asyncio.ensure_future(where.box.exec(launch_argv(spec, argv), input=input, cwd=where.cwd, user="root",
                                                    concurrency=False))
    call = Call(handle=handle, agent=agent, tool=tool, where=where, command=command, started=now(), task=task)
    task.add_done_callback(_settled)
    task.add_done_callback(lambda _: setattr(call, "ended", call.ended or now()))
    calls()[handle] = call
    deadline = round_deadline(agent)
    wait = None if deadline is None else max(deadline - now(), LATE_START_S)
    done, _ = await asyncio.wait({task}, timeout=wait)
    if done:
        calls().pop(handle, None)
        result = task.result()
        if after is not None:
            await after(running_pid_files(where.service, but=handle))
        stderr, stdout, head = await read(call, wait_s=SETTLE_S, final=True)
        if not head.get("found"):       # the launch failed before its directory: the exec's own words
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
    """Kill the call's session (STOP) and wait for its exec to return; status names who stopped it."""
    if not call.task.done():
        spec = {"pid_files": [call.pid_file]}
        await call.where.box.exec(["python3", "-I", "-c", STOP], input=json.dumps(spec), user="root",
                                  timeout=INFRA_TIMEOUT_S)
        await asyncio.wait({call.task}, timeout=STOP_WAIT_S)
        call.status = status
        call.ended = call.ended or now()
    finished(call)


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
    """At the end of the run: kill every command call still running (a job's status "killed"), let each exec
    return, and write every job's record with its totals. The records, in order. Safe to call twice."""
    for call in list(calls().values()):
        if not call.task.done():
            try:
                await stop(call, "killed")
            except Exception:  # noqa: BLE001 - a machine already gone ends its jobs with it
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
