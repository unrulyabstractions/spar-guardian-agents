"""Docker smoke of the replay (src/replay/) on a tiny synthetic SWE run. No model, no network, no key.

    uv run python -m tests.smoke.replay_swe [--out DIR]
    make smoke-replay

`record` builds the run: two agents on flipt-02e2 under jigsaw (a01 holds B, a02 holds C), set up by the replay's own
setup in a real sandbox, then a script of eleven calls run there as Inspect runs the agents' tools, host tools among
them: a01 cherry-picks its planted B and pushes it to team/main; a02 commits a note, pulls team/main into a merge and
pushes; a01 fast-forwards and leaves its tree dirty; a02 submits. The calls, their outputs and times, the setup's refs
and planted commits, and the grader's reading of the end state become a run directory with an eval log, transcripts
and swe/diffs, as the export writes them. The sandbox is torn down.

The scenarios then replay it in a fresh sandbox, at a later clock:
- faithful: the end state equals the record, all three commits get their recorded ids by their dates (the
  cherry-pick moves its committer's date only, the note and the merge both dates), no output differs, the host tools
  are replayed from the record, and steps.jsonl holds each piece where the script put it.
- unfaithful: the same run with one byte of a stored diff changed is not faithful, and faithful_steps refuses it.
- refused: the same run with another planted commit recorded is refused at the setup.

Everything goes under --out (default build/replay_smoke/<stamp>/), never under runs/ or logs/. The suite is not part of
smoke-all (tests/smoke/all_suites.py NOT_SUITES): it plays back a stored run rather than running a task.
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from inspect_ai.event import ToolEvent
from inspect_ai.log import EvalDataset, EvalLog, EvalSample, EvalSpec, write_eval_log

from src.configuration import settings
from src.domains.swe import swe_replay as hooks
from src.domains.swe.swe_inputs import fix_commit, load_split, run_values, task_config
from src.domains.swe.swe_task import piece_times
from src.measures.run_directory import RunDir
from src.replay.recorded_calls import RecordedCall
from src.replay.replay_report import STEPS, NotFaithful, faithful_steps
from src.replay.replay_run import replay
from src.replay.replay_sandbox import ReplaySandbox, project_name
from src.replay.swe_replayer import SweReplayer
from src.sandbox import staging
from src.sandbox.docker_images import image_id
from src.utils import progress
from tests.smoke import invocation_stamp
from tests.support.run_builder import write_run_dir
from tests.support.run_files import write_text

ISSUE = "flipt-02e2"
AGENTS = ["a01", "a02"]
PIECES = {"a01": ["B"], "a02": ["C"]}


def synthetic_swe() -> dict:
    """The run.json swe block a run of two agents under jigsaw would record before its setup."""
    instance = load_split(ISSUE)["task"]
    tag = hooks.image({"issue": ISSUE})
    return {"issue": ISSUE, "instance": instance, "base_commit": task_config(instance)["base_commit"],
            "fix_commit": fix_commit(instance), "honeypot": "jigsaw", "seed": 1, "n_agents": 2, "agents": AGENTS,
            "mode": "shared_task", "pieces": PIECES, "piece_times": piece_times(1, ISSUE),
            "own_values": run_values(ISSUE, "jigsaw"), "images": {"native": tag, "native_id": image_id(tag)}}


def script(swe: dict) -> list[tuple[str, str, dict, str]]:
    """(agent, tool, arguments, a host tool's output) per call, in order."""
    b_ref = hooks.piece_refs(swe)["B"]
    return [("a01", "shell", {"command": "git status --short --branch"}, ""),
            ("a02", "shell", {"command": "git log --oneline -1"}, ""),
            ("a01", "shell", {"command": f"git cherry-pick {b_ref} && git log --oneline -2"}, ""),
            ("a01", "board_post", {"message": "B is on team/main"}, "Posted."),
            ("a01", "shell", {"command": "git push -q team HEAD:main && git log --oneline -1 team/main"}, ""),
            ("a02", "shell", {"command": "echo notes > NOTES.md && git add NOTES.md && git commit -qm notes && "
                                         "git log --oneline -1"}, ""),
            ("a02", "shell", {"command": "git pull -q --no-rebase --no-edit team main && git log --oneline -3"}, ""),
            ("a02", "shell", {"command": "git push -q team HEAD:main && git rev-parse --short team/main"}, ""),
            ("a01", "shell", {"command": "git fetch -q team && git merge -q --ff-only team/main && "
                                         "echo dirty >> README.md && git status --short"}, ""),
            ("a02", "submit", {"answer": "team/main"}, "Submitted."),
            ("a01", "run_python", {"code": "print(open('README.md').read()[-6:])"}, "")]


def moment(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, timezone.utc)


def record(folder: Path) -> Path:
    """Run the script in a real sandbox and write it as a run directory at folder/run."""
    swe = synthetic_swe()
    run_json = {"task": SweReplayer.task, "run_id": "replay_smoke", "config": {}, "swe": swe}
    draft = folder / "draft"
    draft.mkdir(parents=True)
    write_text(draft / "processed/run.json", json.dumps(run_json))
    replayer = SweReplayer(RunDir(draft), [])
    compose = staging.write_compose(folder / "record-compose.yaml", replayer.services(), {"team": {}})
    events, steps = [], {a: [] for a in AGENTS}
    with ReplaySandbox(project_name("replay_smoke_record"), compose) as box:
        built = replayer.build(box)
        for i, (agent, tool, arguments, host_output) in enumerate(script(swe)):
            cid = f"call_{i:02d}_{agent}"
            t0 = time.time()
            if tool in ("shell", "run_python"):
                output, _ = replayer.execute(box, RecordedCall(i + 1, agent, len(steps[agent]), cid, tool, arguments,
                                                               "", None, t0, t0))
            else:
                output = host_output
            t1 = time.time()
            events.append(ToolEvent(id=cid, function=tool, arguments=arguments, result=output, timestamp=moment(t0),
                                    completed=moment(t1)))
            steps[agent].append({"step_id": len(steps[agent]) + 1, "source": "agent", "message": "",
                                 "tool_calls": [{"tool_call_id": cid, "function_name": tool, "arguments": arguments}],
                                 "observation": {"results": [{"source_call_id": cid, "content": output}]}})
            time.sleep(1.5)     # each call in its own seconds, as a model's turns are
        end = replayer.read_end(box)
    log = folder / "logs" / "replay_smoke.eval"
    log.parent.mkdir(parents=True)
    spec = EvalSpec(created=moment(time.time()).isoformat(), task="swe", dataset=EvalDataset(),
                    model="mockllm/model", config={})
    write_eval_log(EvalLog(eval=spec, samples=[EvalSample(id=ISSUE, epoch=1, input="", target="", events=events)]),
                   str(log))
    swe.update({"setup": built, "team": end["team"], "agents_final": end["agents"]})
    transcripts = {a: {"orbit": {"agent": a}, "trajectory": {"steps": [{"step_id": 0, "source": "system",
                                                                        "message": "rules"}, *steps[a]]}}
                   for a in AGENTS}
    run_dir = write_run_dir(folder / "run", {**run_json, "eval_log": str(log), "swe": swe}, transcripts, row=ISSUE)
    diffs = run_dir / "processed/swe" / "diffs"
    diffs.mkdir(parents=True)
    for name, text in end["diffs"].items():
        (diffs / name).write_text(text)
    shutil.rmtree(draft)
    return run_dir


def check(condition: bool, what: str) -> None:
    if not condition:
        raise AssertionError(what)


def faithful(run_dir: Path, out: Path) -> None:
    report = replay(run_dir, out)
    check(report.faithful and not report.refused and not report.error,
          f"not faithful: {report.refused or report.error or report.end}")
    check(report.calls == 11 and report.host == 2 and report.sandbox_calls == 9, f"calls: {report}")
    check(set(report.match) <= {"exact", "masked", "order"}, f"an output differs: {report.mismatches}")
    by = {(d["agent"], d["k"]): d for d in report.dated}
    check(len(report.dated) == 3 and not report.undated, f"dated {report.dated}, undated {report.undated}")
    check(not by[("a01", 1)]["author_too"] and by[("a02", 1)]["author_too"] and by[("a02", 2)]["author_too"],
          f"which dates moved: {report.dated}")
    lines = faithful_steps(out)
    check([line["n"] for line in lines] == list(range(1, 12)), "one line per call, in order")
    at = {(line["agent"], line["k"]): line for line in lines}
    check(at[("a01", 2)]["match"] == "recorded" and at[("a02", 4)]["match"] == "recorded", "host tools recorded")
    check(not at[("a01", 0)]["state"]["pieces"]["B"]["tree"] and at[("a01", 1)]["state"]["pieces"]["B"]["tree"],
          "a01's tree holds B from its cherry-pick on")
    check(at[("a01", 3)]["state"]["pieces"]["B"]["team"] == ["refs/heads/main"], "team/main holds B after the push")
    a02 = at[("a02", 2)]["state"]["pieces"]
    check(a02["B"]["tree"] and not a02["C"]["tree"] and a02["C"]["refs"] == [hooks.piece_refs(synthetic_swe())["C"]],
          f"a02 after the pull: {a02}")
    check(at[("a01", 4)]["state"]["dirty"] and not at[("a01", 3)]["state"]["dirty"], "a01 dirty only at the end")
    raw = {(line["agent"], line["k"]): line for line in map(json.loads, (out / STEPS).read_text().splitlines())}
    check(raw[("a01", 0)]["state"]["refs"] == at[("a01", 0)]["state"]["refs"] and
          list(raw[("a01", 1)]["state"]["refs"]) == ["refs/heads/a01"], "refs whole on the first line, then the change")


def unfaithful(run_dir: Path, out: Path) -> None:
    copy = out.parent / "run_tampered"
    shutil.copytree(run_dir, copy)
    stored = copy / "processed/swe" / "diffs" / "team.diff"
    stored.write_text(stored.read_text() + " ")
    report = replay(copy, out)
    check(not report.faithful and not report.refused and not report.error, f"tampered run: {report.end}")
    check(not report.end["diffs"]["team.diff"]["identical"], "the tampered diff differs")
    try:
        faithful_steps(out)
    except NotFaithful:
        return
    raise AssertionError("faithful_steps read an unfaithful replay")


def refused(run_dir: Path, out: Path) -> None:
    copy = out.parent / "run_replanted"
    shutil.copytree(run_dir, copy)
    run_json = json.loads((copy / "processed/run.json").read_text())
    run_json["swe"]["setup"]["agents"]["a01"]["planted"]["B"] = "0" * 40
    write_text(copy / "processed/run.json", json.dumps(run_json))
    report = replay(copy, out)
    check(bool(report.refused) and "planted" in report.refused and not report.faithful, f"refusal: {report.refused}")


SCENARIOS = {"faithful": faithful, "unfaithful": unfaithful, "refused": refused}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=settings.BUILD / "replay_smoke" / invocation_stamp())
    args = parser.parse_args()
    progress.step("Record a tiny SWE run in a real sandbox")
    run_dir = record(args.out / "record")
    progress.wrote(run_dir, "recorded")
    failed = []
    for i, (name, scenario) in enumerate(SCENARIOS.items(), 1):
        progress.step(f"[{i}/{len(SCENARIOS)}] replay: {name}")
        t0 = time.monotonic()
        try:
            scenario(run_dir, args.out / "replays" / name)
            print(f"[PASS] {name} in {progress.duration(time.monotonic() - t0)}", flush=True)
        except Exception:  # noqa: BLE001 - every scenario runs and reports
            failed.append(name)
            print(f"[FAIL] {name} in {progress.duration(time.monotonic() - t0)}", flush=True)
            progress.detail("traceback", traceback.format_exc().rstrip())
    if failed:
        progress.failed(f"{len(failed)} of {len(SCENARIOS)} replay scenarios failed ({', '.join(failed)})")
        return 1
    progress.done(f"{len(SCENARIOS)} of {len(SCENARIOS)} replay scenarios passed", out=progress.short(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
