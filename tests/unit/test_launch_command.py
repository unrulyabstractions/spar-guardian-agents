"""run.json "launch" (src/configuration/launch_command.py): the command line of a run, its keys redacted, from the
example scripts' parser to the task's metadata to the export. No Docker, no model call."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from examples.example_arguments import parse_example_arguments
from src import tasks
from src.configuration import launch_command, settings
from src.configuration.presets import build_config
from src.domains.openmath import OpenMath
from src.export import _export_run_blocks as blocks

FLIPT = settings.ROOT / "examples" / "swe" / "flipt_minimal.py"


@pytest.fixture(autouse=True)
def no_launch(monkeypatch):
    monkeypatch.setattr(launch_command, "_LAUNCH", None)


def test_an_example_records_its_module_and_arguments_as_typed(monkeypatch):
    argv = ["--model", "gpt-6-luna-openrouter", "--no-judge", "--name", "swe_luna_x_s1", "--set", "seed=1",
            "--set", "seed_pack=swe/revealing"]
    monkeypatch.setattr(sys, "argv", [str(FLIPT), *argv])
    parse_example_arguments(FLIPT, None)
    launch = launch_command.current()
    assert launch["module"] == "examples.swe.flipt_minimal" and launch["argv"] == argv
    assert launch["study"] == settings.STUDY
    assert launch["command"] == (f"STUDY={settings.STUDY} uv run python -m examples.swe.flipt_minimal "
                                 "--model gpt-6-luna-openrouter --no-judge --name swe_luna_x_s1 --set seed=1 "
                                 "--set seed_pack=swe/revealing")


def test_a_parse_of_given_arguments_records_nothing():
    """A test or a script that hands the parser its own list is no launch of the process."""
    parse_example_arguments(FLIPT, None, ["--name", "x"])
    assert launch_command.current() is None


def test_a_key_never_reaches_the_record():
    launch = launch_command.record("src", ["run", "x", "--set", "api_key=abc123", "sk-or-v1-0123456789abcdef",
                                          "TOKEN=t", "--set", "seed=2"])
    assert launch["argv"] == ["run", "x", "--set", "api_key=REDACTED", "REDACTED", "TOKEN=REDACTED", "--set", "seed=2"]
    assert "abc123" not in launch["command"] and "0123456789" not in launch["command"]


def test_the_task_and_the_export_carry_the_launch():
    config = build_config("default", "small", ["n_agents=3"])
    plain = tasks.swarm_task(OpenMath(), config).dataset[0].metadata["cheatbench"]
    assert "launch" not in plain
    launch = launch_command.record("examples.openmath.riemann_minimal", ["--name", "r"], study="some_study")
    cb = tasks.swarm_task(OpenMath(), config).dataset[0].metadata["cheatbench"]
    assert cb["launch"] == launch
    view = SimpleNamespace(store_get={}.get, metadata={"experiment": {}, "cheatbench": cb}, scores={}, limit=None,
                           error=None)
    assert blocks.recorded_run_blocks(view)["launch"] == launch
    assert (settings.ROOT / Path(*launch["module"].split("."))).with_suffix(".py").is_file()
