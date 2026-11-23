"""The run's tmp_snapshots.json, from the /tmp shots the run took itself (src/sandbox/live_tmp_capture.py), and run.json
"tmp_snapshots".

    block = write_tmp_snapshots(view, run_dir)      # after the run directory is written: transcripts, run.json

The file has the replay's layout (src/replay/tmp_snapshots.py, schema 2) with "clock": "run": steps holds every
recorded call in global order (recorded_calls: n, agent, k, call_id), and a call that took a shot carries "ran": true,
its machine, and before and after as changes. A step's after is what changed on its machine between the shot before
its own and its own; its before is what changed between the state the previous step of that machine (in n order) left
and that earlier shot, the whole state on the machine's first step. decode and state_at read it as they read a replay's.

The run's clock adds to each step that ran: "t" (its shot's time), "window" ([the machine's shot before, this shot]),
"ms" (the listing's time), "overlapping" (the n of every other call that took a shot on the machine and ran during the
window, by the gate's times of each call, its listing left out), "jobs" (the command jobs of another call that ran on
the machine during the window), "began_before_window" (the call started before the shot before its own, which may then
hold part of its change) and "attribution": "call" when nothing else ran on the machine in the window and the call ran
within it, so the change is the call's own and whole, "shared" otherwise, and "unknown" for a machine with no listing
before. A call whose shot failed has "ran": false and "snapshot_error". A call that marked more than one machine keeps
its first in machine, before and after, and the others' changes under "also" ({machine, after}).

run.json "tmp_snapshots": {"status": "written", "file", "shots", "ran", "errors", "ms": {median, p90, max}, "bytes"},
{"status": "error", "error"} when the file could not be built (nothing written), {"status": "off"} under
SwarmConfig.tmp_snapshots False, {"status": "none"} when the switch was on and the run took no listing (a domain with
no shell). A run whose config has no switch (before 2026-11-22) gets no block and its file, a replay's, stays.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

from src.export._export_run_blocks import cheatbench
from src.export.eval_sample_view import SampleView
from src.measures.run_directory import FILES, RunDir
from src.replay.recorded_calls import recorded_calls
from src.replay.step_snapshots import apply_delta, delta
from src.sandbox.live_tmp_capture import STORE
from src.sandbox.machines.agent_machines import DEFAULT_SERVICE
from src.sandbox.tmp_listing import HASH_MAX_BYTES, TEXT_MAX_BYTES, TMP_DIRS
from src.swarm.turn_rules import JOBS_STORE, TMP_CALLS_STORE

FILE = Path(FILES["tmp_snapshots.json"]).name
"""The name run.json's block records; the file sits at RunDir.file("tmp_snapshots.json")."""
SCHEMA = 2
MATCH_SLACK_S = 2.0     # a shot with no named call goes to its agent's call of that tool that started this near


def pair_shots(shots: list[dict], named: dict[str, str], calls: list) -> dict[str, list[dict]]:
    """{call id: its shots in seq order}: by the id turn_rules named, else by agent, tool and start time."""
    by_id = {call.id: call for call in calls}
    out: dict[str, list[dict]] = {}
    unnamed = []
    for shot in shots:
        cid = named.get(str(shot["seq"]))
        if cid in by_id:
            out.setdefault(cid, []).append(shot)
        else:
            unnamed.append(shot)
    for shot in unnamed:
        near = [c for c in calls if c.agent == shot["agent"] and c.tool == shot.get("tool") and shot.get("t0")
                and abs(c.t0 - shot["t0"]) <= MATCH_SLACK_S]
        if near:
            best = min(near, key=lambda c: abs(c.t0 - shot["t0"]))
            out.setdefault(best.id, []).append(shot)
    return out


def full_states(held: dict) -> tuple[dict[int, dict], dict[int, dict], dict[int, float | None]]:
    """({seq: the machine's state after the shot}, {seq: its state at the shot before}, {seq: that shot's time}) for
    every shot that worked. A machine with no first listing starts at its first shot, which then shows no change."""
    last = {m: init["state"] for m, init in (held.get("initial") or {}).items()}
    last_t = {m: init["t"] for m, init in (held.get("initial") or {}).items()}
    after, before, since = {}, {}, {}
    for shot in held.get("shots") or []:
        if "error" in shot:
            continue
        machine = shot["machine"]
        prev = last.get(machine)
        state = apply_delta(prev, shot["change"])
        after[shot["seq"]], before[shot["seq"]] = state, prev if prev is not None else state
        since[shot["seq"]] = last_t.get(machine)
        last[machine], last_t[machine] = state, shot["t"]
    return after, before, since


def job_rows(jobs: dict | None) -> list[dict]:
    return [{**job, "machine": job.get("machine") or DEFAULT_SERVICE} for job in (jobs or {}).values()]


def build(held: dict, calls: list, named: dict[str, str], jobs: dict | None, run: str, task: str | None) -> dict:
    """The file's dict (the module docstring)."""
    paired = pair_shots(held.get("shots") or [], named, calls)
    after, before, since = full_states(held)
    agents = held.get("agents") or {}
    # a call's own time is the gate's (the shot's t0 and t1): its tool event's end comes after its listing, and the
    # listings of one machine wait on each other
    times = {call.id: (call.t0, call.t1) for call in calls}
    on_machine: dict[str, list[tuple[int, str]]] = {}
    for call in calls:
        for shot in paired.get(call.id, []):
            if shot.get("t0") is not None and shot.get("t1") is not None:
                times[call.id] = (shot["t0"], shot["t1"])
            if shot["seq"] in after:
                on_machine.setdefault(shot["machine"], []).append((call.n, call.id))
    rows = job_rows(jobs)
    last: dict[str, dict] = {}
    steps = []
    for call in calls:
        step = {"n": call.n, "agent": call.agent, "k": call.k, "call_id": call.id, "tool": call.tool, "match": None,
                "machine": agents.get(call.agent, DEFAULT_SERVICE), "ran": False}
        mine = paired.get(call.id, [])
        good = [shot for shot in mine if shot["seq"] in after]
        if not good and mine:
            step["snapshot_error"] = mine[0].get("error")
        if good:
            shot = good[0]
            machine, seq = shot["machine"], shot["seq"]
            w0, w1 = since[seq], shot["t"]
            overlapping = [n for n, cid in on_machine.get(machine, []) if cid != call.id and w0 is not None
                           and times[cid][0] < w1 and times[cid][1] > w0]
            running = [job["id"] for job in rows if job["machine"] == machine and job.get("call_id") != call.id
                       and w0 is not None and (job.get("started") or 0) < w1
                       and (job.get("ended") is None or job["ended"] > w0)]
            early = w0 is not None and times[call.id][0] < w0
            step.update(machine=machine, ran=True, before=delta(last.get(machine), before[seq]),
                        after=delta(before[seq], after[seq]), t=w1, window=[w0, w1], ms=shot.get("ms"),
                        overlapping=overlapping, jobs=running, began_before_window=early,
                        attribution="unknown" if w0 is None else "shared" if overlapping or running or early
                        else "call")
            if shot.get("truncated"):
                step["truncated"] = True
            last[machine] = after[seq]
            if len(good) > 1:
                step["also"] = [{"machine": s["machine"], "after": delta(before[s["seq"]], after[s["seq"]])}
                                for s in good[1:]]
        steps.append(step)
    machines: dict[str, list[str]] = {m: [] for m in held.get("machines") or {}}
    for agent, machine in agents.items():
        machines.setdefault(machine, []).append(agent)
    for step in steps:
        if step["ran"] and step["agent"] not in machines.setdefault(step["machine"], []):
            machines[step["machine"]].append(step["agent"])
    shots = held.get("shots") or []
    ms = sorted(s["ms"] for s in shots if s.get("ms") is not None)
    return {"schema": SCHEMA, "run": run, "task": task, "dirs": held.get("dirs") or TMP_DIRS,
            "hash_max_bytes": HASH_MAX_BYTES, "text_max_bytes": TEXT_MAX_BYTES, "clock": "run",
            "machines": {m: sorted(a) for m, a in sorted(machines.items())},
            "users": {m: (held.get("users") or {}).get(m, {}) for m in sorted(machines)},
            "capture": {"shots": len(shots), "errors": held.get("error_count", 0),
                        "error_messages": held.get("errors") or [], "ms": ms_summary(ms),
                        "initial": {m: init["t"] for m, init in (held.get("initial") or {}).items()},
                        "truncated": sum(bool(s.get("truncated")) for s in shots),
                        "unread": held.get("no_read") or {}},
            "steps": steps}


def ms_summary(ms: list[float]) -> dict | None:
    if not ms:
        return None
    return {"median": round(statistics.median(ms), 1), "p90": round(ms[min(len(ms) - 1, int(0.9 * len(ms)))], 1),
            "max": round(ms[-1], 1)}


def write_tmp_snapshots(view: SampleView | None, run_dir: Path) -> dict | None:
    """Write run_dir/tmp_snapshots.json from the sample's shots and return run.json's block (the module docstring);
    None for a run whose config has no switch. Never raises: a failure is the block's error."""
    if view is None:
        return None
    config = cheatbench(view).get("config") or {}
    if "tmp_snapshots" not in config:
        return None
    if not config["tmp_snapshots"]:
        return {"status": "off"}
    held = view.store_get(STORE)
    if not held or not held.get("on"):
        return {"status": "none"}
    try:
        run = RunDir(run_dir)
        calls = recorded_calls(run)
        doc = build(held, calls, view.store_get(TMP_CALLS_STORE) or {}, view.store_get(JOBS_STORE),
                    run.path.name, run.run_json.get("task"))
        path = run.file("tmp_snapshots.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(doc, separators=(",", ":")))
        tmp.replace(path)
    except Exception as error:  # noqa: BLE001 - the snapshots never fail the export
        return {"status": "error", "error": f"{type(error).__name__}: {error}"[:500]}
    capture = doc["capture"]
    return {"status": "written", "file": FILE, "shots": capture["shots"], "ran": sum(s["ran"] for s in doc["steps"]),
            "errors": capture["errors"], "ms": capture["ms"], "bytes": path.stat().st_size}
