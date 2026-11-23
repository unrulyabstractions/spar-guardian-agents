"""The run's own /tmp snapshots: every agent machine's listed folders (LIVE_DIRS: the temporary directories, the
folder a team shares and /var/backups) listed as root before round 1 and after every tool call that ran a command
there, while the run goes on. The export turns them into the run's
tmp_snapshots.json (src/export/_export_tmp_snapshots.py), the file a replay writes for an older run
(src/replay/tmp_snapshots.py).

    setup = [..., tmp_capture_setup(machines_of(tools.seats, agents))]          # src/tasks.py, after the access log's
    turn_rules.AFTER_CALL                                                       # holds after_call, registered here

When. tmp_capture_setup lists each machine and its login users once the setup is done (the seed pack's files planted,
the access log's marks set) and before the first agent's turn. A command tool marks the machine its call ran on
(turn_rules.mark_machine, src/sandbox/command_jobs.py: shell, run_python, a lone agent's bash and python, peer_shell on
the peer's machine, job_output and job_stop on the job's), and after_call lists that machine once the call has ended,
before its answer reaches the agent. A call of a host tool (the board, submit, stop_working) touches no machine and
takes no listing.

How. One exec as root of tmp_listing.LISTING, its output one line per entry (an Inspect SandboxEvent keeps 20 lines of
an exec's output, so the eval log keeps no copy of a listing), outside the sample's working time
(untracked_sandbox_execs). The agents see none of it but the process, which hidepid hides from every agent that is not
root. The access log's honeypot files and every file in their directories are listed by lstat alone, their content
never opened (tmp_listing's no_read): the harness's open of one would be logged as a harness read. A walk stops at
MAX_ENTRIES entries and says truncated.

What is kept. The sample store's STORE key: {"on": true, "dirs", "machines": {machine: service}, "agents": {agent:
machine}, "no_read": {machine: [paths]}, "users": {machine: {user: {uid, gid, groups}}}, "initial": {machine: {"t",
"state", "truncated"}}, "shots": [shot], "errors": [the first MAX_ERRORS], "error_count"}. A shot is {"seq", "machine",
"agent", "tool", "t0", "t1" (the call's start and end), "t" (the listing's start), "ms" (the listing's time), "change"
(tmp_listing entries that differ from the machine's previous shot, a removed path as null), "truncated"}, or the same
with "error" in place of change when the listing failed. seq counts from 1 in the order the shots were taken; a
machine's shots are taken one at a time (a lock per machine), so each change is against the one before it.
TMP_CALLS_STORE names each shot's tool call by seq, once the call's batch is settled (turn_rules._settle).

What it cannot say. Agents run beside each other within a round, and a job runs on after its call returned. A change
between two shots of one machine comes from whatever ran there in between: the export names the other calls and jobs
that overlap that window and claims the call alone only where none did. A failure never reaches the run: a failed
listing is a shot with an error, and the next shot of that machine is against the last one that worked.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

from inspect_ai._util.working import untracked_sandbox_execs  # no public way to keep a harness exec off the clock
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox, store

from src.sandbox.access_log.access_monitor import STORE as ACCESS_STORE
from src.sandbox.agent_tools import Seat
from src.sandbox.machines.agent_machines import DEFAULT_SERVICE
from src.sandbox.tmp_listing import (
    LISTING,
    LIVE_DIRS,
    NOBODY_UID,
    TMP_DIRS,
    USER_MIN_UID,
    USERS,
    listing_spec,
    parse_lines,
)
from src.swarm import turn_rules as rules

STORE = "tmp_capture"
MAX_ENTRIES = 20_000        # a walk past this many entries stops and says truncated
LIST_TIMEOUT_S = 60
MAX_ERRORS = 5


@dataclass
class Memory:
    """One sample's last full listing per machine, and a lock per machine so its shots are taken one at a time."""
    store: object
    last: dict[str, dict] = field(default_factory=dict)
    locks: dict[str, asyncio.Lock] = field(default_factory=dict)


_SAMPLES: dict[int, Memory] = {}
"""id(the sample's store) -> its Memory, as command_jobs keeps its own."""


def memory() -> Memory:
    st = store()
    held = _SAMPLES.get(id(st))
    if held is None or held.store is not st:
        held = _SAMPLES[id(st)] = Memory(st)
    return held


def now() -> float:
    return time.time()


def machine_name(service: str | None) -> str:
    """A machine's name in the file: its compose service, DEFAULT_SERVICE for the sample's default."""
    return service or DEFAULT_SERVICE


def machines_of(seats: dict[str, Seat] | None, agents: list[str]) -> tuple[dict[str, str | None], dict[str, str]]:
    """({machine: service}, {agent: machine}) of a roster: each seat's service, or the default machine for every agent
    of a layout without seats (a lone agent)."""
    if not seats:
        return {DEFAULT_SERVICE: None}, {agent: DEFAULT_SERVICE for agent in agents}
    machines = {machine_name(seat.service): seat.service for seat in seats.values()}
    return machines, {agent: machine_name(seat.service) for agent, seat in seats.items()}


def watched_files(access: dict | None) -> dict[str, list[str]]:
    """{machine: [path]}: the files the access log marks (its loose file objects), whose content the listing never
    opens, nor that of any file beside them."""
    out: dict[str, list[str]] = {}
    for obj in (access or {}).get("objects") or []:
        if obj.get("kind", "file") == "file" and not obj.get("packed"):
            out.setdefault(obj["machine"], []).append(obj["path"])
    return out


def spec_for(held: dict, machine: str) -> dict:
    paths = held.get("no_read", {}).get(machine) or []
    dirs = sorted({path.rsplit("/", 1)[0] or "/" for path in paths})
    return listing_spec(held.get("dirs") or TMP_DIRS, no_read=paths, no_read_dirs=dirs, lines=True,
                        max_entries=MAX_ENTRIES)


async def exec_root(box, script: str, spec: dict):
    with untracked_sandbox_execs():
        return await box.exec(["python3", "-I", "-c", script], input=json.dumps(spec), user="root",
                              timeout=LIST_TIMEOUT_S, concurrency=False)


async def take_listing(box, spec: dict) -> tuple[dict[str, dict], bool]:
    """The machine's listing as root: ({path: entry}, truncated). A failed exec is a RuntimeError."""
    r = await exec_root(box, LISTING, spec)
    if r.returncode != 0:
        raise RuntimeError(f"the /tmp listing failed (rc={r.returncode}): {(r.stderr or r.stdout or '')[-300:]}")
    return parse_lines(r.stdout or "")


async def take_users(box) -> dict[str, dict]:
    r = await exec_root(box, USERS, {"min_uid": USER_MIN_UID, "nobody_uid": NOBODY_UID})
    if r.returncode != 0:
        raise RuntimeError(f"the user listing failed (rc={r.returncode}): {(r.stderr or r.stdout or '')[-300:]}")
    return json.loads((r.stdout or "").strip().splitlines()[-1])


def change_of(previous: dict | None, current: dict) -> dict:
    """The entries of current that differ from previous, a removed path as None; the whole map with no previous."""
    if previous is None:
        return dict(current)
    out = {path: entry for path, entry in current.items() if previous.get(path) != entry}
    out.update({path: None for path in previous if path not in current})
    return out


def note_error(held: dict, message: str) -> None:
    held["error_count"] = held.get("error_count", 0) + 1
    if len(held.setdefault("errors", [])) < MAX_ERRORS:
        held["errors"].append(message[:500])


def box_of(service: str | None):
    return sandbox(service) if service else sandbox()


def tmp_capture_setup(machines: dict[str, str | None], agents: dict[str, str]) -> Solver:
    """The setup solver: switch the capture on and take each machine's users and first listing (STORE). A failure is
    recorded; the run goes on, and that machine's first shot has no listing before it."""

    @solver("tmp_capture_setup")
    def make() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            st = store()
            held = {"on": True, "dirs": LIVE_DIRS, "machines": dict(machines), "agents": dict(agents),
                    "no_read": watched_files(st.get(ACCESS_STORE)), "users": {}, "initial": {}, "shots": [],
                    "errors": [], "error_count": 0}
            mem = memory()
            for machine, service in machines.items():
                box = box_of(service)
                try:
                    held["users"][machine] = await take_users(box)
                    t = now()
                    state_, truncated = await take_listing(box, spec_for(held, machine))
                except Exception as error:  # noqa: BLE001 - the capture never fails the run
                    note_error(held, f"setup {machine}: {type(error).__name__}: {error}")
                    continue
                held["initial"][machine] = {"t": round(t, 3), "state": state_, "truncated": truncated}
                mem.last[machine] = state_
            st.set(STORE, held)
            return state

        return solve

    return make()


async def after_call(agent: str, run: dict) -> None:
    """AFTER_CALL: one shot of each machine the call marked, its seq added to run["tmp"]. Never raises."""
    st = store()
    held = st.get(STORE)
    if not held or not held.get("on"):
        return
    mem = memory()
    for service, box in list((run.get("machines") or {}).items()):
        machine = machine_name(service)
        lock = mem.locks.setdefault(machine, asyncio.Lock())
        async with lock:
            shot = {"seq": len(held["shots"]) + 1, "machine": machine, "agent": agent, "tool": run.get("tool"),
                    "t0": run.get("t0"), "t1": run.get("t1"), "t": round(now(), 3)}
            try:
                current, truncated = await take_listing(box, spec_for(held, machine))
                shot["ms"] = round((now() - shot["t"]) * 1000, 1)
                shot["change"] = change_of(mem.last.get(machine), current)
                shot["truncated"] = truncated
                mem.last[machine] = current
            except Exception as error:  # noqa: BLE001 - the capture never fails the run
                shot["error"] = f"{type(error).__name__}: {error}"[:500]
                note_error(held, f"shot {shot['seq']} {machine}: {shot['error']}")
            shot["seq"] = len(held["shots"]) + 1
            held["shots"].append(shot)
            st.set(STORE, held)
        run.setdefault("tmp", []).append(shot["seq"])


if after_call not in rules.AFTER_CALL:
    rules.AFTER_CALL.append(after_call)
