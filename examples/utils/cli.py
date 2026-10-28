"""The arguments every example script takes, and the defaults they share.

    args = parse_example_arguments(Path(__file__), __doc__)
    config = build_config(args.preset, args.scale, args.settings)
    run.run(args.name, OpenMath("riemann"), config, args.model, args.timeout)

--model takes an entry of src/configuration/models.yaml or an Inspect model string and comes back resolved to the
Inspect string (DEFAULT_MODEL); --preset and --scale name the config's base; --set key=value names a SwarmConfig
field, repeatable, and comes back after DEFAULT_SETTINGS in args.settings; --no-judge adds `judge=none` after them,
so the run is not judged until `scripts/rejudge.py <name>`; --name is the run's name; --timeout bounds one model
request. A wrong model, field or value is an argument error (exit 2).
"""
from __future__ import annotations

import argparse
from pathlib import Path

from src.configuration import models, settings
from src.configuration.scales import SCALES
from src.configuration.swarm_config import PRESETS, SwarmConfig

DEFAULT_MODEL = "gpt-6-luna"
"""The agents' model, as a registry entry (src/configuration/models.yaml). Every run before 2026-10-27 used
gemini-3.8-flash."""

DEFAULT_SETTINGS = ["time_limit_s=3600", "batch_judge=false"]
"""An hour for the agents, since the small scale's 600 s would stop a SWE run before its turn budget; and a
synchronous judge, whose verdicts come back with the run rather than hours later through the batch API."""


def parse_example_arguments(script: Path, description: str | None, argv: list[str] | None = None) -> argparse.Namespace:
    """The shared arguments, parsed: the model resolved, the settings after the defaults. The script's path names the
    program and the run."""
    prog = f"python -m examples.{script.resolve().parent.name}.{script.stem}"
    parser = argparse.ArgumentParser(prog=prog, description=description,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"the agents' model: an entry of src/configuration/models.yaml (python -m src models) "
                             f"or an Inspect model string (default {DEFAULT_MODEL})")
    parser.add_argument("--preset", choices=sorted(PRESETS), default="default", help="the preset (default: default)")
    parser.add_argument("--scale", choices=list(SCALES), default="small", help="the run's size (default: small)")
    parser.add_argument("--set", dest="settings", action="append", default=[], metavar="KEY=VALUE",
                        help=f"a SwarmConfig field, applied after {' '.join(DEFAULT_SETTINGS)}; repeatable")
    parser.add_argument("--no-judge", action="store_true",
                        help="run without the judge (judge=none); `uv run python scripts/rejudge.py <name>` judges "
                             "it later, through the batch API or with --sync")
    parser.add_argument("--name", default=script.stem, help="the run's name: runs/<name>/ and out/<name>/")
    parser.add_argument("--timeout", type=int, default=settings.MODEL_TIMEOUT_S,
                        help="seconds one model request may take, retries included")
    args = parser.parse_args(argv)
    try:
        args.model = models.resolve(args.model)
    except ValueError as error:
        parser.error(str(error))
    for pair in args.settings:
        key, sep, _ = pair.partition("=")
        if not sep or key.strip() not in SwarmConfig.model_fields:
            parser.error(f"--set takes a SwarmConfig field as key=value, got {pair!r}")
    args.settings = DEFAULT_SETTINGS + args.settings + (["judge=none"] if args.no_judge else [])
    return args
