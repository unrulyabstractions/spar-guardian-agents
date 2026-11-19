"""The Collusion ladder by task for study_exploration_1's valid team runs, as code reads it: how many workers, teams and
influencers reach each rung (src/measures/collusion.py RUNGS), through scripts/paper/study_ladders.py record. Valid
runs are study_ladders.valid_runs'. Writes tmp/tables/results/table_collusion_by_task.png.

    uv run python -m scripts.paper.collusion_table    # make results-tables runs every one
"""
from __future__ import annotations

import sys
from collections import Counter

from scripts.paper.study_ladders import TASKS, counts_table, record, valid_runs
from src.configuration.settings import ROOT
from src.measures.collusion import RUNGS
from src.measures.run_directory import is_influencer

OUT = ROOT / "tmp" / "tables" / "results" / "table_collusion_by_task.png"
BLOCKS = [("workers", "Workers", "agents"), ("team", "Teams", "runs"),
          ("influencers", "Influencers", "agents")]


def counts() -> dict[tuple[str, str], Counter]:
    """(block, domain) -> Counter of rungs over the valid runs the ladder applies to."""
    out: dict[tuple[str, str], Counter] = {(b, d): Counter() for b, _, _ in BLOCKS for d, _ in TASKS}
    domains = {d for d, _ in TASKS}
    for listed in valid_runs():
        row = listed.meta
        if row["domain"] not in domains:
            raise SystemExit(f"{row['path']}: domain {row['domain']!r} has no place in TASKS")
        run, d = record(listed.path), row["domain"]
        if not run["collusion_applicable"]:
            continue
        for r in run["collusion_agents"].values():
            out[("influencers" if is_influencer(r["role"]) else "workers", d)][r["rung"]] += 1
        out[("team", d)][run["collusion"]] += 1
    return out


def main() -> int:
    data = counts()
    for (block, domain), c in data.items():
        print(f"{block:8s} {domain:17s}", {level: c.get(level, 0) for level in RUNGS})
    print(counts_table(OUT, RUNGS, data, BLOCKS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
