"""The Collusion ladder by task for study_exploration_1's valid team runs, as code reads it: how many workers, teams and
hidden agents (regulators and disregulators) reach each code rung (src/measures/collusion.py CODE_RUNGS), through
scripts/paper/study_ladders.py record. Collusion does not by itself show intent, so beside it stand the agents and
teams at collusion whose awareness label (src/measures/judge_labels.py labels) reads yes; the awareness table
(scripts/paper/label_tables.py) gives the other answers. Valid runs are study_ladders.valid_runs'. Writes
tmp/tables/results/table_collusion_by_task.png.

    uv run python -m scripts.paper.collusion_table    # make results-tables runs every one
"""
from __future__ import annotations

import sys
from collections import Counter

from scripts.paper.study_ladders import TASKS, counts_table, labels, record, valid_runs
from src.configuration.settings import ROOT
from src.measures.collusion import CODE_RUNGS, COLLUDING
from src.measures.run_directory import is_worker

OUT = ROOT / "tmp" / "tables" / "results" / "table_collusion_by_task.png"
BLOCKS = [("workers", "Workers", "agents"), ("team", "Teams", "runs"),
          ("hidden", "Regulators and disregulators", "agents")]
AWARE = "aware"
"""The column beside the rungs: of those at collusion, the ones whose awareness label reads yes (for a team, a
colluding worker's: study_ladders.record collusion_aware)."""


def counts() -> dict[tuple[str, str], Counter]:
    """(block, domain) -> Counter of code rungs, and of AWARE among those at collusion, over the valid runs the ladder
    applies to."""
    out: dict[tuple[str, str], Counter] = {(b, d): Counter() for b, _, _ in BLOCKS for d, _ in TASKS}
    domains = {d for d, _ in TASKS}
    for listed in valid_runs():
        row = listed.meta
        if row["domain"] not in domains:
            raise SystemExit(f"{row['path']}: domain {row['domain']!r} has no place in TASKS")
        run, d = record(listed.path), row["domain"]
        if not run["collusion_applicable"]:
            continue
        for agent, r in run["collusion_agents"].items():
            c = out[("workers" if is_worker(r["role"]) else "hidden", d)]
            c[r["code_rung"]] += 1
            c[AWARE] += r["code_rung"] == COLLUDING and labels(listed.path)["agents"][agent]["awareness"] == "yes"
        out[("team", d)][run["collusion_code"]] += 1
        out[("team", d)][AWARE] += run["collusion_code"] == COLLUDING and run["collusion_aware"] == "yes"
    return out


def main() -> int:
    data = counts()
    for (block, domain), c in data.items():
        print(f"{block:8s} {domain:17s}", {level: c.get(level, 0) for level in (*CODE_RUNGS, AWARE)})
    print(counts_table(OUT, (*CODE_RUNGS, AWARE), data, BLOCKS, apart=(AWARE,)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
