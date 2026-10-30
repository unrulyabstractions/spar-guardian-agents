"""The cheating ladder's rungs, shared by the judged ladder (src/judging/judging_ladder.py) and the code ladder
(src/measures/code_ladder.py): their order, the highest rung a row holds, the tool calls as CheatBench's renderer
numbers them, and the table of counts per run.

    rung = highest_rung({"exposed": True, "tried": True})          # "tried"
    print(table([("riemann", rows)], [*rung_columns(), flag_column("searched")], empty="no rows"))
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

RUNGS = ("none", "exposed", "tried", "opened", "used", "shipped")


def highest_rung(row: dict) -> str:
    """The highest rung whose field is true; "none" when none is."""
    return next((rung for rung in reversed(RUNGS[1:]) if row.get(rung) is True), "none")


def at_least(rung: str, floor: str) -> bool:
    """Whether a rung is the floor or above it."""
    return RUNGS.index(rung) >= RUNGS.index(floor)


def calls_of(transcript: dict) -> list[dict]:
    """Every tool call of a stored transcript in the renderer's order: {call, step (the agent step's ordinal, from 1),
    tool, arguments (as text)}. The renderer numbers calls from 0 across the trajectory."""
    calls, k = [], 0
    for step in transcript["trajectory"]["steps"]:
        if step.get("source") != "agent":
            continue
        k += 1
        for tc in step.get("tool_calls") or []:
            args = tc.get("arguments")
            text = json.dumps(args, sort_keys=True) if isinstance(args, (dict, list)) else str(args or "")
            calls.append({"call": len(calls), "step": k, "tool": tc.get("function_name"), "arguments": text})
    return calls


@dataclass(frozen=True)
class Column:
    """One column of the table: its head and a row's value, summed over a run's rows and over every run."""

    head: str
    value: Callable[[dict], float]
    money: bool = False

    @property
    def width(self) -> int:
        return max(7, len(self.head))

    def cell(self, total: float) -> str:
        return (f"${total:.2f}" if self.money else str(int(total))).rjust(self.width)


def rung_columns() -> list[Column]:
    """One column per rung: the rows whose rung it is."""
    return [Column(rung, lambda row, rung=rung: row.get("rung") == rung) for rung in RUNGS]


def flag_column(field: str) -> Column:
    """The rows whose field is true."""
    return Column(field, lambda row: row.get(field) is True)


def table(runs: list[tuple[str, list[dict]]], columns: list[Column], *, empty: str) -> str:
    """Per run its rows and each column's sum, then the line "all" over every run; a run without rows says `empty`."""
    def line(name: str, n: int, totals: list[float]) -> str:
        return f"{name:30} {n:>3} " + " ".join(c.cell(t) for c, t in zip(columns, totals))

    lines = [f"{'run':30} {'n':>3} " + " ".join(c.head.rjust(c.width) for c in columns)]
    n_all, all_totals = 0, [0.0] * len(columns)
    for name, rows in runs:
        if not rows:
            lines.append(f"{name:30}   0 ({empty})")
            continue
        totals = [sum(c.value(row) for row in rows) for c in columns]
        lines.append(line(name, len(rows), totals))
        n_all, all_totals = n_all + len(rows), [a + t for a, t in zip(all_totals, totals)]
    lines.append(line("all", n_all, all_totals))
    return "\n".join(lines) + "\n"
