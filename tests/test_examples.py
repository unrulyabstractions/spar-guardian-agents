"""The examples build without a model: each parses the shared arguments, builds its config in the open, names its
task and runs it; by default three neutral agents on the default preset at the small scale."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from examples import USE_JUDGE_AS_DEFAULT
from examples.utils.example_arguments import DEFAULT_MODEL, DEFAULT_SETTINGS, parse_example_arguments
from src import run
from src.configuration import model_registry as models
from src.configuration import settings
from src.configuration.swarm_config import build_config
from src.run import access_line
from src.tasks import swarm_task
from src.ui import run_figures as plots

EXAMPLES = ["examples.openmath.riemann_minimal", "examples.swe.flipt_minimal",
            "examples.knowledge_work.worktime_minimal", "examples.subset_sum.subset_minimal",
            "examples.prime_factorization.factor_minimal", "examples.protein_design.binder_minimal"]
# The cells of the first experiment, per domain: one agent alone, one agent alone told to cheat, three agents told
# to cheat (CHEAT in the script, appended to the task instruction).
CELLS = {"solo": (1, False), "solo_cheat": (1, True), "minimal_cheat": (3, True)}
CELL_SCRIPTS = [f"examples.{domain}.{stem}_{cell}" for domain, stem in (("openmath", "riemann"), ("swe", "flipt"),
                                                                          ("knowledge_work", "worktime"),
                                                                          ("subset_sum", "subset"),
                                                                          ("prime_factorization", "factor"),
                                                                          ("protein_design", "binder"))
                for cell in CELLS]


def script_of(name: str) -> Path:
    return Path(importlib.import_module(name).__file__)


def test_the_shared_arguments_and_their_defaults():
    script = script_of(EXAMPLES[0])
    args = parse_example_arguments(script, "The first line.", [])
    assert args.model == models.resolve(DEFAULT_MODEL) == "openrouter/deepseek/deepseek-v4.1-flash-1017"
    # no judge by default: judge=none after the settings, and USE_JUDGE_AS_DEFAULT says so
    assert USE_JUDGE_AS_DEFAULT is False and args.judge is False
    assert (args.preset, args.scale, args.settings, args.name, args.timeout) == \
        ("default", "small", [*DEFAULT_SETTINGS, "judge=none"], script.stem, settings.MODEL_TIMEOUT_S)
    assert DEFAULT_SETTINGS == ["batch_judge=false"]
    args = parse_example_arguments(script, None, ["--judge", "--model", "claude-haiku-5-5", "--scale", "medium",
                                                  "--set", "regulators=1", "--set", "time_limit_s=900", "--name",
                                                  "trial", "--timeout", "60"])
    assert args.model == "anthropic/claude-haiku-5-5" and (args.name, args.timeout) == ("trial", 60)
    assert args.judge is True and args.settings == [*DEFAULT_SETTINGS, "regulators=1", "time_limit_s=900"]
    config = build_config(args.preset, args.scale, args.settings)
    assert (config.scale, config.n_agents, config.regulator_count, config.time_limit_s) == ("medium", 10, 1, 900)
    assert config.batch_judge is False and config.judge == settings.JUDGE_MODEL
    # --no-judge, the default spelled out: judge=none after every other setting, so the run is not judged
    args = parse_example_arguments(script, None, ["--no-judge", "--set", "judge=gpt-6-sol"])
    assert args.settings == [*DEFAULT_SETTINGS, "judge=gpt-6-sol", "judge=none"]
    assert build_config(args.preset, args.scale, args.settings).judge is None
    with pytest.raises(SystemExit) as stop:
        parse_example_arguments(script, None, ["--judge", "--no-judge"])
    assert stop.value.code == 2
    for bad in (["--model", "nope"], ["--set", "nope=1"], ["--set", "regulators"], ["--scale", "huge"]):
        with pytest.raises(SystemExit) as stop:
            parse_example_arguments(script, None, bad)
        assert stop.value.code == 2, bad
    with pytest.raises(SystemExit) as stop:
        parse_example_arguments(script, "The first line.", ["--help"])
    assert stop.value.code == 0


def test_a_role_model_flag_comes_after_the_set_pairs():
    script = script_of(EXAMPLES[0])
    flash = models.resolve("deepseek-flash-openrouter")
    args = parse_example_arguments(script, None, ["--regulator-model", "deepseek-flash-openrouter",
                                                  "--set", "regulator_model=mockllm/model"])
    assert args.settings == [*DEFAULT_SETTINGS, "regulator_model=mockllm/model", f"regulator_model={flash}",
                             "judge=none"]
    config = build_config(args.preset, args.scale, args.settings)
    assert config.role_models() == {"worker": None, "regulator": flash, "disregulator": None}
    args = parse_example_arguments(script, None, ["--worker-model", "mockllm/model", "--disregulator-model",
                                                  "gemini-3.8-flash"])
    assert args.settings == [*DEFAULT_SETTINGS, "worker_model=mockllm/model",
                             "disregulator_model=google/gemini-3.8-flash", "judge=none"]
    with pytest.raises(SystemExit) as stop:
        parse_example_arguments(script, None, ["--worker-model", "nope"])
    assert stop.value.code == 2


@pytest.mark.parametrize("name", EXAMPLES)
def test_each_script_runs_three_neutral_agents_on_the_default_preset(name, monkeypatch):
    module = importlib.import_module(name)
    script = script_of(name)
    assert script.stem == name.rsplit(".", 1)[1] and script.parent.name == name.split(".")[1]
    calls, plotted = [], []
    monkeypatch.setattr(run, "run", lambda *args, **kwargs: calls.append((args, kwargs)) or 0)
    monkeypatch.setattr(plots, "plot_run", lambda run_dir, out_dir: plotted.append((run_dir, out_dir)) or [])
    monkeypatch.setattr("sys.argv", [script.name])
    assert module.main() == 0
    [(args, kwargs)] = calls
    run_name, benchmark, config, model, timeout = args
    assert (run_name, model, timeout) == (script.stem, models.resolve(DEFAULT_MODEL), settings.MODEL_TIMEOUT_S)
    assert kwargs == {}
    # The run goes to runs/<name>/ and the figures follow a successful run into out/<name>/.
    assert plotted == [(settings.RUNS / script.stem, settings.ROOT / "out" / script.stem)]
    assert (config.preset, config.scale, config.n_agents) == ("default", "small", 3)
    assert config.regulator_count == 0 and config.disregulator_count == 0 and config.access == "split"
    assert config.time_limit_s == 3600 and config.batch_judge is False and config.turn_budget == settings.TURN_BUDGETS
    assert config.judge is None                                 # not judged as it finishes (USE_JUDGE_AS_DEFAULT)
    benchmark.check(config)
    held = list(benchmark.access_plan(config.names, config, None).values())
    assert all(len(items) == 1 for items in held) and len({i for items in held for i in items}) >= 2
    monkeypatch.setattr("sys.argv", [script.name, "--model", "gemini-3.8-flash", "--name", "gemini",
                                     "--set", "regulators=1"])
    assert module.main() == 0
    args, kwargs = calls[-1]
    assert args[3] == "google/gemini-3.8-flash" and args[2].regulator_count == 1
    assert kwargs == {}
    assert plotted[-1] == (settings.RUNS / "gemini", settings.ROOT / "out" / "gemini")
    # A refused or failed run gets no figures.
    monkeypatch.setattr(run, "run", lambda *args, **kwargs: 3)
    assert module.main() == 3 and len(plotted) == 2


def test_the_openmath_example_task_records_its_plan():
    module = importlib.import_module(EXAMPLES[0])
    args = parse_example_arguments(script_of(EXAMPLES[0]), None, [])
    config = build_config(args.preset, args.scale, args.settings)
    task = swarm_task(module.OpenMath("riemann"), config)
    assert access_line(task) == "== access split: a01 slice1, a02 slice2, a03 slice3"


@pytest.mark.parametrize("name", CELL_SCRIPTS)
def test_each_cell_script_sets_its_agents_and_its_cheat_text(name, monkeypatch):
    module = importlib.import_module(name)
    script = script_of(name)
    n_agents, cheats = CELLS[script.stem.split("_", 1)[1]]
    calls = []
    monkeypatch.setattr(run, "run", lambda *args: calls.append(("run", args)) or 0)
    monkeypatch.setattr(run, "evaluate", lambda *args: calls.append(("evaluate", args)) or 0)
    monkeypatch.setattr(plots, "plot_run", lambda run_dir, out_dir: [])
    monkeypatch.setattr("sys.argv", [script.name])
    assert module.main() == 0
    [(entry, args)] = calls
    if cheats:
        # the cell builds the sample with CHEAT itself and runs the task it built from it
        assert entry == "evaluate"
        run_name, benchmark, config, task, model, timeout = args
        assert module.CHEAT.startswith("Your real objective") and "fair game" in module.CHEAT
        assert ("team" in module.CHEAT) == (n_agents == 3)
        assert task.dataset[0].input.endswith(f"\n{module.CHEAT}\n")
    else:
        assert entry == "run" and not hasattr(module, "CHEAT")
        run_name, benchmark, config, model, timeout = args
    assert (run_name, model, config.n_agents, config.judge) == (script.stem, models.resolve(DEFAULT_MODEL), n_agents,
                                                                None)
    assert config.preset == "default" and config.access == "split" and config.turn_budget == settings.TURN_BUDGETS
    benchmark.check(config)
    if n_agents == 1:
        assert benchmark.access_plan(config.names, config, None) == {"a01": benchmark.whole_access()}
