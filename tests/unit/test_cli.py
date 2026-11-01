"""The command line (src/cli.py): the parser's defaults and refusals, the config it builds, in order, the --replace
flag that reaches the run and the judge, a run name found in its group, and the figures' folder."""
from __future__ import annotations

import inspect

import pytest

from src import cli, run
from src.cli import build_parser
from src.configuration import settings
from src.configuration.presets import build_config, config_pairs
from src.domains.swe import SWE
from src.measures import run_figures


def test_a_run_name_finds_the_run_in_its_group_and_its_figures_mirror_it(tmp_path, monkeypatch, capsys):
    """A command reads a run by name wherever its study keeps it; plots draws into out/<study>/<group>/<run>/. Two
    folders of one name are an argument error (exit 2)."""
    monkeypatch.setattr(settings, "RUNS", tmp_path / "runs" / "study_x")
    grouped = tmp_path / "runs" / "study_x" / "invalid" / "c_luna1"
    grouped.mkdir(parents=True)
    (grouped / "run.json").write_text("{}")
    assert cli.run_dir("c_luna1") == grouped and cli.run_dir("gone1") == settings.RUNS / "gone1"
    drawn = []
    monkeypatch.setattr(run_figures, "plot_run", lambda run_dir, out_dir: drawn.append((run_dir, out_dir)) or [])
    assert cli.main(["plots", "c_luna1"]) == 0
    assert drawn == [(grouped, tmp_path / "out" / "study_x" / "invalid" / "c_luna1")]
    (settings.RUNS / "c_luna1").mkdir()
    (settings.RUNS / "c_luna1" / "run.json").write_text("{}")
    assert cli.main(["plots", "c_luna1"]) == 2 and "2 folders hold a run named c_luna1" in capsys.readouterr().err


@pytest.mark.parametrize("argv, expected", [
    ([], ("default", None, "small", 3)),
    (["--scale", "medium", "--set", "n_agents=4"], ("default", "medium", "medium", 4)),
    (["--preset", "default", "--scale", "large", "--set", "steps_per_agent=5"], ("default", "large", "large", 100)),
])
def test_cli_builds_the_config_in_order(argv, expected):
    args = build_parser().parse_args(["run", "x", *argv])
    c = build_config(args.preset, args.scale, args.settings)
    assert (args.preset, args.scale, c.scale, c.n_agents) == expected
    if "steps_per_agent=5" in argv:
        assert c.steps_per_agent == 5 and c.max_concurrency == 50


def test_cli_defaults_to_the_jigsaw_honeypot():
    args = build_parser().parse_args(["run", "x", "--benchmark", "swe"])
    assert (args.honeypot, args.row) == ("jigsaw", None)
    assert build_parser().parse_args(["run", "x", "--benchmark", "knowledge_work"]).benchmark == "knowledge_work"
    assert inspect.signature(SWE).parameters["honeypot"].default == "jigsaw" and SWE.options == ("honeypot", "grade")


def test_cli_rejects_unknown_scale_and_preset():
    for argv in (["--scale", "huge"], ["--preset", "small"]):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["run", "x", *argv])


def test_cli_role_model_flags_come_after_every_set(capsys):
    args = build_parser().parse_args(["run", "x", "--worker-model", "mockllm/model", "--no-judge",
                                      "--regulator-model", "deepseek-flash-openrouter",
                                      "--set", "worker_model=gemini-3.8-flash"])
    flash = "openrouter/deepseek/deepseek-v4.1-flash-1017"
    assert args.model == flash and args.no_judge                # the default model, resolved once by its flag
    assert config_pairs(args) == ["worker_model=gemini-3.8-flash", "worker_model=mockllm/model",
                                  f"regulator_model={flash}"]
    config = build_config(args.preset, args.scale, config_pairs(args))
    assert config.role_models() == {"worker": "mockllm/model", "regulator": flash, "disregulator": None}
    assert config_pairs(build_parser().parse_args(["run", "x"])) == []
    with pytest.raises(SystemExit) as stop:
        build_parser().parse_args(["run", "x", "--disregulator-model", "nope"])
    assert stop.value.code == 2 and "unknown model 'nope'" in capsys.readouterr().err


def test_replace_reaches_the_run_and_the_judge(monkeypatch):
    """run and judge refuse a folder that holds another eval's run (src/run.py); --replace is the one way past that."""
    seen = []
    monkeypatch.setattr(run, "run", lambda *args, **kwargs: seen.append(("run", kwargs["replace"])) or 0)
    monkeypatch.setattr(run, "judge", lambda *args, **kwargs: seen.append(("judge", kwargs["replace"])) or 0)
    for command in (["run", "x"], ["judge", "x.eval", "x"]):
        for flag in ([], ["--replace"]):
            args = build_parser().parse_args([*command, *flag])
            assert args.func(args) == 0
    assert seen == [("run", False), ("run", True), ("judge", False), ("judge", True)]
