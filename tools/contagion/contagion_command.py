"""`python -m tools contagion <run>... [--models si,threshold,sir] [--synthetic N] [--seed S]`: contagion fits."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from tools.contagion.contagion_models import MODEL_NAMES, Settings
from tools.contagion.contagion_observed import Board, board_of_run, synthetic_board
from tools.contagion.contagion_report import write_outputs
from tools.run_reader import RUNS_DIR, load_run, tools_dir, write_summary

NAME = "contagion"
HELP = "SI, linear threshold and SIR models of dishonest and honest spread on the board, fitted to the onsets"
SIMULATIONS = 200
GRIDS = {
    "si": [float(value) for value in np.round(np.logspace(-3, 0, 31), 6)],
    "threshold": [float(value) for value in np.round(np.linspace(0, 1, 41), 4)],
    "sir": [float(value) for value in np.round(np.logspace(-3, 0.5, 36), 6)],
}
THRESHOLD_SPREAD = 0.1
SIR_GAMMA = 0.1
MIN_AGENTS = 10
SYNTHETIC_ROUNDS = 20
SYNTHETIC_PLANTED = {"dishonest": 0.02, "honest": 0.01}
SYNTHETIC_ROLE_SHARES = {"regulator": 0.1, "disregulator": 0.1}
SYNTHETIC_SEED_ROUNDS = {"dishonest": 1, "honest": 2}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("runs", nargs="*", help="run names under runs/ or run directories")
    parser.add_argument("--models", default=",".join(MODEL_NAMES),
                        help=f"comma-separated models out of {','.join(MODEL_NAMES)} (default: all)")
    parser.add_argument("--synthetic", type=int, metavar="N",
                        help="also fit a synthetic complete board of N agents with seeded random onsets")
    parser.add_argument("--seed", type=int, default=0, help="seed of the simulations and the synthetic board")
    parser.add_argument("--simulations", type=int, default=SIMULATIONS, help="simulations per grid value")


def run(args: argparse.Namespace) -> int:
    models = tuple(name.strip() for name in args.models.split(",") if name.strip())
    unknown = [name for name in models if name not in MODEL_NAMES]
    if unknown or not models:
        print(f"{NAME}: unknown models {unknown}; choose from {','.join(MODEL_NAMES)}", file=sys.stderr)
        return 2
    if not args.runs and args.synthetic is None:
        print(f"{NAME}: give at least one run or --synthetic N", file=sys.stderr)
        return 2
    if args.synthetic is not None and args.synthetic < 2:
        print(f"{NAME}: --synthetic needs at least 2 agents", file=sys.stderr)
        return 2
    settings = Settings(models=models, simulations=args.simulations, seed=args.seed, grids=GRIDS,
                        threshold_spread=THRESHOLD_SPREAD, sir_gamma=SIR_GAMMA, min_agents=MIN_AGENTS)
    failures = 0
    for name in args.runs:
        try:
            run_ = load_run(name)
            board = board_of_run(run_)
            payload = write_outputs(board, settings, tools_dir(run_, NAME), prefix="")
            path = write_summary(run_, NAME, payload)
        except (FileNotFoundError, ValueError, KeyError) as error:
            print(f"{NAME}: {name}: {error}", file=sys.stderr)
            failures += 1
            continue
        report(path, payload)
    if args.synthetic is not None:
        board = synthetic_board(args.synthetic, SYNTHETIC_ROUNDS, args.seed, SYNTHETIC_PLANTED,
                                SYNTHETIC_ROLE_SHARES, SYNTHETIC_SEED_ROUNDS)
        directory = RUNS_DIR / board.name / "tools" / NAME
        payload = write_outputs(board, settings, directory, prefix="synthetic_")
        report(write_synthetic_summary(board, directory, payload), payload)
    return 1 if failures else 0


def write_synthetic_summary(board: Board, directory: Path, payload: dict) -> Path:
    """summary.json of a synthetic board, in the shape write_summary gives a real run."""
    written_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path = directory / "summary.json"
    path.write_text(json.dumps({"tool": NAME, "run": board.name, "written_at": written_at, **payload}, indent=2,
                               default=str) + "\n")
    return path


def report(path: Path, payload: dict) -> None:
    """One line per board on stdout: where the summary went and whether the fit was degenerate."""
    state = f"degenerate ({payload['reason']})" if payload["degenerate"] else "fitted"
    print(f"{NAME}: {path} {state}")
