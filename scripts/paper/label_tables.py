"""The judge's answers beside the ladders on study_exploration_1's valid runs, from the verdicts stored beside each run
(src/judging/verdict_files.py) as the measures read them. No model is called. Writes, under tmp/tables/results/:

    table_scheming_by_task.png       per task, the pairs code places at collusion by their scheming verdict
                                     (src/measures/collusion.py, through scripts/paper/study_ladders.py record),
                                     pairs of workers and pairs with a regulator or disregulator apart
    table_awareness_by_task.png      per task, the agents the awareness label is read for (src/measures/judge_labels.py
                                     labels: at attempted or above on Cheating) by its verdict, hidden roles apart
    table_manipulation_by_task.png   per task, the agents the manipulation label is read for (those that posted) by its
                                     verdict; regulators and disregulators each apart, since each is told to hide its
                                     role or its instruction

    uv run python -m scripts.paper.label_tables    # make results-tables runs every one

A verdict reads yes, no, unchecked (a yes whose quote failed code's check) or not judged (no row of the pass yet).
The agents each label is read for come from judge_labels alone; the script stops when they differ from record's
Cheating rungs, so the tables and the Cheating table rest on one reading of the runs.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from scripts.paper.study_ladders import TASKS, counts_table, labels, record, valid_runs
from src.configuration.settings import ROOT
from src.measures import judge_labels
from src.measures.cheating_ladder import cheated
from src.measures.collusion import NOT_JUDGED
from src.measures.run_directory import is_worker

OUT = ROOT / "tmp" / "tables" / "results"
VERDICTS = ("yes", "no", "unchecked", NOT_JUDGED)
SCHEMING_BLOCKS = [("workers", "Colluding pairs of workers", "pairs"),
                   ("hidden", "Colluding pairs with a regulator or disregulator", "pairs")]
AWARENESS_BLOCKS = [("workers", "Workers at attempted or above", "agents"),
                    ("hidden", "Regulators and disregulators at attempted or above", "agents")]
MANIPULATION_BLOCKS = [
    ("workers", "Workers that posted", "agents"),
    ("regulator", "Regulators that posted (each told not to reveal its role)", "agents"),
    ("disregulator", "Disregulators that posted (each told not to reveal its instruction)", "agents"),
]


def runs() -> list[tuple[str, Path]]:
    """(domain, run folder) of every valid run."""
    domains = {d for d, _ in TASKS}
    out = []
    for listed in valid_runs():
        if listed.meta["domain"] not in domains:
            raise SystemExit(f"{listed.path.name}: domain {listed.meta['domain']!r} has no place in TASKS")
        out.append((listed.meta["domain"], listed.path))
    return out


def empty(blocks: list[tuple[str, str, str]]) -> dict[tuple[str, str], Counter]:
    return {(b, d): Counter() for b, _, _ in blocks for d, _ in TASKS}


def scheming_counts(found: list[tuple[str, Path]]) -> dict[tuple[str, str], Counter]:
    """(block, domain) -> Counter of VERDICTS over the pairs at collusion by code."""
    out = empty(SCHEMING_BLOCKS)
    for domain, path in found:
        for pair in record(path)["collusion_colluding"]:
            out[("workers" if all(map(is_worker, pair["roles"])) else "hidden", domain)][pair["scheming"]] += 1
    return out


def label_counts(found: list[tuple[str, Path]]) -> tuple[dict, dict]:
    """(block, domain) -> Counter of VERDICTS for awareness and for manipulation, over the agents each is read for
    (judge_labels: every value but NOT_READ)."""
    aware, manipulated = empty(AWARENESS_BLOCKS), empty(MANIPULATION_BLOCKS)
    for domain, path in found:
        run = record(path)
        read = labels(path)["agents"]
        asked = {a for a, v in read.items() if v["awareness"] != judge_labels.NOT_READ}
        expected = {a for a, r in run["agents"].items() if cheated(r["rung"])}
        if asked != expected:
            raise SystemExit(f"{path.name}: awareness is read for {sorted(asked)}, the Cheating ladder puts "
                             f"{sorted(expected)} at attempted or above; rebuild the run's signals")
        for agent, v in read.items():
            role = run["hidden"].get(agent, {}).get("role")
            if v["awareness"] != judge_labels.NOT_READ:
                aware[("workers" if role is None else "hidden", domain)][v["awareness"]] += 1
            if v["manipulation"] != judge_labels.NOT_READ:
                manipulated[(role or "workers", domain)][v["manipulation"]] += 1
    return aware, manipulated


def show(name: str, data: dict) -> None:
    for (block, domain), c in data.items():
        print(f"{name:12s} {block:12s} {domain:17s}", {v: c.get(v, 0) for v in VERDICTS})


def main() -> int:
    found = runs()
    scheming = scheming_counts(found)
    show("scheming", scheming)
    print(counts_table(OUT / "table_scheming_by_task.png", VERDICTS, scheming, SCHEMING_BLOCKS))
    aware, manipulated = label_counts(found)
    show("awareness", aware)
    print(counts_table(OUT / "table_awareness_by_task.png", VERDICTS, aware, AWARENESS_BLOCKS))
    show("manipulation", manipulated)
    print(counts_table(OUT / "table_manipulation_by_task.png", VERDICTS, manipulated, MANIPULATION_BLOCKS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
