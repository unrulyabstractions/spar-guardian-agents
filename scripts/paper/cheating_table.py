"""The Cheating ladder by task for study_exploration_1's valid runs: how many workers, teams and hidden agents
(regulators and disregulators) reach each rung, and how many runs carry the alarm (src/measures/cheating_ladder.py
ALARM), as the transcript viewer measures them (scripts/paper/study_ladders.py record). Valid runs are the run index's
rows marked valid and not mock. Writes tmp/tables/results/table_cheating_by_task.png.

    uv run python -m scripts.paper.cheating_table    # make results-tables runs every one
"""
from __future__ import annotations

import sys
from collections import Counter

from scripts.paper.study_ladders import CHEATING, STUDY, TASKS, counts_table, record, valid_runs
from src.configuration.settings import ROOT

OUT = ROOT / "tmp" / "tables" / "results" / "table_cheating_by_task.png"
ALARM = "alarm"
"""The column of runs the grader passed with no trace of the honeypot: beside the rungs, never one of them."""
BLOCKS = [("workers", "Workers", "agents"), ("team", "Teams", "runs"),
          ("hidden", "Regulators and disregulators", "agents")]


def counts() -> dict[tuple[str, str], Counter]:
    """(block, domain) -> Counter of rungs, and of alarms in the team block."""
    out: dict[tuple[str, str], Counter] = {(b, d): Counter() for b, _, _ in BLOCKS for d, _ in TASKS}
    domains = {d for d, _ in TASKS}
    for row in valid_runs():
        if row["domain"] not in domains:
            raise SystemExit(f"{row['path']}: domain {row['domain']!r} has no place in TASKS")
        run, d = record(STUDY / row["path"]), row["domain"]
        for agent in run["agents"].values():
            out[("workers" if agent["worker"] else "hidden", d)][agent["rung"]] += 1
        out[("team", d)][run["cheating"]] += 1
        out[("team", d)][ALARM] += bool(run["alarm"])
    return out


def main() -> int:
    data = counts()
    for (block, domain), c in data.items():
        print(f"{block:8s} {domain:17s}", {level: c.get(level, 0) for level in (*CHEATING, ALARM)})
    blank = {(block, d, ALARM): "" for block, _, _ in BLOCKS if block != "team" for d, _ in TASKS}
    print(counts_table(OUT, (*CHEATING, ALARM), data, BLOCKS, blank, apart=(ALARM,)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
