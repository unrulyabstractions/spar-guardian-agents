"""Each stored run's ledger total beside its calls priced one by one from its eval log, for one study.

    uv run python -m scripts.study.priced_calls <study>                       # runs/<study> and logs/<study>
    uv run python -m scripts.study.priced_calls <study> --runs ../../runs     # from a worktree: main's runs

A run's ledger (processed/run.json "ledger") is what the export priced when the run was stored, and a stored run is
never exported again. Until 2026-11-25 the export priced every Anthropic cache write at the five-minute rate, while
Inspect writes at the one-hour lifetime after a gap of more than five minutes in a sample, and those writes cost 1.6
times as much. This script reads each run's eval log (logs/<study>/<run>/*.eval, beside the runs folder), one sample at
a time, and prices every model call with usage at the registry's rates (model_registry.cost_usd), its one-hour writes
at their own rate, read from the raw response the log kept (eval_sample_view.cache_write_1h). Beside the two it prints
what Inspect itself counted against the run's cost cap (each call's usage.total_cost), and the study's sums.

It reads and never writes. It stops before reading a log while macOS's memory_pressure reports less than 40% free: a
sample's events stay whole in memory, and the 821 MB log of disregulator_existence_2's luna baseline took 17 GB.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from inspect_ai.log import read_eval_log_samples

from src.configuration.settings import ROOT
from src.export.eval_sample_view import cache_write_1h, usage_dict
from src.measures.run_directory import RunDir
from src.models import model_registry as models
from src.models.swarm_model_provider import inspect_string

MIN_FREE = 40
"""The share of memory, in percent, that must be free before a log is read."""
UNREAD = {"messages", "attachments", "store"}
"""The fields of a sample this script never reads, left out of memory."""


def free_memory() -> int | None:
    """macOS's system-wide free memory in percent (memory_pressure), or None where the command gives none."""
    try:
        out = subprocess.run(["memory_pressure"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = re.search(r"free percentage: (\d+)%", out)
    return int(found.group(1)) if found else None


def priced_log(log: Path) -> dict:
    """One eval log's model calls priced one by one: the sum at the registry's rates ("priced"), the sum Inspect
    counted ("inspect"), the calls with usage, those at a model without a price, and the one-hour cache writes."""
    out = {"priced": 0.0, "inspect": 0.0, "calls": 0, "unpriced": 0, "written_1h": 0}
    for sample in read_eval_log_samples(str(log), all_samples_required=False, exclude_fields=UNREAD):
        for event in sample.events:
            usage = getattr(getattr(event, "output", None), "usage", None) if event.event == "model" else None
            if usage is None:
                continue
            tokens, hour = usage_dict(usage), cache_write_1h(event)
            cost = models.cost_usd(models.usd_per_million(inspect_string(event.model)), tokens["input"],
                                   tokens["cache_read"], tokens["output"], cache_write=tokens["cache_write"],
                                   cache_write_1h=hour)
            out["calls"] += 1
            out["written_1h"] += hour
            out["inspect"] += usage.total_cost or 0.0
            if cost is None:
                out["unpriced"] += 1
            else:
                out["priced"] += cost
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("study", help="a study's name, such as regulator_scale_2")
    parser.add_argument("--runs", type=Path, default=ROOT / "runs",
                        help="the runs folder (default: this checkout's runs/); its logs/ sits beside it")
    args = parser.parse_args(argv)
    runs, logs = args.runs / args.study, args.runs.resolve().parent / "logs" / args.study
    if not runs.is_dir():
        parser.error(f"no folder {runs}")
    print(f"{'run':48} {'calls':>6} {'1h writes':>10} {'ledger':>10} {'priced':>10} {'inspect':>10}")
    sums = {"ledger": 0.0, "priced": 0.0, "inspect": 0.0}
    for folder in sorted(runs.iterdir()):
        run, run_json = folder.name, RunDir(folder).file("run.json")
        if not run_json.is_file():
            continue
        ledger = ((json.loads(run_json.read_text()).get("ledger") or {}).get("total") or {}).get("cost_usd")
        found = sorted((logs / run).glob("*.eval"))
        if len(found) != 1:
            print(f"{run:48} {len(found)} eval logs in {logs / run}: skipped")
            continue
        free = free_memory()
        if free is not None and free < MIN_FREE:
            print(f"stopped before {run}: {free}% of memory is free, under {MIN_FREE}%", file=sys.stderr)
            return 2
        priced = priced_log(found[0])
        note = f"  ({priced['unpriced']} call(s) without a price)" if priced["unpriced"] else ""
        print(f"{run:48} {priced['calls']:>6} {priced['written_1h']:>10} {ledger or 0:>10.4f} "
              f"{priced['priced']:>10.4f} {priced['inspect']:>10.4f}{note}")
        sums["ledger"] += ledger or 0.0
        sums["priced"] += priced["priced"]
        sums["inspect"] += priced["inspect"]
    print(f"{args.study + ' (sums)':48} {'':>6} {'':>10} {sums['ledger']:>10.4f} {sums['priced']:>10.4f} "
          f"{sums['inspect']:>10.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
