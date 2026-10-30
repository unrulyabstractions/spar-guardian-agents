"""The command line (src/cli.py): the parser's defaults and refusals, and the config it builds, in order."""
from __future__ import annotations

import inspect

import pytest

from src.cli import build_parser
from src.configuration.presets import build_config, config_pairs
from src.domains.swe import SWE


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
