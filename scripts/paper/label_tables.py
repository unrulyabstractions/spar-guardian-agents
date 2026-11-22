"""The judge's two scores on ian_baseline_explorations_1's valid runs, as the measures read them
(src/measures/judge_labels.py labels through scripts/paper/study_ladders.py labels). No model is called. Writes
tmp/tables/results/table_label_scores_by_task.png: per label (judge_labels.LABELS: disclosure, manipulation), per
task, how many agents score 0, 1 to 49, 50 to 74 and 75 to 100, and how many the judge has not scored; workers and
influencers apart (study_ladders.record).

    uv run python -m scripts.paper.label_tables    # make results-tables runs every one

The bins only group the scores for the table. No rate is read from them, and the measures hold no threshold.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from scripts.paper.study_ladders import TASKS, counts_table, labels, record, valid_runs
from src.configuration.settings import ROOT
from src.measures.judge_labels import LABELS

OUT = ROOT / "tmp" / "tables" / "results" / "table_label_scores_by_task.png"
BINS = (("0", 0, 0), ("1-49", 1, 49), ("50-74", 50, 74), ("75-100", 75, 100))
"""(column, lowest score, highest score) of each column of scores."""
NOT_JUDGED = "not judged"
"""An agent the judge has not scored: the run has no labels file, or no row for the agent."""
COLUMNS = (*(name for name, _, _ in BINS), NOT_JUDGED)
ROLES = (("workers", "workers"), ("influencers", "influencers"))
BLOCKS = [(f"{label}/{role}", f"{label.capitalize()}, {title}", "agents") for label in LABELS for role, title in ROLES]


def column(score: int) -> str:
    """The column of BINS a score from 0 to 100 falls in."""
    for name, low, high in BINS:
        if low <= score <= high:
            return name
    raise SystemExit(f"score {score!r} is outside 0 to 100")


def runs() -> list[tuple[str, Path]]:
    """(domain, run folder) of every valid run."""
    domains = {d for d, _ in TASKS}
    out = []
    for listed in valid_runs():
        if listed.meta["domain"] not in domains:
            raise SystemExit(f"{listed.path.name}: domain {listed.meta['domain']!r} has no place in TASKS")
        out.append((listed.meta["domain"], listed.path))
    return out


def score_counts(found: list[tuple[str, Path]]) -> dict[tuple[str, str], Counter]:
    """(block, domain) -> Counter of COLUMNS over every agent of each run (record's agents), for each label."""
    out = {(block, d): Counter() for block, _, _ in BLOCKS for d, _ in TASKS}
    for domain, path in found:
        scored = labels(path)["agents"]
        for agent, r in record(path)["agents"].items():
            role = "workers" if r["worker"] else "influencers"
            for label in LABELS:
                value = (scored.get(agent) or {}).get(label)
                out[(f"{label}/{role}", domain)][NOT_JUDGED if value is None else column(value["score"])] += 1
    return out


def judges(found: list[tuple[str, Path]]) -> set[str]:
    """The judges whose scores the measures read on these runs; more than one stops the build."""
    named = {labels(path)["judge"] for _, path in found} - {None}
    if len(named) > 1:
        raise SystemExit(f"the study's scores come from several judges: {sorted(named)}")
    return named


def main() -> int:
    found = runs()
    print("judge:", sorted(judges(found)) or NOT_JUDGED)
    data = score_counts(found)
    for (block, domain), c in data.items():
        print(f"{block:24s} {domain:17s}", {level: c.get(level, 0) for level in COLUMNS})
    print(counts_table(OUT, COLUMNS, data, BLOCKS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
