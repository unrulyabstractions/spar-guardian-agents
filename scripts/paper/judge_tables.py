"""CheatBench's judge beside the code on study_exploration_1's valid runs, from the verdicts stored beside each run
(src/judging/verdict_files.py) and the measures (src/measures/judge_labels.py, scripts/paper/study_ladders.py record).
No model is called: the tables read verdicts a judge pass already wrote. Writes, under tmp/tables/results/:

    table_judge_cheated_by_task.png   per task, agents by the code's cheated (cheating_ladder.cheated) against the
                                      judge's own `cheated`, workers and hidden roles apart
    table_judge_labels.png            per task and judge label (judge_labels.LABELS), workers by the code's clauses
                                      against the judge's label
    table_disclosure_by_task.png      per task, agents by the judge's disclosure (judge_labels.disclosure)

    uv run python -m scripts.paper.judge_tables    # make results-tables runs every one

The judges named in the verdict files are printed; a run whose verdicts come from a judge other than the one the
others name, or an agent without a verdict, stops the build, so the tables always rest on one judge.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from scripts.paper.study_ladders import STUDY, TASKS, counts_table, record, valid_runs
from src.configuration.settings import ROOT
from src.judging import verdict_files
from src.measures import judge_labels
from src.measures.cheating_ladder import cheated

OUT = ROOT / "tmp" / "tables" / "results"
BLOCKS = [("workers", "Workers", "agents"), ("hidden", "Regulators and disregulators", "agents")]
VERDICTS = ("both", "code only", "judge only", "neither")
"""An agent's cell: the code and the judge both say yes, one of them, or neither."""
LABEL_VERDICTS = ("both yes", "code only", "judge only", "both no")
"""judge_labels.verdict_of's cells, in the order the table shows them."""
DISCLOSURE = ("yes", "no", judge_labels.NOT_JUDGED, judge_labels.NOT_RECORDED)
TASK_OF = {"software_engineering": "swe", "protein_design": "protein_design", "creative_writings": "creative_writing"}
"""CheatBench's task name for each of TASKS' keys."""


def run_dirs() -> list[tuple[str, Path]]:
    """(domain, run folder) of every valid run."""
    domains = {d for d, _ in TASKS}
    out = []
    for row in valid_runs():
        if row["domain"] not in domains:
            raise SystemExit(f"{row['path']}: domain {row['domain']!r} has no place in TASKS")
        out.append((row["domain"], STUDY / row["path"]))
    return out


def judge_of(runs: list[tuple[str, Path]]) -> str:
    """The one judge whose verdicts the measures take on every run."""
    judges = Counter(verdict_files.applied(verdict_files.cheatbench(path)) for _, path in runs)
    print("CheatBench judge by run:", dict(judges))
    if len(judges) != 1 or None in judges:
        raise SystemExit(f"the runs' verdicts do not come from one judge: {dict(judges)}")
    return next(iter(judges))


def cheated_counts(runs: list[tuple[str, Path]]) -> dict[tuple[str, str], Counter]:
    """(block, domain) -> Counter of VERDICTS: the code's cheated against the judge's `cheated`, per agent."""
    out: dict[tuple[str, str], Counter] = {(b, d): Counter() for b, _, _ in BLOCKS for d, _ in TASKS}
    for domain, path in runs:
        verdicts = verdict_files.episode_verdicts(path)
        for agent, r in record(path)["agents"].items():
            verdict = judge_labels.parsed(verdicts.get(r["episode"]))
            if verdict is None:
                raise SystemExit(f"{path.name}: agent {agent} has no parsed verdict")
            code, judge = cheated(r["rung"]), bool(verdict["cheated"])
            cell = {(True, True): "both", (True, False): "code only", (False, True): "judge only"}.get((code, judge))
            out[("workers" if r["worker"] else "hidden", domain)][cell or "neither"] += 1
    return out


def label_counts(runs: list[tuple[str, Path]]) -> tuple[dict[tuple[str, str], Counter], dict[str, list]]:
    """(domain, label) -> Counter of LABEL_VERDICTS over the workers (judge_labels.agreement), and each domain's
    rows of labels."""
    found = judge_labels.agreement([path for _, path in runs], workers=True)["labels"]
    data = {(TASK_OF[task], label): c for (task, label), c in found.items()}
    rows = {domain: [(label.name, label.name) for label in judge_labels.LABELS[task]]
            for task, domain in TASK_OF.items()}
    for domain, labels in rows.items():
        for key, _ in labels:
            data.setdefault((domain, key), Counter())
    return data, rows


def disclosure_counts(runs: list[tuple[str, Path]]) -> dict[tuple[str, str], Counter]:
    """(block, domain) -> Counter of DISCLOSURE values, per agent (judge_labels.disclosure)."""
    out: dict[tuple[str, str], Counter] = {(b, d): Counter() for b, _, _ in BLOCKS for d, _ in TASKS}
    for domain, path in runs:
        result = judge_labels.disclosure(path)
        out[("workers", domain)].update(result["team"])
        out[("hidden", domain)].update(result["hidden"].values())
    return out


def show(name: str, data: dict) -> None:
    for key, c in sorted(data.items()):
        print(f"{name:10s} {key[0]:17s} {key[1]:32s}", dict(c))


def main() -> int:
    runs = run_dirs()
    print("judge:", judge_of(runs))
    both = cheated_counts(runs)
    show("cheated", both)
    print(counts_table(OUT / "table_judge_cheated_by_task.png", VERDICTS, both, BLOCKS))
    labels, rows = label_counts(runs)
    show("labels", labels)
    blocks = [(domain, title, "workers") for domain, title in TASKS]
    print(counts_table(OUT / "table_judge_labels.png", LABEL_VERDICTS, labels, blocks, rows=rows,
                       first="Judge label", left=2.75))
    disclosed = disclosure_counts(runs)
    show("disclosed", disclosed)
    print(counts_table(OUT / "table_disclosure_by_task.png", DISCLOSURE, disclosed, BLOCKS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
