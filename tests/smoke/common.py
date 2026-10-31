"""What the mock-model smoke suites share: the Suite object, the transcript readers and the one main.

    SUITE = Suite("smoke_swe", judge_spec(SWE.task_dir, SWE.name))
    log, summary, records = SUITE.run("board", task, scripted(script))
    sample = assert_ok(log)
    SUITE.assert_judged("board", sample, records)
    ...
    if __name__ == "__main__":
        raise SystemExit(main(SUITE, SCENARIOS, __doc__))

Every suite runs the real sandbox under Inspect's mockllm, exports the log with src.export.run_export.write_run and
reads the run directory back the way CheatBench's judge does. A suite keeps only its own scenarios, its task builders
and the checks of its domain. The access log stays on, so every scenario also holds that the log worked (log_worked).
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import functools
import json
import shutil
import traceback
from collections.abc import Callable
from pathlib import Path

from inspect_ai import eval as inspect_eval
from inspect_ai.log import read_eval_log
from inspect_ai.model import get_model

from src.configuration import settings
from src.configuration.presets import build_config
from src.configuration.swarm_config import SwarmConfig
from src.export.run_export import write_run
from src.measures.code_ladder import run_signals
from src.measures.run_directory import RunDir
from src.sandbox.machines import agent_machines as machines
from src.swarm import turn_rules as rules
from src.swarm.turn_rules._turn_state import HALT_TAG
from src.tasks import swarm_task
from src.utils.cheatbench_core import judge as core_judge
from src.utils.json_files import read_json_dir, read_jsonl
from tests.support.mock_model import MODEL, honest_judge, tagged_infos
from tests.support.ordered_ids import use_ordered_ids

use_ordered_ids()       # scenarios script their agents by name; `drawn_ids` runs one on the real draw

TIME_LIMIT_S = 1200
BOARD_TOOLS = set(rules.BOARD_TOOLS)
is_judgeable = core_judge.is_judgeable


# ------------------------------------------------------------------------------------------------ configs and tasks

def log_worked(block: dict | None) -> bool:
    """Whether a run's access log saw all it could: valid, kept on the host, switched off, or short only of objects in a
    pack, with no harness read of content beyond the harness's own, so every scenario of every domain measures what the
    harness itself reads. A stand-in task records no switch and has no block."""
    if block is None:
        return True
    reads = block.get("harness_reads") or {"count": 0, "own": 0}
    if reads["count"] > reads["own"]:
        return False
    if block["status"] in ("valid", "host", "off"):
        return True
    return block["status"] == "partial" and not (block["gaps"] or block["gone"] or block["dropped"])


def small(*pairs: str) -> SwarmConfig:
    """The default preset at the small scale with the smoke time limit, then the pairs."""
    return build_config("default", "small", [f"time_limit_s={TIME_LIMIT_S}", *pairs])


def task_for(benchmark, **fields):
    """The benchmark under a SwarmConfig of the field defaults, the smoke time limit and `fields`."""
    return swarm_task(benchmark, SwarmConfig(**{"time_limit_s": TIME_LIMIT_S, **fields}))


# ------------------------------------------------------------------------------------------------ the suite

class Suite:
    """A suite's directories and its domain's judge spec. A stamped suite gets a fresh pair per invocation,
    logs/<name>/<stamp>/ and runs/<name>/<stamp>/; an unstamped one the fixed logs/<name>/ and runs/<name>/, for runs
    read by name elsewhere (openmath's)."""

    def __init__(self, name: str, spec=None, *, stamped: bool = True) -> None:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        tail = Path(name) / stamp if stamped else Path(name)
        self.logs, self.runs, self.spec = settings.LOGS / tail, settings.RUNS / tail, spec

    def with_spec(self, spec) -> Suite:
        """The same directories under another judge spec, for a suite over several tasks."""
        suite = copy.copy(self)
        suite.spec = spec
        return suite

    def run(self, name: str, task, outputs, judge: Callable | None = None, *, damaged_log: bool = False) -> tuple:
        """Run one scenario under the mock model and export it: (the eval log, write_run's summary, the episode
        records). `outputs` drives the agents' model and `judge` the judge role (honest_judge(spec) when None). Earlier
        files of the same name are removed first, and the machines layer's host tools forget their states. The run's
        access log must have worked, unless the scenario hurts it on purpose (`damaged_log`)."""
        shutil.rmtree(self.logs / name, ignore_errors=True)
        shutil.rmtree(self.runs / name, ignore_errors=True)
        machines._HOST_TOOLS.clear()      # task tool states are keyed by sample, epoch and owner: one eval per scenario
        model = get_model(MODEL, custom_outputs=outputs)
        judge_model = get_model(MODEL, custom_outputs=judge or honest_judge(self.spec))
        [log] = inspect_eval(task, model=model, model_roles={"judge": judge_model}, log_dir=str(self.logs / name),
                             display="plain", log_level="warning")
        log = read_eval_log(log.location, resolve_attachments=True)
        summary = write_run(Path(log.location), self.runs / name)
        block = self.run_json(name).get("access_log")
        assert damaged_log or log_worked(block), block
        return log, summary, read_jsonl(self.runs / name / "episodes.jsonl")

    def assert_judged(self, name: str, sample, records: list[dict], cheated: float = 0.0) -> None:
        """Every agent with a transcript was judged by the mock judge: the score's verdicts name them, its value is
        `cheated`, every verdict parsed, and judge.jsonl holds one row per such episode with the task's fields and the
        judge's name."""
        score = sample.scores["cheatbench_judge"]
        with_transcript = sorted(r["orbit_agent"] for r in records if r["transcript_path"])
        assert sorted(score.metadata["verdicts"]) == with_transcript and score.value == cheated, score
        assert all(v["parse_ok"] for v in score.metadata["verdicts"].values()), score.metadata["verdicts"]
        rows = self.judge_rows(name)
        assert sorted(r["episode"] for r in rows) == sorted(r["episode"] for r in records if r["transcript_path"]), rows
        assert all(r["parse_ok"] and r["judge"] == settings.JUDGE_MODEL for r in rows), rows
        assert all(set(self.spec.fields) <= set(r) for r in rows), rows[0]

    def judge_rows(self, name: str) -> list[dict]:
        return read_jsonl(self.runs / name / "judge.jsonl")

    def run_json(self, name: str) -> dict:
        return RunDir(self.runs / name).run_json

    def run_block(self, name: str, sample, task: str, key: str, scorer: str, *, extra: tuple[str, ...] = ()) -> dict:
        """run.json, after checking its task and its `key` block: the sample's layout plus the scorer's outcome, and
        the score metadata's `extra` keys, which the domain's run_blocks adds."""
        run = self.run_json(name)
        score = sample.scores[scorer]
        added = {field: score.metadata[field] for field in extra}
        assert run["task"] == task, run["task"]
        assert run[key] == plain({**sample.metadata["cheatbench"][key], **score.metadata[key], "value": score.value,
                                  **added}), sorted(run[key])
        return run

    def ladder(self, name: str) -> dict[str, dict]:
        """The run's agents on the code ladder, by agent."""
        return {row["agent"]: row for row in run_signals(self.runs / name)}

    def team_record(self, name: str) -> dict:
        """The code ladder's team record of the run's one sample."""
        [record] = RunDir(self.runs / name).team_signals()
        return record

    def board_posts(self, name: str) -> list[tuple[str, str]]:
        """(sender, content) of each post on the run's one board."""
        [board] = read_json_dir(self.runs / name / "communication").values()
        return [(m["sender"], m["content"]) for m in board["messages"]]


def plain(value):
    """The value as run.json stores it."""
    return json.loads(json.dumps(value))


def assert_ok(log):
    """The log's one sample, once the eval succeeded with no error and no limit."""
    sample = log.samples[0]
    assert log.status == "success" and sample.error is None and sample.limit is None, (log.status, sample.error)
    return sample


# ------------------------------------------------------------------------------------------------ reading a run back

def load_transcript(record: dict) -> dict:
    """The episode's transcript, read as CheatBench's judge reads it: it must parse as ATIF and render."""
    assert record["transcript_path"], f"{record['episode']}: no transcript"
    transcript = json.loads(Path(record["transcript_path"]).read_text())
    assert core_judge.render_transcript(transcript).strip(), "rendered transcript is empty"
    return transcript


def steps_of(transcript: dict) -> list[dict]:
    return transcript["trajectory"]["steps"]


def tool_calls_of(transcript: dict) -> list[tuple[str, dict]]:
    return [(tc["function_name"], tc["arguments"]) for s in steps_of(transcript) for tc in (s.get("tool_calls") or [])]


def results_of(transcript: dict) -> list[str]:
    return [r["content"] for s in steps_of(transcript) for r in ((s.get("observation") or {}).get("results") or [])]


def results_text(transcript: dict) -> str:
    return "\n".join(results_of(transcript))


def first_user(transcript: dict) -> str:
    return next(s["message"] for s in steps_of(transcript) if s["source"] == "user")


def system_text(transcript: dict) -> str:
    return next(s["message"] for s in steps_of(transcript) if s["source"] == "system")


def by_agent(records: list[dict]) -> dict[str, dict]:
    return {r["orbit_agent"]: r for r in records}


def results_by_agent(records: list[dict]) -> dict[str, list[str]]:
    return {r["orbit_agent"]: results_of(load_transcript(r)) for r in records}


def timeline_of(sample, agent: str) -> list[tuple[str, list[str]]]:
    """(step id, tools called) per activation."""
    return [(e["step"], e["tools"]) for e in sample.store.get("TurnState:timeline") if e["agent"] == agent]


def charges_of(sample, agent: str) -> list[str]:
    """What each of the agent's activations was charged."""
    return [e["charge"] for e in sample.store.get("TurnState:timeline") if e["agent"] == agent]


def halts_of(sample) -> list[dict]:
    """The info events of a plan cut after the team's hand-in."""
    return tagged_infos(sample, HALT_TAG)


# ------------------------------------------------------------------------------------------------ the runner

def chosen(only: str, known: dict) -> list[str]:
    """The names of a comma-separated option, or every name."""
    names = [n for n in only.split(",") if n]
    unknown = [n for n in names if n not in known]
    assert not unknown, f"unknown names {unknown}; choose from {list(known)}"
    return names or list(known)


def run_jobs(jobs: dict[str, Callable[[], dict]], runs: Path) -> int:
    """Run every job, printing one [PASS] or [FAIL] line each (a failure is recorded, not raised, so every job
    reports), write smoke_results.json under `runs`, the suite's run directory, and return 1 when any failed."""
    results, failed = {}, []
    for label, job in jobs.items():
        try:
            results[label] = {"status": "PASS", "run": str(runs / label), **job()}
        except Exception as ex:  # noqa: BLE001 - report every scenario, then fail
            results[label] = {"status": "FAIL", "run": str(runs / label), "error": f"{type(ex).__name__}: {ex}"[:3000],
                              "traceback": traceback.format_exc()[-4000:]}
            failed.append(label)
        shown = {k: v for k, v in results[label].items() if k != "traceback"}
        print(f"[{results[label]['status']}] {label}: {json.dumps(shown, default=str)[:3000]}", flush=True)
        if results[label]["status"] == "FAIL":
            print(results[label]["traceback"], flush=True)
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "smoke_results.json").write_text(json.dumps(results, indent=1, default=str))
    print("FAILED:" if failed else "ALL PASS:", failed or list(jobs), runs)
    return 1 if failed else 0


def main(suite: Suite, scenarios: dict[str, Callable], description: str, tasks: dict | None = None,
         argv: list[str] | None = None) -> int:
    """A suite's command line. --only picks scenarios. With `tasks`, each scenario takes a task's value and runs once
    per task, as <task>_<scenario>, and --tasks picks the tasks."""
    parser = argparse.ArgumentParser(description=description, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", default="", help="comma-separated scenario names")
    if tasks:
        parser.add_argument("--tasks", default="", help="comma-separated task names")
    args = parser.parse_args(argv)
    names = chosen(args.only, scenarios)
    jobs = {n: scenarios[n] for n in names}
    if tasks:
        jobs = {f"{t}_{n}": functools.partial(scenarios[n], tasks[t]) for t in chosen(args.tasks, tasks) for n in names}
    return run_jobs(jobs, suite.runs)
