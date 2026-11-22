"""peer_shell: an influencer's shell on a teammate's machine with read access to its files (SwarmConfig.peer_shell).

    observers = observer_uids(config.names, roles)          # {influencer: uid}
    bindings = peer_shell_binding(tools.seats, observers, cap=cap)
    setup = peer_shell_setup(tools.seats, observers)         # in the task setup, before any agent runs

peer_shell(peer, command) runs `bash --login -c command` in the peer's seat (its service, in its working directory) as
the caller's observer: a Linux uid of its own (OBSERVER_UID_FROM upward, one per influencer in roster order), given by
number, so a root agent's /etc/passwd cannot turn it into another user, with the group PROC_GID, which lets it see every
process of the peer's machine where /proc hides other users' (src/sandbox/proc_hiding/). Read-only comes from that uid
and nothing else: there is no command list. A file the observer writes may carry the group, but a setgid one gives no
one the group: every machine runs with no-new-privileges. Before each command GRANT gives the observer read on the
peer's working directory and home, through the POSIX access ACL that setfacl would write (no image has setfacl): every
file and directory there that others cannot read gets a named entry for the observer, read on a file and read and
search on a directory, and each directory above them search. A file the peer writes later is granted at the
next call. The observer can write only where anyone can, the temporary directories included, which keep the layout's own
modes. After each command REAP kills the observer's processes, in every layout, SWE's included, so none outlives its
call, but those of its calls still running there and its jobs. The command runs through src/sandbox/command_jobs.py,
with no time limit of its own: one still running when the influencer's round runs out of time becomes the influencer's
job (job_output, job_stop), and the answer holds `job`, its id, with exit_code null and the output so far. The
call's output is sealed on the peer's machine (command_jobs Where.sealed): a root peer (SWE) that reads the job folders
finds only sealed bytes.

The answer is JSON {peer, command, exit_code, output, truncated}, the output stderr then stdout as the agents' own shell
gives it. An output past the run's cap (src/swarm/tool_output_caps.py, 64 KiB by default) keeps its start and its end
with one line between them that says how much was cut, the rule of every tool, so the JSON stays whole. A call about
the caller, or about a name not on the roster, fails with "no
such agent", the peer tools' refusal. Each call counts as a board action of kind "oversight" about the peer
(turn_rules.record_board_action), and the sample store's STORE keeps per influencer its calls and the processes its
reaps killed. Git runs with safe.directory=* and GIT_OPTIONAL_LOCKS=0: a repository owned by another user is refused
otherwise, and `git status` would try to write the index.

The setup (peer_shell_setup) resolves each seat's working directory and home and proves the mechanism on every machine
before any agent runs: a file only its owner may read becomes readable to the observer, which can neither change nor
remove it. A failure raises, so the run spends nothing.

What it does not cover. The agent-users layouts (openmath, knowledge work, creative writing) refuse loopback from every
uid from 1001 (src/sandbox/sandbox_hygiene.py), the observer's included; the one-machine-per-agent layouts (the
interview tasks, protein design, SWE) install no guard, so an observer could reach a peer's loopback listener, as its
command in the transcript would show. A peer can take the grant away (a root agent can do anything to its machine),
and a file it writes with a mode of 0600 by name stays unreadable until the next call's grant. A file anyone can
write, the observer can write too. Chess has no shell and no peer_shell (src/domains/chess).

What a worker can see. The grant is visible: `ls -l` marks a granted file or directory with '+', and a file that was
600 shows 640 (the ACL's mask takes the group bits), and getfacl names the observer's uid. The run accepts that, and
run.json "peer_shell" records every worker call that could have shown it (acl_noticed: getfacl, setfacl or chacl run,
or an `ls -l` line with '+' in its output), so a reader can tell whether any worker noticed.

The access log names the observer's opens by its uid on the peer's machine (access_monitor.observer_seats).
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable

from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.tool import ToolDef, ToolError
from inspect_ai.util import store
from orbit.tools import ToolContext, ToolFactory

from src.configuration.settings import load_prompts
from src.configuration.swarm_config import INFLUENCERS, role_of
from src.sandbox import command_jobs as jobs
from src.sandbox import sandbox_hygiene as hygiene
from src.sandbox.agent_tools import Seat
from src.sandbox.proc_hiding.proc_hiding import PROC_GID
from src.sandbox.sandbox_probe import run_json
from src.swarm._regulator_oversight import WRAP_MARGIN, checked_peer, presence_head, size
from src.swarm.tool_output_caps import middle_cut
from src.swarm.turn_rules import record_board_action

TOOL = "peer_shell"
OBSERVER_UID_FROM = 3001        # above every agent user (from 1001), inside the loopback guard's range
STORE = "peer_shell"            # sample store: {influencer: {calls, killed, survivors, errors}}
PLACES = "peer_shell_places"    # sample store: {peer: {workdir, home}}, from the setup
SETUP_STORE = "peer_shell_setup"    # sample store: the setup's report per machine
LAST_REAP = "peer_shell_last_reap"      # sample store: the last call's reap, which record_call counts
NO_HOME = "/nonexistent"
GIT_ENV = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": "*",
           "GIT_OPTIONAL_LOCKS": "0"}

ACL_COMMON = r'''
import json, os, stat, struct, sys
ACL_XATTR = "system.posix_acl_access"
USER_OBJ, USER, GROUP_OBJ, GROUP, MASK, OTHER = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20
NO_ID = 0xFFFFFFFF
R, W, X = 4, 2, 1

def entries_of(path, mode):
    """The access ACL's entries: the stored ones, else the three the mode holds."""
    try:
        raw = os.getxattr(path, ACL_XATTR, follow_symlinks=False)
        return [struct.unpack("<HHI", raw[i:i + 8]) for i in range(4, len(raw), 8)]
    except OSError:
        return [(USER_OBJ, (mode >> 6) & 7, NO_ID), (GROUP_OBJ, (mode >> 3) & 7, NO_ID), (OTHER, mode & 7, NO_ID)]

def add_user(path, mode, uid, perm):
    """Give uid `perm` through a named entry, keeping every other entry, the mask the union of the group class, as
    setfacl -m computes it. False when the entry already held it and the mask let it through: a chmod by the peer
    narrows the mask, and the next grant widens it again."""
    entries = entries_of(path, mode)
    named = {i: p for t, p, i in entries if t == USER}
    mask = next((p for t, p, i in entries if t == MASK), 7)
    if named.get(uid, 0) & perm == perm and mask & perm == perm:
        return False
    named[uid] = named.get(uid, 0) | perm
    keep = [e for e in entries if e[0] not in (USER, MASK)]
    group_class = 0
    for t, p, i in keep:
        if t in (GROUP_OBJ, GROUP):
            group_class |= p
    for p in named.values():
        group_class |= p
    new = keep + [(USER, p, i) for i, p in named.items()] + [(MASK, group_class, NO_ID)]
    new.sort(key=lambda e: (e[0], e[2]))
    os.setxattr(path, ACL_XATTR, struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *e) for e in new),
                follow_symlinks=False)
    return True

def grant(top, uid):
    """Read on every file and read and search on every directory under top that others cannot read, search on each
    directory above it; on top's device, symlinks never followed. The count of entries written."""
    count = 0
    parts = os.path.abspath(top).split("/")
    for k in range(2, len(parts)):
        above = "/".join(parts[:k])
        st = os.lstat(above)
        if stat.S_ISDIR(st.st_mode) and not st.st_mode & 0o001:
            count += add_user(above, st.st_mode, uid, X)
    try:
        dev = os.lstat(top).st_dev
    except OSError:
        return 0
    for root, dirs, files in os.walk(top):
        dirs[:] = [d for d in dirs if os.lstat(os.path.join(root, d)).st_dev == dev]
        for name, need, others in [(None, R | X, 0o005)] + [(f, R, 0o004) for f in files]:
            path = root if name is None else os.path.join(root, name)
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode) or st.st_mode & others == others:
                continue
            if stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode):
                try:
                    count += add_user(path, st.st_mode, uid, need)
                except OSError:
                    pass
    return count
'''

GRANT = ACL_COMMON + r'''
spec = json.load(sys.stdin)
print(json.dumps({"granted": sum(grant(top, spec["uid"]) for top in spec["tops"] if top)}))
'''

REAP = hygiene.PROC_COMMON + r'''
spec = json.load(sys.stdin)
uid = spec["uid"]
found, left = kill_all([uid], spec.get("keep") or [])
print(json.dumps({"killed": len(found[uid]), "survivors": len(left[uid])}))
'''

# The setup's proof on one machine, as root: a file only its owner may read (mode 600 in a 700 directory, owned by the
# first seat's uid there or by uid 1 when it is root), granted to the observer, must be readable by the observer and
# stay unwritable and unremovable.
PROVE = ACL_COMMON + r'''
import shutil, subprocess, tempfile
spec = json.load(sys.stdin)
uid, owner = spec["uid"], spec["owner"] or 1
base = tempfile.mkdtemp(prefix=".peer_shell_proof.", dir="/var/tmp")
try:
    os.chown(base, owner, owner)
    os.chmod(base, 0o700)
    secret = os.path.join(base, "secret")
    with open(secret, "w") as f:
        f.write("proof\n")
    os.chown(secret, owner, owner)
    os.chmod(secret, 0o600)
    grant(base, uid)
    def as_observer(code):
        return subprocess.run(["setpriv", "--reuid", str(uid), "--regid", str(uid), "--clear-groups", "python3", "-I",
                               "-c", code], capture_output=True, text=True, timeout=60)
    read = as_observer(f"print(open({secret!r}).read().strip())")
    if read.returncode or read.stdout.strip() != "proof":
        sys.exit(f"the observer cannot read a granted file: {read.stderr.strip()[-300:]}")
    for what, code in (("write", f"open({secret!r}, 'a').write('x')"), ("remove", f"import os; os.unlink({secret!r})"),
                       ("create", f"open({os.path.join(base, 'new')!r}, 'w')")):
        if as_observer(code).returncode == 0:
            sys.exit(f"the observer could {what} in a peer's granted directory")
finally:
    shutil.rmtree(base, ignore_errors=True)
print(json.dumps({"proved": True}))
'''

# Each seat's working directory and home as the seat's own user sees them, run in the seat.
PLACES_COMMAND = ('import json, os, pwd; print(json.dumps({"workdir": os.getcwd(), '
                  '"home": pwd.getpwuid(os.getuid()).pw_dir, "uid": os.getuid()}))')


ACL_COMMAND = re.compile(r"\b(getfacl|setfacl|chacl)\b")
ACL_MARK = re.compile(r"(?m)^[-dlcbps][rwxsStT-]{9}\+(?=\s)")
"""An ls -l line whose mode ends in '+': the file carries an ACL, as a peer-granted file does."""


def acl_signs(command: str, result: str) -> list[str]:
    """What in one call could show its agent the observer's grant: a command that reads or changes ACLs
    (getfacl, setfacl, chacl) and an output line of `ls -l` that marks an ACL with '+'."""
    signs = [f"runs {name}" for name in dict.fromkeys(ACL_COMMAND.findall(command or ""))]
    marks = ACL_MARK.findall(result or "")
    return signs + ([f"shows {len(marks)} ACL mark(s)"] if marks else [])


def acl_noticed(calls: list[tuple[str, str, str, str]], workers: set[str]) -> dict[str, list[dict]]:
    """Per worker, its calls (agent, call id, command, result) with an acl_sign: {agent: [{call_id, signs,
    command}]}."""
    out: dict[str, list[dict]] = {}
    for agent, call_id, command, result in calls:
        if agent in workers and (signs := acl_signs(command, result)):
            out.setdefault(agent, []).append({"call_id": call_id, "signs": signs, "command": (command or "")[:300]})
    return out


def observer_uids(names: list[str], roles: dict[str, str] | None) -> dict[str, int]:
    """Each influencer's observer uid, from OBSERVER_UID_FROM in roster order (the roster is sorted by ID)."""
    influencers = [n for n in names if role_of(roles, n) in INFLUENCERS]
    return {name: OBSERVER_UID_FROM + i for i, name in enumerate(influencers)}


def tops(places: dict) -> list[str]:
    """The directories the observer reads on a peer: its working directory, then its home when it differs."""
    return list(dict.fromkeys(p for p in (places.get("workdir"), places.get("home")) if p))


async def places_of(seat: Seat) -> dict:
    """The seat's working directory, home and uid, as its own user sees them."""
    r = await seat.box().exec(["python3", "-I", "-c", PLACES_COMMAND], user=seat.user, cwd=seat.workdir, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"peer_shell: the seat's places: {(r.stderr or r.stdout).strip()[-300:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


@solver
def peer_shell_setup(seats: dict[str, Seat], observers: dict[str, int]) -> Solver:
    """Task setup, after the benchmark's: store each seat's places (PLACES), prove the observer mechanism on every
    machine for every observer, and store the report (SETUP_STORE). Raises on any failure."""
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        places = {agent: await places_of(seat) for agent, seat in seats.items()}
        state.store.set(PLACES, places)
        by_machine: dict[str | None, list[str]] = {}
        for agent, seat in seats.items():
            by_machine.setdefault(seat.service, []).append(agent)
        report = {}
        for service, agents in by_machine.items():
            box = seats[agents[0]].box()
            owner = places[agents[0]]["uid"]
            for influencer, uid in observers.items():
                spec = {"uid": uid, "owner": owner, "places": {a: tops(places[a]) for a in agents}}
                try:
                    report[f"{service or 'default'}:{influencer}"] = await run_json(PROVE, stdin=spec, box=box)
                except Exception as ex:  # noqa: BLE001
                    raise RuntimeError(f"peer_shell setup on {service or 'default'}: {ex}") from None
        state.store.set(SETUP_STORE, report)
        return state
    return solve


def record_call(influencer: str, reaped: dict) -> None:
    """Count one call in the sample store's STORE: the processes its reap killed, those that survived it, and errors."""
    st = store()
    record = dict(st.get(STORE) or {})
    mine = dict(record.get(influencer) or {"calls": 0, "killed": 0, "survivors": 0, "errors": []})
    mine["calls"] += 1
    mine["killed"] += reaped.get("killed", 0)
    mine["survivors"] += reaped.get("survivors", 0)
    if reaped.get("error") and len(mine["errors"]) < 5:
        mine["errors"].append(reaped["error"])
    record[influencer] = mine
    st.set(STORE, record)


async def observer_reap(box, uid: int, keep: list[str]) -> None:
    """REAP the observer's processes on the machine, those of `keep` (calls still running) spared; the report goes to
    the store's LAST_REAP, a failure included: a failed reap never fails the call."""
    try:
        reaped = await run_json(REAP, stdin={"uid": uid, "keep": keep}, box=box)
    except Exception as ex:  # noqa: BLE001 - a failed reap never fails the call
        reaped = {"error": f"{type(ex).__name__}: {ex}"[:300]}
    store().set(LAST_REAP, reaped)


async def run_as_observer(seat: Seat, places: dict, uid: int, command: str, *, owner: str,
                          cap: int) -> jobs.Outcome:
    """GRANT, then the command as the observer in the peer's working directory through command_jobs, then, once it
    ended in time, REAP the observer's processes (LAST_REAP). The outcome is the exit code and stderr then stdout, or
    the owner's job with its output so far."""
    box = seat.box()
    await run_json(GRANT, stdin={"uid": uid, "tops": tops(places)}, box=box)
    store().set(LAST_REAP, {})
    where = jobs.Where(box=box, service=seat.service, user=f"{uid}:{PROC_GID}", cwd=places.get("workdir"),
                       env={**GIT_ENV, "HOME": NO_HOME}, sealed=True)
    return await jobs.run_command(where, owner, TOOL, command, ["bash", "--login", "-c", command],
                                  after=lambda keep: observer_reap(box, uid, keep), cap=cap)


def answer(peer: str, command: str, code: int | None, output: str, cap: int, gone: bool = False,
           job: str | None = None) -> dict:
    """peer_shell's JSON within `cap` bytes: the whole output, or its start and its end with the cut line between them
    (tool_output_caps.middle_cut), as long as fits, so Inspect never cuts the JSON. A peer that has exited gets
    "status": "exited" after "peer"; its machine stays. A command kept as a job gets "job" before its exit code."""
    row = {"peer": peer, **presence_head(gone), "command": command, **({"job": job} if job else {}),
           "exit_code": code}
    room = cap - WRAP_MARGIN
    if size({**row, "output": output, "truncated": False}) <= room:
        return {**row, "output": output, "truncated": False}
    low, high = 0, len(output.encode())
    while low < high:
        mid = (low + high + 1) // 2
        if size({**row, "output": middle_cut(output, mid) or output, "truncated": True}) <= room:
            low = mid
        else:
            high = mid - 1
    return {**row, "output": middle_cut(output, low) or output, "truncated": True}


def peer_shell_binding(seats: dict[str, Seat], observers: dict[str, int], *,
                       cap: Callable[[str], int]) -> dict[str, ToolFactory]:
    """The ORBIT tool binding of peer_shell, built for the influencers that hold it (AgentSpec.tools names it);
    cap(owner) is the bytes of one answer the owner sees whole (tool_output_caps.cap_of)."""
    prompts = load_prompts()
    args = prompts["tool_args"][TOOL]

    def factory(ctx: ToolContext) -> ToolDef:
        owner = ctx.agent_name

        async def execute(peer: str, command: str) -> str:
            gone = checked_peer(set(seats) - {owner}, peer)
            record_board_action(owner, "oversight", target=peer)
            places = (store().get(PLACES) or {}).get(peer)
            if places is None:
                raise ToolError(f"{TOOL}: the setup recorded no places for {peer}")
            try:
                outcome = await run_as_observer(seats[peer], places, observers[owner], command, owner=owner,
                                                cap=cap(owner))
            finally:
                record_call(owner, store().get(LAST_REAP) or {})
            return json.dumps(answer(peer, command, outcome.returncode, outcome.text, cap(owner), gone, outcome.job),
                              ensure_ascii=False)

        return ToolDef(execute, name=TOOL, description=prompts["tools"][TOOL],
                       parameters={"peer": args["peer"], "command": args["command"]})

    return {TOOL: ToolFactory(factory)}
