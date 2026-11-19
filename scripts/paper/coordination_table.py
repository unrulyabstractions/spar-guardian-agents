"""The Coordination ladder by task for study_exploration_1's valid team runs: how many workers, teams and hidden
agents (regulators and disregulators) reach each level, with src.measures.coordination on each run's board. Valid runs
are the run index's rows marked valid and not mock. Writes tmp/tables/results/table_coordination_by_task.png.

    uv run python -m scripts.paper.coordination_table    # make results-tables runs every one
"""
from __future__ import annotations

import sys
from collections import Counter

from scripts.paper.study_ladders import STUDY, TASKS, counts_table, valid_runs
from src.configuration.settings import ROOT
from src.measures.coordination import LEVELS, run_coordination
from src.measures.run_directory import is_worker

OUT = ROOT / "tmp" / "tables" / "results" / "table_coordination_by_task.png"
BLOCKS = [("workers", "Workers", "agents"), ("team", "Teams", "runs"),
          ("hidden", "Regulators and disregulators", "agents")]


def counts() -> dict[tuple[str, str], Counter]:
    """(block, domain) -> Counter of levels, over the valid runs with at least two agents."""
    out: dict[tuple[str, str], Counter] = {(b, d): Counter() for b, _, _ in BLOCKS for d, _ in TASKS}
    domains = {d for d, _ in TASKS}
    for row in valid_runs():
        if row["domain"] not in domains:
            raise SystemExit(f"{row['path']}: domain {row['domain']!r} has no place in TASKS")
        run = run_coordination(STUDY / row["path"])
        if len(run["agents"]) < 2:
            continue
        d = row["domain"]
        for agent, measure in run["agents"].items():
            block = "workers" if is_worker(run["roles"].get(agent)) else "hidden"
            out[(block, d)][measure["level"]] += 1
        out[("team", d)][run["team"]] += 1
    return out


def main() -> int:
    data = counts()
    for (block, domain), c in data.items():
        print(f"{block:8s} {domain:17s} n={sum(c.values()):3d}", {level: c.get(level, 0) for level in LEVELS},
              f"none-applicable={c.get(None, 0)}")
    print(counts_table(OUT, LEVELS, data, BLOCKS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
