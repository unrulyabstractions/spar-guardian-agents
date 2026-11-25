"""tmp_snapshots.json: the temporary directories of every agent machine before and after every recorded call, read by
a replay (replay_run.replay) with no model call.

    report, doc = replay_tmp(run_dir, out_dir)         # the replay into out_dir, the snapshots as a dict
    states = decode(doc)                                # every step's before and after in full, by n
    state = state_at(doc, n=51)                         # {machine: {path: entry}} once call 51 has run

    python -m src.replay.tmp_snapshots <run dir> --out <replay dir> [--force] [--pem] [--warm-image]

The command replays the run into the replay dir (steps.jsonl, replay.json, end/ as `python -m src replay` writes them)
and writes <run dir>/tmp_snapshots.json only when the replay is faithful. It refuses to replace an existing file
without --force.

What a snapshot is. Every entry under TMP_DIRS (/tmp, /var/tmp, /dev/shm, the directories themselves included) of one
machine, read as root with lstat, links not followed:

    {path: {"type": "file" | "dir" | "link" | "fifo" | "socket" | "other", "size": int, "mode": "-rw-rw-r--",
            "owner": "s7m7z", "uid": 1001, "group": "team", "gid": 1000, "mtime": 1794000000.123,
            "acl": ["user::rw-", "user:s7m7z:r--", "group::---", "mask::r--", "other::---"],
            "default_acl": [...], "sha256": "<hex>", "text": "...", "target": "<link target>"}}

- sha256 is there for a regular file of at most HASH_MAX_BYTES (1 MiB), text for a regular file of at most
  TEXT_MAX_BYTES (8 KiB) that holds no NUL and decodes as UTF-8, target for a link. owner and group are the names the
  machine gives the uid and gid, or the id as text.
- acl is the POSIX access ACL as getfacl prints its lines (a named entry by the machine's name for its id), there only
  when the entry has one; default_acl the same for a directory's default ACL. The images have no getfacl: the listing
  reads the xattr (system.posix_acl_access) and decodes it. With an ACL, mode's group bits are the mask (as `ls -l`).
- mtime is the replay's clock, not the run's: the replay runs the calls again, so a file's time is when the replay
  wrote it. Compare times within one file only.
- A machine is a compose service of the replay: one per agent in software engineering and protein design (named by the
  agent), "default" in creative writing, where every agent shares one container.

The file:

    {"schema": 2, "run": "<run>", "task": "<task>", "dirs": TMP_DIRS, "hash_max_bytes": 1048576,
     "text_max_bytes": 8192, "clock": "replay", "machines": {"default": ["k5t9q", ...]},
     "users": {"default": {"s7m7z": {"uid": 1001, "gid": 1001, "groups": ["s7m7z", "team"]}, ...}},
     "replay": {"faithful": true, "calls": 122, "sandbox_calls": 39, "match": {...}, "code": {...}},
     "steps": [{"n": 51, "agent": "s7m7z", "k": 6, "call_id": "toolu_...", "tool": "shell", "match": "exact",
                "machine": "default", "ran": true, "before": {path: entry | null}, "after": {path: entry | null}},
               {"n": 52, ..., "ran": false}, ...]}

steps holds every recorded call in global order, with n, agent, k and call_id as steps.jsonl and the viewer number
them. A call that ran in a machine ("ran": true) carries two maps of changes: before, against the state its machine
was left in by the previous call that ran there (the whole state on the machine's first call), and after, against
before. A changed entry appears whole, a removed path as null (step_snapshots.delta). A call that ran in no machine (a
host tool: the board, submit without a kept file, oversight) changed nothing and carries no maps; the state at it is
the state after the last call that ran. decode and state_at rebuild the full state. users holds each machine's login
users (uid USER_MIN_UID up to nobody) with their groups by name, read as root before the machine's first call (USERS):
with an entry's owner, group, mode and acl it says who can read the entry. Schema 1 files have no group, gid, acl or
users. Pure, but for replay_tmp.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest import mock

from src.measures.run_directory import FILES, RunDir
from src.replay import replay_run
from src.replay.recorded_calls import RecordedCall
from src.replay.replay_report import STEPS
from src.replay.replay_sandbox import ReplaySandbox
from src.replay.step_snapshots import apply_delta, delta
from src.sandbox.machines.agent_machines import DEFAULT_SERVICE
from src.sandbox.tmp_listing import (  # noqa: F401 - the listing's names, read here and by the tests
    HASH_MAX_BYTES,
    LISTING,
    LISTING_LIB,
    NOBODY_UID,
    TEXT_MAX_BYTES,
    TMP_DIRS,
    USER_MIN_UID,
    USERS,
    listing_spec,
)

FILE = Path(FILES["tmp_snapshots.json"]).name
"""The file's name in the replay's own folder; a run holds it at RunDir.file("tmp_snapshots.json")."""
SCHEMA = 2
MEMORY_LIMIT = "4g"
"""The memory limit each container of the replay gets (compose mem_limit)."""
LIST_TIMEOUT_S = 120


def as_root(box: ReplaySandbox, machine: str, script: str, spec: dict, what: str) -> dict:
    """The JSON the script prints last, run as root in the machine with the spec on stdin."""
    done = box.run(machine, ["python3", "-I", "-c", script], stdin=json.dumps(spec).encode(), timeout=LIST_TIMEOUT_S,
                   user="root")
    if done.rc != 0:
        raise RuntimeError(f"the {what} failed in {machine} (rc={done.rc}): "
                           f"{(done.stderr or done.stdout).decode(errors='replace')[-800:]}")
    return json.loads(done.stdout.decode().strip().splitlines()[-1])


def listing(box: ReplaySandbox, machine: str) -> dict[str, dict]:
    """The snapshot of the machine's temporary directories (LISTING), as root."""
    return as_root(box, machine, LISTING, listing_spec(), "/tmp listing")


def users(box: ReplaySandbox, machine: str) -> dict[str, dict]:
    """The machine's login users and their groups (USERS), as root."""
    return as_root(box, machine, USERS, {"min_uid": USER_MIN_UID, "nobody_uid": NOBODY_UID}, "user listing")


class TmpRecorder:
    """A replayer (replay_run.REPLAYERS) that also lists its call's machine before execute and after after, by n, and
    the machine's users before its first call. Everything else is the wrapped replayer's; each container gets
    MEMORY_LIMIT."""

    def __init__(self, inner, calls: list[RecordedCall]) -> None:
        self.inner, self.calls = inner, calls
        self.service_names = set(inner.services())
        self.taken: dict[int, dict] = {}
        self.users: dict[str, dict] = {}

    def __getattr__(self, name: str):
        return getattr(self.inner, name)

    def machine(self, call: RecordedCall) -> str:
        """The compose service the call runs in: a peer_shell call's peer's and a job call's job's (replay_jobs.py),
        else the agent's own, else the shared one."""
        jobs = getattr(self.inner, "jobs", None)
        found = jobs.machine(call) if jobs is not None else None
        if found is not None:
            return found
        return call.agent if call.agent in self.service_names else DEFAULT_SERVICE

    def services(self) -> dict[str, dict]:
        return {name: {"mem_limit": MEMORY_LIMIT, **service} for name, service in self.inner.services().items()}

    def execute(self, box: ReplaySandbox, call: RecordedCall):
        machine = self.machine(call)
        if machine not in self.users:
            self.users[machine] = users(box, machine)
        self.taken[call.n] = {"machine": machine, "before": listing(box, machine)}
        return self.inner.execute(box, call)

    def after(self, box: ReplaySandbox, call: RecordedCall, output: str):
        result = self.inner.after(box, call, output)
        self.taken[call.n]["after"] = listing(box, self.taken[call.n]["machine"])
        return result


def encode(calls: list[RecordedCall], taken: dict[int, dict], machine_of, matches: dict[int, str] | None = None
           ) -> list[dict]:
    """The steps of the file from the calls in global order and their full snapshots by n (TmpRecorder.taken)."""
    last: dict[str, dict] = {}
    steps = []
    for call in calls:
        step = {"n": call.n, "agent": call.agent, "k": call.k, "call_id": call.id, "tool": call.tool,
                "match": (matches or {}).get(call.n), "machine": machine_of(call), "ran": call.n in taken}
        if step["ran"]:
            shot = taken[call.n]
            step["machine"] = shot["machine"]
            step["before"] = delta(last.get(shot["machine"]), shot["before"])
            step["after"] = delta(shot["before"], shot["after"])
            last[shot["machine"]] = shot["after"]
        steps.append(step)
    return steps


def decode(doc: dict) -> dict[int, dict]:
    """{n: {"machine", "before", "after"}} in full for each step that ran."""
    last: dict[str, dict] = {}
    out = {}
    for step in doc["steps"]:
        if not step["ran"]:
            continue
        before = apply_delta(last.get(step["machine"]), step["before"])
        after = apply_delta(before, step["after"])
        last[step["machine"]] = after
        out[step["n"]] = {"machine": step["machine"], "before": before, "after": after}
    return out


def state_at(doc: dict, n: int, *, before: bool = False) -> dict[str, dict]:
    """{machine: {path: entry}} once call n has run (before it, with `before`), each machine as the last call that ran
    in it left it. A machine no call has yet run in is absent."""
    last: dict[str, dict] = {}
    for step in doc["steps"]:
        if step["n"] > n or (before and step["n"] == n):
            break
        if step["ran"]:
            last[step["machine"]] = apply_delta(apply_delta(last.get(step["machine"]), step["before"]),
                                                step["after"])
    if before:
        step = next((s for s in doc["steps"] if s["n"] == n and s["ran"]), None)
        if step is not None:
            last[step["machine"]] = apply_delta(last.get(step["machine"]), step["before"])
    return last


def replay_tmp(run_dir: Path, out_dir: Path, *, pem: bool = False, warm_image: bool = False
               ) -> tuple[replay_run.ReplayReport, dict | None]:
    """replay_run.replay with every replayer wrapped in a TmpRecorder; the report, and the file's dict (None when the
    replay refused or failed). pem and warm_image are replay's opt-in remedies (SWE): the certificate swap
    (random_content.py) and the image with Go's build cache filled (warm_image.py). Writes nothing beyond what replay
    writes into out_dir."""
    made: list[TmpRecorder] = []

    def wrap(cls):
        def make(run, calls):
            made.append(TmpRecorder(cls(run, calls), calls))
            return made[-1]
        return make
    with mock.patch.dict(replay_run.REPLAYERS, {task: wrap(cls) for task, cls in replay_run.REPLAYERS.items()}):
        report = replay_run.replay(Path(run_dir), Path(out_dir), pem=pem, warm_image=warm_image)
    if report.refused or report.error or not made:
        return report, None
    recorder = made[0]
    if len(recorder.taken) != report.sandbox_calls or any("after" not in s for s in recorder.taken.values()):
        raise RuntimeError(f"{len(recorder.taken)} snapshots for {report.sandbox_calls} sandbox calls")
    steps_file = Path(out_dir) / STEPS
    matches = {line["n"]: line["match"] for line in map(json.loads, steps_file.read_text().splitlines())}
    machines: dict[str, list[str]] = {}
    for call in recorder.calls:
        machines.setdefault(recorder.machine(call), [])
        if call.agent not in machines[recorder.machine(call)]:
            machines[recorder.machine(call)].append(call.agent)
    doc = {"schema": SCHEMA, "run": report.run, "task": report.task, "dirs": TMP_DIRS,
           "hash_max_bytes": HASH_MAX_BYTES, "text_max_bytes": TEXT_MAX_BYTES, "clock": "replay",
           "machines": {m: sorted(agents) for m, agents in sorted(machines.items())},
           "users": {m: recorder.users.get(m, {}) for m in sorted(machines)},
           "replay": {"faithful": report.faithful, "calls": report.calls, "sandbox_calls": report.sandbox_calls,
                      "match": report.match, "code": report.code},
           "steps": encode(recorder.calls, recorder.taken, recorder.machine, matches)}
    return report, doc


def write(doc: dict, path: Path, *, force: bool = False) -> Path:
    """The file, written atomically; refuses to replace one without force."""
    if path.exists() and not force:
        raise FileExistsError(f"{path} exists; pass force to replace it")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":")))
    tmp.replace(path)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay a run and write its tmp_snapshots.json beside it.")
    parser.add_argument("run", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="the replay's own folder (steps.jsonl, replay.json)")
    parser.add_argument("--force", action="store_true", help="replace an existing tmp_snapshots.json")
    parser.add_argument("--pem", action="store_true",
                        help="swap the certificates and keys the run's calls drew for the recorded ones (SWE)")
    parser.add_argument("--warm-image", action="store_true",
                        help="run every machine on the run's image with Go's build cache filled (SWE)")
    args = parser.parse_args(argv)
    target = RunDir(args.run).file("tmp_snapshots.json")
    if target.exists() and not args.force:
        print(f"{target} exists; pass --force to replace it", file=sys.stderr)
        return 2
    report, doc = replay_tmp(args.run, args.out, pem=args.pem, warm_image=args.warm_image)
    if doc is None or not report.faithful:
        why = report.refused or (report.error or "").split("\n")[0] or "its end state differs"
        if doc is not None:
            write(doc, args.out / FILE, force=True)
        print(f"{report.run}: not faithful ({why}); nothing written beside the run", file=sys.stderr)
        return 1
    write(doc, args.out / FILE, force=True)
    write(doc, target, force=args.force)
    print(f"{target}: {sum(s['ran'] for s in doc['steps'])} of {len(doc['steps'])} steps ran in a machine; "
          f"{target.stat().st_size} bytes", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
