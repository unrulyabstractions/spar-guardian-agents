"""replay(run_dir, out_dir): a stored run played back in its sandbox from its recorded tool calls, with no model call
and no network, writing steps.jsonl (the state after every call) and replay.json (the report).

    report = replay(Path("runs/study_exploration_1/flipt_minimal_luna_fixed1"), Path("/tmp/steps/fixed1"))
    report.faithful        # the end state equals the stored one
    replay_runs([run_or_study, ...], Path("/tmp/steps"), only_missing=True)    # one folder per run under the out dir

The calls run in their recorded global order (recorded_calls.py). A call that may touch what the agents share (the team
remote) runs alone, after every earlier call; the others run in their agents' own machines beside each other, each
agent's in its own order, which changes no output since nothing else connects the machines. A task whose agents share
one machine says every call is shared. Host tools (the board, submit, stop_working, a regulator's oversight) change no
machine: their line carries the recorded output's place and the agent's state as it was. A task plays a host call
that did change a machine (creative writing's submit, which keeps writing.md). A replay that is refused, fails or ends
elsewhere than the stored end state is not faithful (replay_report.py).

Two shared calls of different agents may overlap in time, and their start times then do not say which touched the team
remote first. Where the record shows that a call read the remote before an overlapping earlier call wrote it
(git_state.read_before_write), the call plays against the remote's refs as they stood before that write, and the refs
the write made are put back after it (play_before). A replayer opts in with set_team_refs.

A replayer (REPLAYERS) gives the task's sandbox (services, volumes, image), its setup and calls (setup, initial,
in_sandbox, shared, command, execute, after, team_state), each line's state (line_state) and its end state
(end_state): swe_replayer.py, protein_replay.py, creative_replay.py.
"""
from __future__ import annotations

import json
import time
import traceback
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import NamedTuple

from src.configuration import settings
from src.measures.run_directory import RunDir
from src.measures.run_listing import list_collection
from src.replay.creative_replay import CreativeReplayer
from src.replay.git_state import changed_refs, read_before_write
from src.replay.output_match import cause_of, match_of
from src.replay.protein_replay import ProteinReplayer
from src.replay.recorded_calls import RecordedCall, ReplayRefused, recorded_calls
from src.replay.replay_report import REPORT, STEPS, ReplayReport, read_report, write_report
from src.replay.replay_sandbox import ReplaySandbox, project_name
from src.replay.step_snapshots import encode
from src.replay.swe_replayer import SweReplayer
from src.sandbox import staging
from src.swarm.tool_output_caps import INSPECT_CUT, middle_cut

REPLAYERS = {r.task: r for r in (SweReplayer, ProteinReplayer, CreativeReplayer)}
"""The tasks a replay can play back, by the task name run.json records."""
DEFAULT_TOOL_OUTPUT = 16 * 1024
"""Inspect's limit on a tool's output when the run set none (GenerateConfig.max_tool_output)."""
END = "end"
"""The folder of the replay's end state: the grader's diffs and end.json."""
SLOWEST = 10
"""The slowest calls the report lists."""
RECORDED = "recorded"
"""The match of a call replayed from its recorded output: a host tool, or a call whose arguments were refused."""


class OutputCut(NamedTuple):
    """How a run cut a long tool result: its cap in bytes, and whether in the middle with the cut stated
    (tool_output_caps.middle_cut, runs whose config records tool_output_cut) or by Inspect's own truncate_tool_output,
    its first and last halves under its note (runs before 2026-11-20)."""
    bytes: int
    middle: bool

    @classmethod
    def of(cls, config: dict) -> OutputCut:
        return cls(config.get("tool_output_bytes") or DEFAULT_TOOL_OUTPUT, config.get("tool_output_cut") == "middle")


def tool_output(tool: str, output: str, cut: OutputCut) -> str:
    """The output as the model saw it, cut as its run cut it."""
    if cut.middle:
        return middle_cut(output, cut.bytes) or output
    truncated = INSPECT_CUT(tool, output, cut.bytes)
    return truncated.output if truncated else output


def play_call(box, replayer, call: RecordedCall, cut: OutputCut, swap=None) -> dict:
    """One call in its machine: its output against the recorded one, the agent's state after it and, where it may
    have touched the team remote, the team's. With `swap` (random_content.CertificateSwap) the certificates and keys
    the call drew give way to the recorded ones before its commits are dated."""
    started = time.time()
    output, timed_out = replayer.execute(box, call)
    ran = time.time() - started
    swapped = None
    if swap is not None:
        output, swapped = swap.apply(box, call.agent, call.result, output, replayer.command(call))
    output, state, dated, undated = replayer.after(box, call, output)
    team = replayer.team_state(box) if replayer.shared(call) else None
    if call.error == "timeout" or timed_out:
        match = "exact" if call.error == "timeout" and timed_out else "differs"
    else:
        match = match_of(call.result, tool_output(call.tool, output, cut))
    cause = cause_of(call.result, output, replayer.command(call)) if match == "differs" else None
    return {"call": call, "match": match, "cause": cause, "state": state, "team": team, "dated": dated,
            "undated": undated, "swapped": swapped, "ran": ran, "snapshot": time.time() - started - ran}


def play_before(box, replayer, call: RecordedCall, cut: OutputCut, swap, write: tuple, refs: dict[str, str]) -> dict:
    """play_call with the team remote's refs set back to how they stood before `write` (the writing call and the refs
    it changed), then put back to `refs`, the refs the write left. The result's team is the remote's once put back,
    and read_before names the write. A call that changes the refs meanwhile is a RuntimeError."""
    writer, changed = write
    before = {name: sha for name, sha in refs.items() if name not in changed}
    before.update({name: old for name, (old, _) in changed.items() if old is not None})
    replayer.set_team_refs(box, before)
    try:
        result = play_call(box, replayer, call, cut, swap)
    finally:
        replayer.set_team_refs(box, refs)
    if result["team"]["refs"] != before:
        raise RuntimeError(f"n{call.n} changed the team remote while it stood before n{writer.n}'s write")
    result["team"] = replayer.team_state(box)
    result["read_before"] = {"writer": writer.n, "refs": {name: list(pair) for name, pair in changed.items()}}
    return result


def play(box, replayer, calls: list[RecordedCall], cut: OutputCut, parallel: bool, swap=None,
         team0: dict | None = None) -> dict[int, dict]:
    """Every sandbox call, each as play_call, by n. A shared call (or every call without parallel) waits for all
    earlier ones and runs alone. With a replayer's set_team_refs, a shared call that the record shows read the team
    remote before the last write to it (git_state.read_before_write) plays as play_before. `team0` is the remote's
    state before the first call."""
    lanes = {agent: ThreadPoolExecutor(max_workers=1) for agent in replayer.agents}
    pending: list[Future] = []
    done: dict[int, dict] = {}
    rewinds = getattr(replayer, "set_team_refs", None) is not None
    refs = dict((team0 or {}).get("refs") or {})   # the team remote's refs as the last shared call left them
    write = None                                    # the last shared call that changed them, and the refs it changed
    try:
        for call in calls:
            if not replayer.in_sandbox(call):
                continue
            if not parallel or replayer.shared(call):
                wait(pending)
                done.update((f.result()["call"].n, f.result()) for f in pending)
                pending = []
                if rewinds and write and read_before_write(call, replayer.command(call), *write):
                    done[call.n] = play_before(box, replayer, call, cut, swap, write, refs)
                    continue
                done[call.n] = play_call(box, replayer, call, cut, swap)
                if rewinds and done[call.n]["team"] is not None:
                    changed = changed_refs(refs, done[call.n]["team"]["refs"])
                    if changed:
                        write, refs = (call, changed), dict(done[call.n]["team"]["refs"])
            else:
                pending.append(lanes[call.agent].submit(play_call, box, replayer, call, cut, swap))
        wait(pending)
        done.update((f.result()["call"].n, f.result()) for f in pending)
    finally:
        for lane in lanes.values():
            lane.shutdown(wait=True, cancel_futures=True)
    return done


def step_rows(replayer, calls: list[RecordedCall], played: dict[int, dict], initial: dict[str, dict],
              team0: dict) -> list[dict]:
    """One row per call in global order with its state in full, as the task's line_state builds it from the call's own
    state (a played call's, else None), the agent's state as it stands (the played call's, else as its last played
    call left it) and the shared state (team_state's, as the last shared call left it)."""
    agent_state, team, rows = dict(initial), team0, []
    first = calls[0].t0 if calls else 0.0
    for call in calls:
        result = played.get(call.n)
        if result is not None:
            agent_state[call.agent] = result["state"]
            team = result["team"] or team
        rows.append({"n": call.n, "agent": call.agent, "k": call.k, "call_id": call.id,
                     "t": round(call.t0 - first, 3), "tool": call.tool,
                     "match": result["match"] if result else RECORDED,
                     "state": replayer.line_state(call, result["state"] if result else None, agent_state[call.agent],
                                                  team)})
    return rows


def fill_report(report: ReplayReport, calls: list[RecordedCall], played: dict[int, dict], replayer) -> None:
    report.calls = len(calls)
    report.sandbox_calls = len(played)
    report.not_run = sum(1 for c in calls if c.tool in ("shell", "run_python") and not replayer.in_sandbox(c))
    report.host = len(calls) - len(played) - report.not_run
    report.match = dict(Counter(r["match"] for r in played.values()))
    report.causes = dict(Counter(r["cause"] for r in played.values() if r["cause"]))
    report.mismatches = [{"n": n, "agent": r["call"].agent, "k": r["call"].k, "match": r["match"], "cause": r["cause"],
                          "command": replayer.command(r["call"])[:200]}
                         for n, r in sorted(played.items()) if r["match"] in ("order", "differs")]
    report.dated = [d for _, r in sorted(played.items()) for d in r["dated"]]
    report.undated = [d for _, r in sorted(played.items()) for d in r["undated"]]
    report.swaps = [{"n": n, "agent": r["call"].agent, "k": r["call"].k, **r["swapped"]}
                    for n, r in sorted(played.items()) if r.get("swapped")]
    report.read_before = [{"n": n, "agent": r["call"].agent, "k": r["call"].k, **r["read_before"]}
                          for n, r in sorted(played.items()) if r.get("read_before")]
    report.slowest = [{"n": n, "agent": r["call"].agent, "seconds": round(r["ran"], 1),
                       "recorded_seconds": round(r["call"].t1 - r["call"].t0, 1),
                       "command": replayer.command(r["call"])[:120]}
                      for n, r in sorted(played.items(), key=lambda item: -item[1]["ran"])[:SLOWEST]]
    report.shared = sum(1 for r in played.values() if replayer.shared(r["call"]))
    report.seconds["calls_in_series"] = round(sum(r["ran"] for r in played.values()), 1)
    report.seconds["snapshots_in_series"] = round(sum(r["snapshot"] for r in played.values()), 1)


def replay(run_dir: Path, out_dir: Path, *, parallel: bool = True, pem: bool = False,
           warm_image: bool = False) -> ReplayReport:
    """Play the run back and write out_dir/steps.jsonl and out_dir/replay.json. Never raises for the run itself: a
    refusal or a failure is the report's `refused` or `error`, and the report is not faithful. Opt-in remedies: `pem`
    swaps the certificates and keys a call draws for the recorded ones (random_content.py), `warm_image` runs every
    machine on the run's image with Go's build cache filled (warm_image.py); replay.json's options name them."""
    run = RunDir(Path(run_dir))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = ReplayReport(run=run.name, run_dir=str(run.path), faithful=False, parallel=parallel,
                          options={"pem": pem, "warm_image": warm_image}, code=settings.code_state())
    wall = time.time()
    try:
        run.require()
        report.task = run.run_json.get("task")
        if report.task not in REPLAYERS:
            raise ReplayRefused(f"no replay for task {report.task!r}; it replays {', '.join(REPLAYERS)}")
        calls = recorded_calls(run)
        replayer = REPLAYERS[report.task](run, calls)
        for wanted, hook in ((pem, "certificate_swap"), (warm_image, "use_warm_image")):
            if wanted and not hasattr(replayer, hook):
                raise ReplayRefused(f"the {report.task} replay has no {hook}")
        swap = replayer.certificate_swap() if pem else None
        if warm_image:
            replayer.use_warm_image()
        report.image = replayer.image()
        if not report.image["id"]:
            raise ReplayRefused(f"the image {report.image['tag']} is not on this host")
        cut = OutputCut.of(run.run_json.get("config") or {})
        compose = staging.write_compose(out_dir / "compose.yaml", replayer.services(), replayer.volumes())
        mark = time.time()
        with ReplaySandbox(project_name(run.name), compose) as box:
            report.seconds["compose_up"] = round(time.time() - mark, 1)
            mark = time.time()
            report.setup = replayer.setup(box)
            initial, team0 = replayer.initial(box), replayer.team_state(box)
            report.seconds["setup"] = round(time.time() - mark, 1)
            mark = time.time()
            played = play(box, replayer, calls, cut, parallel, swap, team0)
            report.seconds["calls"] = round(time.time() - mark, 1)
            mark = time.time()
            report.end = replayer.end_state(box, out_dir / END)
            report.seconds["end_state"] = round(time.time() - mark, 1)
            mark = time.time()
        report.seconds["compose_down"] = round(time.time() - mark, 1)
        fill_report(report, calls, played, replayer)
        lines = encode(step_rows(replayer, calls, played, initial, team0))
        tmp = out_dir / (STEPS + ".tmp")
        tmp.write_text("".join(json.dumps(line) + "\n" for line in lines))
        tmp.replace(out_dir / STEPS)
        report.faithful = bool(report.end.get("equal"))
    except ReplayRefused as refused:
        report.refused = str(refused)
    except Exception as error:  # noqa: BLE001 - the report carries any failure; the next run still plays
        report.error = f"{type(error).__name__}: {error}\n{traceback.format_exc()[-3000:]}"
    report.wall_seconds = round(time.time() - wall, 1)
    write_report(out_dir, report)
    return report


def runs_of(paths: list[Path]) -> list[Path]:
    """The runs given, a study folder standing for its valid runs of a task REPLAYERS plays back. Each run once."""
    out: list[Path] = []
    for path in map(Path, paths):
        found = [path] if RunDir(path).is_run() else [
            r.path for r in list_collection(path) if r.status == "valid" and RunDir(r.path).run_json.get("task")
            in REPLAYERS]
        if not found:
            raise ValueError(f"neither a run nor a study with valid runs to replay: {path}")
        out += [p for p in found if p.resolve() not in {o.resolve() for o in out}]
    return out


def replay_runs(paths: list[Path], out: Path, *, only_missing: bool = False, parallel: bool = True, jobs: int = 1,
                pem: bool = False, warm_image: bool = False, log=print) -> int:
    """Each run of runs_of(paths) into out/<run>/, `jobs` runs at once (each its own compose project), with replay's
    opt-in remedies; with only_missing a run whose replay.json exists is skipped. Returns 0 when every replay played was
    faithful, 1 otherwise."""
    todo = []
    for path in runs_of(paths):
        if only_missing and read_report(Path(out) / path.name) is not None:
            log(f"{path.name}: {REPORT} exists, skipped", flush=True)
        else:
            todo.append(path)

    def one(path: Path) -> bool:
        report = replay(path, Path(out) / path.name, parallel=parallel, pem=pem, warm_image=warm_image)
        shares = ", ".join(f"{k} {v:.1%}" for k, v in sorted(report.shares().items()))
        why = report.refused or (report.error or "").split("\n")[0] or "its end state differs"
        verdict = "faithful" if report.faithful else f"NOT faithful: {why}"
        log(f"{path.name}: {verdict}; {report.sandbox_calls} sandbox calls ({shares}); {report.wall_seconds:.0f} s",
            flush=True)
        return report.faithful

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        faithful = list(pool.map(one, todo))
    return 0 if all(faithful) else 1
