"""src/run.py without a real model: the generate config it hands the agents, and the judge's role model."""
from __future__ import annotations

import json

import pytest
from inspect_ai.model import GenerateConfig
from pydantic import ValidationError

from src import run as run_module
from src import tasks
from src.configuration import settings
from src.configuration.presets import build_config
from src.domains.openmath import OpenMath
from src.measures.run_directory import RunDir
from src.run import agent_generate_config
from tests.support.turn_rules_harness import (
    do,
    pull,
    run,
)


def test_batch_reaches_the_agents_model(tmp_path):
    """run.py passes batch=True to the eval with batch_agents; the agents' calls carry it. The mock ignores it."""
    config = pull(steps_per_agent=1, batch_agents=True)
    scripts = {a: [do(a)] for a in config.names}
    _, rec = run(config, scripts, tmp_path, **run_module.agent_generate_config(config))
    assert rec and all(r["batch"] is True for r in rec)
    _, rec = run(pull(steps_per_agent=1), scripts, tmp_path, **run_module.agent_generate_config(pull()))
    assert rec and all(not r["batch"] for r in rec)


def test_a_config_without_a_judge_binds_no_judge_model():
    assert pull(judge="none").judge is None and pull(judge=None).judge is None
    assert pull().judge == settings.JUDGE_MODEL == "gpt-6-luna"
    assert build_config("default", "small", ["judge=none"]).judge is None
    assert run_module.judge_role_model(pull(judge=None)) is None
    with_judge = tasks.scorers(OpenMath(), pull())
    without = tasks.scorers(OpenMath(), pull(judge=None))
    assert len(with_judge) == len(without) + 1
    assert "cheatbench_judge" in str(with_judge) and "cheatbench_judge" not in str(without)


def test_the_judge_role_model_batches(monkeypatch):
    monkeypatch.setattr(run_module.judging, "judge_model_config", lambda name: ("mockllm/model", GenerateConfig()))
    assert run_module.judge_role_model(pull()).config.batch is True
    assert run_module.judge_role_model(pull(batch_judge=False)).config.batch is False
    assert run_module.judge_role_model(pull(n_agents=1)).config.batch is True   # alone as on a board


def test_the_agents_call_bounds_reach_the_generate_config():
    default = build_config("default", "small")
    assert (default.reasoning_effort, default.tool_output_bytes) == (None, None)
    assert agent_generate_config(default) == {}
    bounded = build_config("default", "small", ["reasoning_effort=low", "tool_output_bytes=8192"])
    assert agent_generate_config(bounded) == {"reasoning_effort": "low", "max_tool_output": 8192}
    assert agent_generate_config(bounded.replace(batch_agents=True))["batch"] is True
    with pytest.raises(ValidationError):
        build_config("default", "small", ["reasoning_effort=hard"])


def test_a_run_pulled_from_a_box_finds_its_log_in_this_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LOGS", tmp_path / "logs")
    run_dir = tmp_path / "runs" / "game_solo_luna1"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({"eval_log": "/root/work/logs/game_solo_luna1/x.eval"}))
    assert RunDir(run_dir).eval_log is None
    log = tmp_path / "logs" / "game_solo_luna1" / "x.eval"
    log.parent.mkdir(parents=True)
    log.write_text("")
    assert RunDir(run_dir).eval_log == log


def test_rejudge_holds_the_runs_on_one_client_and_a_refusal_stops_that_run_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LOGS", tmp_path / "logs")
    dirs = []
    for name in ("good1", "good2", "bad", "gone"):
        run_dir = tmp_path / "runs" / name
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({"eval_log": f"/root/work/logs/{name}/x.eval"}))
        dirs.append(run_dir)
        if name != "gone":
            (tmp_path / "logs" / name).mkdir(parents=True)
            (tmp_path / "logs" / name / "x.eval").write_text("")
    clients, written, ladders = [], [], []

    def views_of(eval_log, judge_errors):
        if eval_log.parent.name == "bad":
            raise run_module.Refusal("run has a harness or provider failure", 5)
        return [eval_log.parent.name]

    async def judged(views, judge):
        return {views[0]: judge}

    monkeypatch.setattr(run_module, "require_judge_key", lambda name, code: None)
    monkeypatch.setattr(run_module, "_log_views", views_of)
    monkeypatch.setattr(run_module, "_judge_views", judged)
    monkeypatch.setattr(run_module, "_write_verdicts", lambda log, name, verdicts: written.append((name, verdicts)))
    monkeypatch.setattr(run_module.judging.Judge, "of", lambda name, batch: clients.append(batch) or "client")
    monkeypatch.setattr(run_module, "ladder_runs", lambda paths, *a, **k: ladders.extend(p.name for p in paths) or 0)
    assert run_module.rejudge(dirs, "gpt-6-luna") == 5
    assert clients == [True]
    assert written == [("good1", {"good1": "client"}), ("good2", {"good2": "client"})]
    assert ladders == ["good1", "good2", "gone"]
