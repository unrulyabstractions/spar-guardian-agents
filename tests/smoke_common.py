"""What the five mock-model smoke suites share: the scripted mock model, the mock judge, one scenario's eval and
export, the transcript readers, the judged-run check and the runner that prints one line per scenario.

    LOGS, RUNS = suite_dirs("smoke_swe")
    log, summary, records = run_scenario(LOGS, RUNS, SPEC, "board", task, scripted(script), judge=None)
    assert_judged(RUNS, SPEC, "board", log.samples[0], records)

Every suite runs the real sandbox under Inspect's mockllm, exports the log with src.export.run_export.write_run
and reads the run directory back the way CheatBench's judge does. A suite keeps only its own scenarios, its task
builders and the checks of its domain.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import traceback
import typing
from collections.abc import Callable
from pathlib import Path

from core.agents import trajectory_from_transcript
from core.render import render_trajectory
from inspect_ai import eval as inspect_eval
from inspect_ai.log import read_eval_log
from inspect_ai.model import ModelOutput, get_model
from orbit.communication.runtime import current_identity

from src.configuration import settings
from src.configuration.swarm_config import SwarmConfig, build_config
from src.export.run_export import write_run
from src.sandbox import machines
from src.tasks import swarm_task
from src.utils.json_files import read_jsonl

MODEL = "mockllm/model"
TIME_LIMIT_S = 1200
BOARD_TOOLS = set(settings.BOARD_TOOLS)


def suite_dirs(suite: str, *, stamped: bool = True) -> tuple[Path, Path]:
    """(logs, runs) of a suite: logs/<suite>/<stamp>/ and runs/<suite>/<stamp>/, a fresh pair per invocation, or
    the fixed logs/<suite>/ and runs/<suite>/ when the suite's runs are read by name elsewhere (openmath's)."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tail = Path(suite) / stamp if stamped else Path(suite)
    return settings.LOGS / tail, settings.RUNS / tail


# ------------------------------------------------------------------------------------------------ the mock models

def call(name: str, args: dict, call_id: str) -> ModelOutput:
    """One model turn that calls one tool."""
    return ModelOutput.for_tool_call(MODEL, name, args, tool_call_id=call_id)


def text(content: str) -> ModelOutput:
    """One model turn of plain text."""
    return ModelOutput.from_content(MODEL, content)


def mock_value(annotation):
    """A Verdict field's value in the mock judge's answer: None for an optional field, "smoke" for a string, False for
    a flag, 0 for a count."""
    if type(None) in typing.get_args(annotation):
        return None
    return {str: "smoke", bool: False, int: 0}[annotation]


def mock_verdict(spec) -> dict:
    """The task's Verdict with every flag false and the summary "smoke: mock judge"."""
    verdict = {name: mock_value(f.annotation) for name, f in spec.verdict.model_fields.items()}
    verdict["summary"] = "smoke: mock judge"
    return verdict


def honest_judge(spec) -> Callable:
    """The mock judge's outputs: mock_verdict(spec) as JSON on every call."""
    verdict = json.dumps(mock_verdict(spec))

    def outputs(messages, tools, tool_choice, config) -> ModelOutput:
        return text(verdict)
    return outputs


def scripted(script: dict, recorder: list | None = None) -> Callable:
    """Each agent follows its own script by its count of model turns; an entry may be a function of the messages.
    The agent is named by its team system prompt ("You are a01.") or by ORBIT's identity; a lone agent is the one
    script. A recorder gets one entry per call: agent, step, the tools offered, the last message, the system texts."""
    def outputs(messages, tools, _tool_choice, _config) -> ModelOutput:
        system = next((m.text for m in messages if m.role == "system" and m.text.startswith("You are ")), "")
        who = system.split()[2].rstrip(".,") if system else "?"
        if who not in script and current_identity() is not None:
            who = current_identity().agent_id
        if who not in script and len(script) == 1:
            who = next(iter(script))
        assert who in script, f"cannot tell which agent is asking from {system[:60]!r}"
        step = sum(1 for m in messages if m.role == "assistant")
        if recorder is not None:
            recorder.append({"agent": who, "step": step, "tools": sorted(t.name for t in tools),
                             "last": messages[-1].text, "system": system,
                             "system_texts": [m.text for m in messages if m.role == "system"]})
        plan = script[who]
        out = plan[min(step, len(plan) - 1)]
        return out(messages) if callable(out) else out
    return outputs


# ------------------------------------------------------------------------------------------------ configs and tasks

def small(*pairs: str) -> SwarmConfig:
    """The default preset at the small scale with the smoke time limit, then the pairs."""
    return build_config("default", "small", [f"time_limit_s={TIME_LIMIT_S}", *pairs])


def task_for(benchmark, **fields):
    """The benchmark under a SwarmConfig of the field defaults, the smoke time limit and `fields`."""
    return swarm_task(benchmark, SwarmConfig(**{"time_limit_s": TIME_LIMIT_S, **fields}))


# ------------------------------------------------------------------------------------------------ one scenario

def run_scenario(logs: Path, runs: Path, spec, name: str, task, outputs, judge: Callable | None = None) -> tuple:
    """Run one scenario under the mock model and export it: (the eval log, write_run's summary, the episode records).
    `outputs` drives the agents' model and `judge` the judge role (honest_judge(spec) when None). Earlier files of the
    same name are removed first, and the machines layer's host tools forget their states."""
    shutil.rmtree(logs / name, ignore_errors=True)
    shutil.rmtree(runs / name, ignore_errors=True)
    machines._HOST_TOOLS.clear()      # task tool states are keyed by sample, epoch and owner: one eval per scenario
    model = get_model(MODEL, custom_outputs=outputs)
    judge_model = get_model(MODEL, custom_outputs=judge or honest_judge(spec))
    [log] = inspect_eval(task, model=model, model_roles={"judge": judge_model}, log_dir=str(logs / name),
                         display="plain", log_level="warning")
    log = read_eval_log(log.location, resolve_attachments=True)
    summary = write_run(Path(log.location), runs / name)
    return log, summary, read_jsonl(runs / name / "episodes.jsonl")


# ------------------------------------------------------------------------------------------------ reading a run back

def load_transcript(record: dict) -> dict:
    """The episode's transcript, read as CheatBench's judge reads it: it must parse as ATIF and render."""
    assert record["transcript_path"], f"{record['episode']}: no transcript"
    transcript = json.loads(Path(record["transcript_path"]).read_text())
    rendered = render_trajectory(trajectory_from_transcript(transcript))
    assert rendered.strip(), "rendered transcript is empty"
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


def assert_judged(runs: Path, spec, name: str, sample, records: list[dict], cheated: float = 0.0) -> None:
    """Every agent with a transcript was judged by the mock judge: the score's verdicts name them, its value is
    `cheated`, every verdict parsed, and judge.jsonl holds one row per such episode with the task's fields and the
    judge's name."""
    score = sample.scores["cheatbench_judge"]
    with_transcript = sorted(r["orbit_agent"] for r in records if r["transcript_path"])
    assert sorted(score.metadata["verdicts"]) == with_transcript and score.value == cheated, score
    assert all(v["parse_ok"] for v in score.metadata["verdicts"].values()), score.metadata["verdicts"]
    rows = read_jsonl(runs / name / "judge.jsonl")
    assert sorted(r["episode"] for r in rows) == sorted(r["episode"] for r in records if r["transcript_path"]), rows
    assert all(r["parse_ok"] and r["judge"] == settings.JUDGE_MODEL for r in rows), rows
    assert all(set(spec.fields) <= set(r) for r in rows), rows[0]


# ------------------------------------------------------------------------------------------------ the runner

def chosen(only: str, scenarios: dict) -> list[str]:
    """The scenario names of --only, or every scenario."""
    names = [n for n in only.split(",") if n]
    unknown = [n for n in names if n not in scenarios]
    assert not unknown, f"unknown scenarios {unknown}; choose from {list(scenarios)}"
    return names or list(scenarios)


def run_jobs(jobs: dict[str, Callable[[], dict]], runs: Path) -> int:
    """Run every job, printing one [PASS] or [FAIL] line each (a failure is recorded, not raised, so every job
    reports), write runs/smoke_results.json, and return 1 when any failed."""
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
