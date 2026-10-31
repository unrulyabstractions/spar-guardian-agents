"""The arguments every example script takes, and the defaults they share.

    args = parse_example_arguments(Path(__file__), __doc__)
    config = build_config(args.preset, args.scale, args.settings)
    return finish(args, run.run(args.name, OpenMath("riemann"), config, args.model, args.timeout))

The model and config flags are the command line's (presets.add_config_arguments): --model and the role-model flags
come back as Inspect strings, and args.settings holds the run's name as `id_salt`, DEFAULT_SETTINGS, every --set, then
each role model as `<role>_model=<Inspect string>`. --judge and --no-judge say whether the run is judged as it finishes
(USE_JUDGE_AS_DEFAULT otherwise); a run that is not adds `judge=none` last, and `python -m src rejudge <name>` judges
it later. --name is the run's name and --timeout bounds one model request. A wrong model or field is an argument
error (exit 2). finish() ends every script: after a run that succeeded it draws the run's figures under out/<name>/
and returns the exit code either way.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from src.configuration import settings
from src.configuration.presets import add_config_arguments, config_pairs
from src.configuration.swarm_config import SwarmConfig
from src.measures import run_figures as plots

USE_JUDGE_AS_DEFAULT = False
"""Whether an example judges its run as it finishes; --judge and --no-judge override it for one run."""
DEFAULT_SETTINGS = ["batch_judge=false"]
"""A synchronous judge when a run is judged as it finishes, so its verdicts come back with the run rather than hours
later through the batch API. The time limit is the scale's."""


def parse_example_arguments(script: Path, description: str | None, argv: list[str] | None = None) -> argparse.Namespace:
    """The shared arguments, parsed: the models resolved, the settings after the defaults. The script's path names
    the program and the run."""
    prog = f"python -m examples.{script.resolve().parent.name}.{script.stem}"
    parser = argparse.ArgumentParser(prog=prog, description=description,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_config_arguments(parser)
    judging = parser.add_mutually_exclusive_group()
    judging.add_argument("--judge", dest="judge", action="store_true", default=USE_JUDGE_AS_DEFAULT,
                         help="judge the run as it finishes" + (" (default)" if USE_JUDGE_AS_DEFAULT else ""))
    judging.add_argument("--no-judge", dest="judge", action="store_false",
                         help="run without the judge; `uv run python -m src rejudge <name>` judges it later"
                              + ("" if USE_JUDGE_AS_DEFAULT else " (default)"))
    parser.add_argument("--name", default=script.stem, help="the run's name: runs/<study>/<name>/ and out/<name>/")
    parser.add_argument("--timeout", type=int, default=settings.MODEL_TIMEOUT_S,
                        help="seconds one model request may take, retries included")
    args = parser.parse_args(argv)
    for pair in args.settings:
        key, sep, _ = pair.partition("=")
        if not sep or key.strip() not in SwarmConfig.model_fields:
            parser.error(f"--set takes a SwarmConfig field as key=value, got {pair!r}")
    args.settings = [f"id_salt={args.name}", *DEFAULT_SETTINGS, *config_pairs(args),
                     *([] if args.judge else ["judge=none"])]
    return args


def finish(args: argparse.Namespace, exit_code: int) -> int:
    """The end of every example: the run's figures into out/<name>/ when the run succeeded (src/measures/run_figures.py
    plot_run), and the run's exit code either way."""
    if exit_code == 0:
        plots.plot_run(settings.RUNS / args.name, settings.ROOT / "out" / args.name)
    return exit_code
