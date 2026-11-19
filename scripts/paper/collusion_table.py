"""The Collusion ladder by task for study_exploration_1's valid team runs: how many workers, teams and hidden agents
(regulators and disregulators) reach each rung, as the transcript viewer measures them (scripts/paper/study_ladders.py
record): code's rungs, and a collusion judge's where its verdicts are stored. The judge's rungs read "not judged" in a
row of runs no judge read. Valid runs are the run index's rows marked valid and not mock. Writes
tmp/tables/results/table_collusion_by_task.png.

    uv run python -m scripts.paper.collusion_table    # make results-tables runs every one
"""
from __future__ import annotations

import sys
from collections import Counter

from scripts.paper.study_ladders import COLLUSION, STUDY, TASKS, counts_table, record, valid_runs
from src.configuration.settings import ROOT
from src.measures.collusion import CODE_RUNGS
from src.measures.run_directory import is_worker

OUT = ROOT / "tmp" / "tables" / "results" / "table_collusion_by_task.png"
BLOCKS = [("workers", "Workers", "agents"), ("team", "Teams", "runs"),
          ("hidden", "Regulators and disregulators", "agents")]
JUDGE_RUNGS = COLLUSION[len(CODE_RUNGS):]


def counts() -> tuple[dict[tuple[str, str], Counter], dict[str, int]]:
    """(block, domain) -> Counter of rungs over the valid runs the ladder applies to, and the judged runs by domain."""
    out: dict[tuple[str, str], Counter] = {(b, d): Counter() for b, _, _ in BLOCKS for d, _ in TASKS}
    judged = Counter()
    domains = {d for d, _ in TASKS}
    for row in valid_runs():
        if row["domain"] not in domains:
            raise SystemExit(f"{row['path']}: domain {row['domain']!r} has no place in TASKS")
        run, d = record(STUDY / row["path"]), row["domain"]
        if not run["collusion_applicable"]:
            continue
        judged[d] += run["collusion_judged"]
        for agent in run["collusion_agents"].values():
            out[("workers" if is_worker(agent["role"]) else "hidden", d)][agent["rung"]] += 1
        out[("team", d)][run["collusion"]] += 1
    return out, judged


def main() -> int:
    data, judged = counts()
    for (block, domain), c in data.items():
        print(f"{block:8s} {domain:17s}", {level: c.get(level, 0) for level in COLLUSION})
    print("runs a collusion judge read:", dict(judged))
    blank = {(block, d, level): "not judged" for block, _, _ in BLOCKS for d, _ in TASKS if not judged[d]
             for level in JUDGE_RUNGS}
    print(counts_table(OUT, COLLUSION, data, BLOCKS, blank))
    return 0


if __name__ == "__main__":
    sys.exit(main())
