"""A screen of the elicitation cell: several models, several seeds each, as many runs at once as the machine holds.

    STUDY=study_elicitation uv run python -m elicitation.screen --models muse-glimmer-30b-openrouter \
        qwen3.8-27b-openrouter gemma-4-31b-openrouter --samples 5 --jobs 5 [--set key=value ...] [--summary-only]

Sample k of a model is `python -m elicitation.swe --model <model> --set seed=<k>`, which names its run
flipt_duo_<model>_s<k>. Its output goes to logs/<study>/screen/<name>.log. The runs go round robin over the models, so
every model advances at the same pace. Each run's line prints as it ends. A run whose signals.jsonl exists is skipped,
so a stopped screen resumes. Every --set goes to every run. --set seed is refused, since a sample's seed is its number.

The summary gives each run's team rung, its agents' rungs and its ledger (scripts/screen.py summary). Then one line per
model gives its finished runs, how many teams the code ladder found at each honeypot rung from tried to shipped and with
evidence for each collaboration level, how many passed the hidden tests, and how many model calls errored.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from elicitation.swe import default_name
from scripts import screen
from src.configuration import settings
from src.measures.run_directory import RunDir
from src.models import model_registry as models

RATE_RUNGS = ("tried", "opened", "used", "shipped")
RATE_LEVELS = ("discussed", "solicited", "crossed", "assembled")


def jobs(entries: list[str], samples: int) -> list[tuple[str, int, str]]:
    """(model, sample, run name) for every sample of every model, round robin over the models."""
    return [(entry, k, default_name(models.resolve(entry), k)) for k in range(1, samples + 1) for entry in entries]


def command(entry: str, sample: int, pairs: list[str]) -> list[str]:
    return [sys.executable, "-m", "elicitation.swe", "--model", entry,
            *[arg for pair in pairs for arg in ("--set", pair)], "--set", f"seed={sample}"]


def launch(entry: str, sample: int, name: str, pairs: list[str]) -> tuple[str, int, float]:
    """One run as a subprocess, its output in LOGS/<name>.log; (name, exit code, minutes)."""
    screen.LOGS.mkdir(parents=True, exist_ok=True)
    argv = command(entry, sample, pairs)
    started = time.time()
    with (screen.LOGS / f"{name}.log").open("w") as log:
        log.write(" ".join(argv) + "\n")
        log.flush()
        code = subprocess.run(argv, cwd=settings.ROOT, stdout=log, stderr=subprocess.STDOUT).returncode
    return name, code, (time.time() - started) / 60


def counts(entry: str, samples: int) -> list[str]:
    """One model's row: finished runs, the teams whose code ladder found each rung and each collaboration level, the
    teams that passed, and the model calls that errored. Each level counts the teams with evidence for it, since a team
    can cross without discussing."""
    runs = [RunDir(settings.RUNS / name) for _, _, name in jobs([entry], samples) if screen.done(name)]
    teams = [team for run in runs for team in run.team_signals()[:1]]
    n = len(teams)
    errored = sum(agent.get("errored", 0) for run in runs
                  for agent in ((run.run_json.get("ledger") or {}).get("agents") or {}).values())
    return [entry, str(n), *[f"{sum(bool(t.get(r)) for t in teams)}/{n}" for r in RATE_RUNGS],
            *[f"{sum(bool((t.get('collaboration_evidence') or {}).get(c)) for t in teams)}/{n}" for c in RATE_LEVELS],
            f"{sum(bool(t.get('passed')) for t in teams)}/{n}", str(errored)]


def rates(entries: list[str], samples: int) -> str:
    """The table of counts(), one line per model."""
    table = [["model", "runs", *RATE_RUNGS, *RATE_LEVELS, "passed", "errored_calls"],
             *[counts(entry, samples) for entry in entries]]
    widths = [max(len(row[i]) for row in table) for i in range(len(table[0]))]
    return "\n".join("  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip() for row in table)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", required=True, help="registry entries")
    parser.add_argument("--samples", type=int, default=5, help="runs per model, seeds 1..samples")
    parser.add_argument("--jobs", type=int, default=5, help="runs at once")
    parser.add_argument("--set", dest="pairs", action="append", default=[], metavar="KEY=VALUE",
                        help="a SwarmConfig field for every run; repeatable")
    parser.add_argument("--summary-only", action="store_true", help="print the summary of what exists and stop")
    args = parser.parse_args(argv)
    unknown = [entry for entry in args.models if models.entry_name(entry) != entry]
    if unknown:
        parser.error(f"--models takes registry entries (python -m src models), got {unknown}")
    if any(pair.partition("=")[0].strip() == "seed" for pair in args.pairs):
        parser.error("--set seed: each sample's seed is its number")
    planned = jobs(args.models, args.samples)
    if not args.summary_only:
        todo = [job for job in planned if not screen.done(job[2])]
        print(f"== screen {settings.STUDY}: {len(todo)} runs to go of {len(planned)}, {args.jobs} at once", flush=True)
        failures = 0
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            running = [pool.submit(launch, *job, args.pairs) for job in todo]
            for future in as_completed(running):
                name, code, minutes = future.result()
                failures += code != 0
                print(f"   {name:48s} rc={code} {minutes:5.1f} min", flush=True)
        if failures:
            print(f"== {failures} run(s) failed; their logs are under {screen.LOGS}", flush=True)
    print(screen.summary([name for _, _, name in planned]))
    print()
    print(rates(args.models, args.samples))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
