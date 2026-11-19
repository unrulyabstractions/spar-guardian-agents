"""The ladder tables of the transcript viewer's #/ladders page, by task: level rows, task columns, one cell per task.

The Cheating table by task is the paper's own (src/measures/ladder_by_domain.py cheating_by_task).
Coordination has no table by task: its rule is the same on every task, and the page lists its levels with their
glosses (transcript_levels.LADDERS["coordination"]). Collusion's levels are listed the same way; what a take and the
hand-in are on each task is read from src/measures/collusion.py itself: the generic take rules from its docstring, and
each task's own rule from the docstrings of its TASK_RULES functions, or the reason NO_RULE gives.

The columns follow ladder_by_domain.DOMAINS: SWE first, then the tasks by their weight in the studies. The viewer
leaves out the domains of HIDDEN_DOMAINS: their columns here and their runs in the index (hidden_task). The paper's
tables keep every domain.

    ladders_payload(transcript_levels.LADDERS, transcript_levels.ORDER)   # what /api/ladders answers
    hidden_task("chess")                                                  # True: the index does not list its runs
"""
from __future__ import annotations

import inspect
import re

from src.measures import collusion, ladder_by_domain

HIDDEN_DOMAINS = ("Chess", "OpenMath", "Subset sum, factoring")
"""The domains the viewer leaves out, at the user's order of 2026-11-18: their columns of the by-task tables and their
runs in the index. An empty tuple shows every domain again."""
SHOWN = [i for i, domain in enumerate(ladder_by_domain.DOMAINS) if domain not in HIDDEN_DOMAINS]
"""The columns of ladder_by_domain.DOMAINS the viewer shows, in their order."""
assert set(ladder_by_domain.DOMAINS) >= set(HIDDEN_DOMAINS)

RULE_LINE = re.compile(r"^- (\w+): (.*)$")


def hidden_task(task: str | None) -> bool:
    """Whether the viewer leaves out a CheatBench task's runs. A task no domain names is shown."""
    return ladder_by_domain.TASK_DOMAIN.get(task or "") in HIDDEN_DOMAINS


def shown(cells) -> list:
    """One cell per domain, cut to the domains the viewer shows."""
    return [cells[i] for i in SHOWN]


def take_rules() -> list[tuple[str, str]]:
    """The generic take rules, (name, text), as src/measures/collusion.py's docstring lists them under "What a take
    is, per task": each "- name: text" line with its indented continuation lines."""
    doc = collusion.__doc__ or ""
    section = doc[doc.index("What a take is, per task"):].splitlines()[1:]
    out: list[tuple[str, str]] = []
    for line in section:
        m = RULE_LINE.match(line)
        if m:
            out.append((m.group(1), m.group(2)))
        elif line.startswith("  ") and out:
            out[-1] = (out[-1][0], f"{out[-1][1]} {line.strip()}")
        elif out:
            break
    return out


def _task_rule_cells(which: str) -> list[str]:
    """One cell per domain: the docstring of the task's TASK_RULES function (`which` is takes or hand_in), the reason
    NO_RULE gives for a task without one, or nothing for a domain no task of ladder_by_domain.TASK_DOMAIN names."""
    cells = []
    for domain in ladder_by_domain.DOMAINS:
        tasks = [t for t, d in ladder_by_domain.TASK_DOMAIN.items() if d == domain]
        rule = next((collusion.TASK_RULES[t] for t in tasks if t in collusion.TASK_RULES), None)
        if rule is not None:
            cells.append(" ".join(inspect.getdoc(getattr(rule, which)).split()))
        else:
            reason = next((collusion.NO_RULE[t] for t in tasks if t in collusion.NO_RULE), "no take rule")
            cells.append(f"Not applicable: {reason}." if which == "takes" else "Not applicable.")
    return cells


def ladders_payload(ladders: dict, order: list[str]) -> dict:
    """The #/ladders page's data: the Cheating ladder by task, the Collusion ladder's take rules (the generic ones
    and each task's take and hand-in) and the three ladders' definitions, with the columns of the domains the viewer
    shows. task_column gives a shown task's column."""
    domains = shown(ladder_by_domain.DOMAINS)
    by_task = ladder_by_domain.cheating_by_task(tuple(domains))
    return {"domains": domains, "cheating": {"notes": by_task["notes"], "rows": by_task["rows"]},
            "task_column": {task: domains.index(d) for task, d in ladder_by_domain.TASK_DOMAIN.items() if d in domains},
            "ladders": ladders, "order": order, "take_rules": take_rules(),
            "collusion_by_task": [("take", "what puts a teammate's work into the agent's own tree or files",
                                   shown(_task_rule_cells("takes"))),
                                  ("hand-in", "whose work the team's hand-in holds",
                                   shown(_task_rule_cells("hand_in")))],
            "hidden_domains": list(HIDDEN_DOMAINS)}
