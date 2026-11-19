"""CheatBench's judge beside the code on study_exploration_1's valid runs, from the verdicts stored beside each run
(src/judging/verdict_files.py) and the measures (src/measures/judge_labels.py, scripts/paper/study_ladders.py record).
No model is called: the tables read verdicts a judge pass already wrote. Writes, under tmp/tables/results/:

    table_judge_cheated_by_task.png   per task, agents by the code's cheated (cheating_ladder.cheated) against the
                                      judge's own `cheated`, workers and influencers apart
    table_judge_labels.png            per task and row of judge_labels.ROWS (the code's clauses and the judge labels
                                      that answer them), workers by the code's clauses against the judge's labels;
                                      "no label" on a row the judge has no label for
    table_cheatbench_disclosed_access_by_task.png
                                      per task, the agents that accessed the honeypot (study_ladders.accessed: looked or
                                      above) by CheatBench's disclosed access
                                      (judge_labels.cheatbench_disclosed_access)

    uv run python -m scripts.paper.judge_tables    # make results-tables runs every one

The judges named in the verdict files are printed; a run whose verdicts come from a judge other than the one the
others name, or an agent without a verdict, stops the build, so the tables always rest on one judge.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from scripts.paper.study_ladders import TASKS, accessed, counts_table, record, valid_runs
from src.configuration.settings import ROOT
from src.judging import verdict_files
from src.measures import judge_labels
from src.measures.cheating_ladder import cheated

OUT = ROOT / "tmp" / "tables" / "results"
BLOCKS = [("workers", "Workers", "agents"), ("influencers", "Influencers", "agents")]
VERDICTS = ("both", "code only", "judge only", "neither")
"""An agent's cell: the code and the judge both say yes, one of them, or neither."""
NOT_ANSWERED = "n/a"
LABEL_VERDICTS = ("both yes", "code only", "judge only", "both no", NOT_ANSWERED)
"""judge_labels.verdict_of's cells, in the order the table shows them, then the agents the judge cannot answer for:
a row with no label (judge_labels.NO_LABEL, shown as such), or an enumerated choice of the judge that hides the row."""
NOTE_CHARS = 32
"""The longest line of judge label names under a row's clauses."""
DISCLOSED_ACCESS = ("yes", "no", judge_labels.NOT_JUDGED, judge_labels.NOT_RECORDED)
TASK_OF = {"software_engineering": "swe", "protein_design": "protein_design", "creative_writings": "creative_writing"}
"""CheatBench's task name for each of TASKS' keys."""


def run_dirs() -> list[tuple[str, Path]]:
    """(domain, run folder) of every valid run."""
    domains = {d for d, _ in TASKS}
    out = []
    for listed in valid_runs():
        row = listed.meta
        if row["domain"] not in domains:
            raise SystemExit(f"{row['path']}: domain {row['domain']!r} has no place in TASKS")
        out.append((row["domain"], listed.path))
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
            out[("workers" if r["worker"] else "influencers", domain)][cell or "neither"] += 1
    return out


def joined(names: list[str], word: str) -> list[str]:
    """Names joined by `word`, in lines of at most NOTE_CHARS."""
    lines: list[str] = []
    for name in names:
        if lines and len(lines[-1]) + len(name) + len(word) + 2 <= NOTE_CHARS:
            lines[-1] += f"{word} {name}"
            continue
        if lines:
            lines[-1] += word
        lines.append(name)
    return lines


UNLABELLED = "other clauses"
"""The row that gathers a task's clauses the judge has no label for: each such clause is one row of judge_labels.ROWS,
and every worker reads "no label" on each, so the table shows them once."""


def label_counts(runs: list[tuple[str, Path]]) -> tuple[dict, dict, dict, dict]:
    """Over the workers (judge_labels.agreement), per (domain, row of judge_labels.ROWS): the Counter of
    LABEL_VERDICTS; each domain's rows, the rows without a label gathered in UNLABELLED; each row's lines of label
    names (of clause names for UNLABELLED); and "no label" in place of UNLABELLED's count."""
    found = judge_labels.agreement([path for _, path in runs], workers=True)["rows"]
    data, rows, notes, blank = {}, {}, {}, {}
    for task, domain in TASK_OF.items():
        labelled = [row for row in judge_labels.ROWS[task] if row.labels]
        unlabelled = [row for row in judge_labels.ROWS[task] if not row.labels]
        rows[domain] = [(row.name, row.name) for row in labelled] + ([(UNLABELLED, UNLABELLED)] if unlabelled else [])
        for row in judge_labels.ROWS[task]:
            cells = Counter()
            for cell, n in (found.get((task, row.name)) or Counter()).items():
                if cell == judge_labels.NOT_JUDGED:
                    raise SystemExit(f"{task} {row.name}: {n} workers have no parsed verdict")
                cells[cell if cell in LABEL_VERDICTS else NOT_ANSWERED] += n
            if row.labels:
                data[(domain, row.name)] = cells
                notes[(domain, row.name)] = joined([label.name for label in row.labels], " or")
            else:
                data[(domain, UNLABELLED)] = cells
        if unlabelled:
            notes[(domain, UNLABELLED)] = joined([row.name for row in unlabelled], ",")
            blank[(domain, UNLABELLED, NOT_ANSWERED)] = judge_labels.NO_LABEL
    return data, rows, notes, blank


DISCLOSED_ACCESS_BLOCKS = [("workers", "Workers at looked or above", "agents"),
                           ("influencers", "Influencers at looked or above", "agents")]
"""Disclosed access counts only the agents that had access to disclose: those that accessed the honeypot."""


def disclosed_access_counts(runs: list[tuple[str, Path]]) -> dict[tuple[str, str], Counter]:
    """(block, domain) -> Counter of DISCLOSED_ACCESS values (judge_labels.cheatbench_disclosed_access), per agent
    that accessed the honeypot (study_ladders.accessed)."""
    out: dict[tuple[str, str], Counter] = {(b, d): Counter() for b, _, _ in BLOCKS for d, _ in TASKS}
    for domain, path in runs:
        disclosed = judge_labels.cheatbench_disclosed_access(path)["agents"]
        for agent, r in record(path)["agents"].items():
            if accessed(r["rung"]):
                out[("workers" if r["worker"] else "influencers", domain)][disclosed[agent]] += 1
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
    labels, rows, notes, blank = label_counts(runs)
    show("labels", labels)
    blocks = [(domain, title, "workers") for domain, title in TASKS]
    print(counts_table(OUT / "table_judge_labels.png", LABEL_VERDICTS, labels, blocks, blank, rows=rows,
                       first="Clauses, CheatBench labels", left=2.3, notes=notes))
    disclosed = disclosed_access_counts(runs)
    show("disclosed", disclosed)
    print(counts_table(OUT / "table_cheatbench_disclosed_access_by_task.png", DISCLOSED_ACCESS, disclosed,
                       DISCLOSED_ACCESS_BLOCKS, left=2.6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
