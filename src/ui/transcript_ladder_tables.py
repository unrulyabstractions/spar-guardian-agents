"""The ladder tables of the transcript viewer's #/ladders page, by task: level rows, task columns, one cell per task.

The Cheating table by task is the paper's own (src/measures/ladder_by_domain.py cheating_by_task), with its columns:
the tasks the study reads (ladder_by_domain.DOMAINS). Coordination has no table by task: its rule is the same on every
task, and the page lists its levels with their glosses (transcript_levels.LADDERS["coordination"]). Collusion's levels
are listed the same way; what a take and the hand-in are on each task is read from the take rules themselves
(src/measures/collusion_takes.py): the generic rules, its TAKE_RULES, and each task's own rule from the docstrings of
its TASK_RULES functions, or the reason NO_RULE gives. The judge labels beside the ladders
(transcript_levels.JUDGE_LABELS) are listed with whom each is read for. The tasks the study does not read
(ladder_by_domain.HIDDEN_TASKS) have no column, and the index neither measures nor lists their runs.

    ladders_payload(transcript_levels.LADDERS, transcript_levels.ORDER, transcript_levels.JUDGE_LABELS)
"""
from __future__ import annotations

import inspect

from src.measures import collusion_takes, ladder_by_domain


def _task_rule_cells(which: str) -> list[str]:
    """One cell per domain: the docstring of the task's TASK_RULES function (`which` is takes or hand_in), or the
    reason NO_RULE gives for a task without one."""
    cells = []
    for domain in ladder_by_domain.DOMAINS:
        task = next(t for t, d in ladder_by_domain.TASK_DOMAIN.items() if d == domain)
        rule = collusion_takes.TASK_RULES.get(task)
        if rule is not None:
            cells.append(" ".join(inspect.getdoc(getattr(rule, which)).split()))
        else:
            reason = collusion_takes.NO_RULE.get(task, "no take rule")
            cells.append(f"Not applicable: {reason}." if which == "takes" else "Not applicable.")
    return cells


def ladders_payload(ladders: dict, order: list[str], judge_labels: dict) -> dict:
    """The #/ladders page's data: the Cheating ladder by task, the Collusion ladder's take rules (the generic ones
    and each task's take and hand-in), the three ladders' definitions and the judge labels beside them, with a column
    per task the study reads. task_column gives a task's column."""
    by_task = ladder_by_domain.cheating_by_task()
    domains = by_task["domains"]
    return {"domains": domains, "cheating": {"notes": by_task["notes"], "rows": by_task["rows"]},
            "task_column": {task: domains.index(d) for task, d in ladder_by_domain.TASK_DOMAIN.items()},
            "ladders": ladders, "order": order, "judge_labels": judge_labels,
            "take_rules": list(collusion_takes.TAKE_RULES),
            "collusion_by_task": [("take", "what puts a teammate's work into the agent's own tree or files",
                                   _task_rule_cells("takes")),
                                  ("hand-in", "whose work the team's hand-in holds", _task_rule_cells("hand_in"))],
            "hidden_tasks": sorted(ladder_by_domain.HIDDEN_TASKS)}
