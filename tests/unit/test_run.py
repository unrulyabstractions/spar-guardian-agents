"""src/run.py without a real model: the generate config it hands the agents, and the judge's role model."""
from __future__ import annotations

import pytest
from inspect_ai.model import GenerateConfig
from pydantic import ValidationError

from src import run as run_module
from src import tasks
from src.configuration import settings
from src.configuration.presets import build_config
from src.domains.openmath import OpenMath
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
