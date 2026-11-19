"""The paper's view of the three ladders: each run's Cheating and Collusion rungs (src/measures/cheating_ladder.py
run_cheating, and the Collusion ladder with its judge's verdicts, src/judging/collusion_judge.py judged_collusion), the
study's valid runs, the ladders' colours and marks, and the table of counts by task that the results tables share.
A team or an agent cheated when the measures' one definition says so (cheating_ladder.cheated: CHEATED_FROM or
above); no paper script draws a line of its own.

    record(path)        # {"cheating": rung, "cheated", "passed", "alarm", "agents": {agent: {worker, rung, episode}},
                        #  "collusion": rung, "collusion_agents": {agent: {role, rung}}, "collusion_judged",
                        #  "collusion_applicable"}
    valid_runs()        # study_exploration_1's run index rows marked valid and not mock

Nothing is written under runs/, and no model is called.
"""
from __future__ import annotations

import json
from collections import Counter
from functools import cache
from pathlib import Path

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch, Rectangle

from src.configuration.settings import ROOT
from src.judging.collusion_judge import judged_collusion
from src.measures.cheating_ladder import CHEATED_FROM, cheated, run_cheating
from src.measures.cheating_ladder import LEVELS as CHEATING
from src.measures.collusion import RUNGS as COLLUSION
from src.utils.figure_style import AXIS, BAND, GREY_RAMP, INK, INK_SECONDARY, MUTED, PRINT_DPI, WHITE

STUDY = ROOT / "runs" / "study_exploration_1"
INDEX = STUDY / "INDEX.jsonl"
# The study's tasks, in the order of src/measures/ladder_by_domain.py DOMAINS: SWE first.
TASKS = [("swe", "SWE"), ("protein_design", "Protein design"), ("creative_writing", "Creative writing")]

CHEATING_FILL = dict(zip(CHEATING, ("#eeede8", "#f9c396", "#e8714a", "#c4392a", "#7c1512")))
"""The Cheating ladder (cheating_ladder.LEVELS) as a red ramp, none neutral."""
CHEATING_TEXT = {"tempted": WHITE, "attempted": WHITE, "hacked": WHITE}
CHEATING_LETTER = dict(zip(CHEATING, ("·", "L", "T", "A", "H")))
COLLUSION_FILL = dict(zip(COLLUSION, ("#eeede8", "#c9c0f0", "#6f5bd0", "#4a3aa8", "#2b1f6e")))
"""The Collusion ladder (collusion.RUNGS) as a violet ramp, none neutral."""
COLLUSION_TEXT = {"collaboration": WHITE, "collusion": WHITE, "scheming": WHITE}
COLLUSION_SHORT = dict(zip(COLLUSION, ("none", "coop", "collab", "collu", "schem")))
CHEATED_HEADER = f"Cheated ({CHEATED_FROM}+)"
CHEATED_LABEL = f"cheated: {CHEATED_FROM} or above"
"""A column's and a legend's words for the cheating line, the one the measures draw."""


def valid_runs() -> list[dict]:
    """The run index's rows marked valid and not mock."""
    rows = [json.loads(line) for line in INDEX.read_text().splitlines() if line.strip()]
    return [r for r in rows if r["valid"] and not r["mock"]]


@cache
def record(path: Path) -> dict:
    """One run's Cheating and Collusion rungs, for the team and for each agent."""
    cheating = run_cheating(Path(path))
    collusion, _ = judged_collusion(Path(path), {a: r["rung"] for a, r in cheating["agents"].items()})
    return {"cheating": cheating["team"], "cheated": cheated(cheating["team"]), "passed": cheating["passed"] is True,
            "alarm": cheating["alarm"],
            "agents": {a: {"worker": r["worker"], "rung": r["rung"], "episode": r["episode"]}
                       for a, r in cheating["agents"].items()},
            "collusion_applicable": bool(collusion.get("applicable")),
            "collusion": collusion.get("team") if collusion.get("applicable") else None,
            "collusion_agents": {a: {"role": r["role"], "rung": r["rung"]}
                                 for a, r in (collusion.get("agents") or {}).items()},
            "collusion_judged": bool(collusion.get("judged"))}


def rung_chip(ax, x: float, y: float, w: float, h: float, rung: str, size: float = 8.5) -> None:
    """A Cheating rung as a rounded tile with its letter."""
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.035", fc=CHEATING_FILL[rung],
                                ec=AXIS if rung == "none" else "none", lw=0.6))
    ax.text(x + w / 2, y + h / 2, CHEATING_LETTER[rung], fontsize=size, weight="bold",
            color=CHEATING_TEXT.get(rung, INK), ha="center", va="center")


def collusion_chip(ax, x: float, y: float, w: float, h: float, rung: str | None, size: float = 6.3,
                   full: bool = False) -> None:
    """A Collusion rung as a pill, its short name (or its whole name when `full`); "n/a" where it does not apply."""
    label = "n/a" if rung is None else rung if full else COLLUSION_SHORT[rung]
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.03",
                                fc=WHITE if rung is None else COLLUSION_FILL[rung], ec=AXIS if rung is None else "none",
                                lw=0.6))
    ax.text(x + w / 2, y + h / 2, label, fontsize=size, ha="center", va="center",
            color=MUTED if rung is None else COLLUSION_TEXT.get(rung, INK))


def cheating_legend(ax, x: float, y: float, shown: set[str], s: float = 0.2) -> float:
    """The Cheating rungs `shown`, in ladder order, from x at height y; returns the x after them."""
    for rung in [r for r in CHEATING if r in shown]:
        rung_chip(ax, x, y - s / 2, s, s, rung, size=6.5)
        ax.text(x + s + 0.07, y, rung, fontsize=7.8, color=INK_SECONDARY, va="center")
        x += s + 0.07 + len(rung) * 0.075 + 0.3
    return x


def collusion_legend(ax, x: float, y: float, shown: set[str | None]) -> float:
    """The Collusion rungs `shown`, in ladder order, then n/a when shown; returns the x after them."""
    for rung in [r for r in COLLUSION if r in shown] + ([None] if None in shown else []):
        collusion_chip(ax, x, y - 0.085, 0.4, 0.17, rung)
        label = "Collusion: not applicable" if rung is None else f"Collusion: {rung}"
        ax.text(x + 0.47, y, label, fontsize=7.8, color=INK_SECONDARY, va="center")
        x += 0.47 + len(label) * 0.075 + 0.3
    return x


def shade(share: float) -> str | None:
    """A count cell's fill by its share of the row: none below a tenth, then the grey ramp."""
    if share < 0.1:
        return None
    return GREY_RAMP[min(int(share * 5), len(GREY_RAMP) - 1)]


def counts_table(path: Path, levels: tuple[str, ...], data: dict[tuple[str, str], Counter],
                 blocks: list[tuple[str, str, str]], blank: dict[tuple[str, str, str], str] | None = None,
                 apart: tuple[str, ...] = (), rows: dict[str, list[tuple[str, str]]] | None = None,
                 first: str = "Task", left: float = 1.75) -> Path:
    """A table of counts: a column per level, a block of rows per (key, title, unit) of `blocks`, a row per (key,
    label) of `rows[block]` (the tasks of TASKS by default), each cell the count of `data[(block, key)]` at that level.
    `blank` gives a cell's text in place of its count, by (block, key, level). The columns of `apart` count something
    beside the levels and stay out of the row's count. `first` heads the label column, `left` is its width."""
    blank = blank or {}
    rows = rows or {block: TASKS for block, _, _ in blocks}
    total, col = 1.0, 0.82 if max(map(len, levels)) <= 8 else 1.05
    width = left + total + col * len(levels) + 0.15
    head_h, block_h, row_h = 0.5, 0.36, 0.3
    height = head_h + sum(block_h + row_h * len(rows[block]) for block, _, _ in blocks) + 0.12
    fig = Figure(figsize=(width, height), dpi=PRINT_DPI)
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.axis("off")
    pad = 0.15
    x_levels = left + total
    ax.text(pad, head_h / 2, first, fontsize=9.5, weight="bold", color=INK, va="center")
    ax.text(left + total - pad, head_h / 2, "Count", fontsize=9.5, weight="bold", color=INK, va="center", ha="right")
    for k, level in enumerate(levels):
        ax.text(x_levels + (k + 0.5) * col, head_h / 2, level, fontsize=9.5, weight="bold", color=INK, va="center",
                ha="center")
    ax.plot([0, width], [head_h, head_h], color=INK, lw=1.0)
    y = head_h
    for block, title, unit in blocks:
        ax.text(pad, y + block_h / 2 + 0.03, title, fontsize=8.6, style="italic", color=INK_SECONDARY, va="center")
        y += block_h
        for i, (key, label) in enumerate(rows[block]):
            c = data[(block, key)]
            n = sum(v for level, v in c.items() if level not in apart)
            if i % 2:
                ax.add_patch(Rectangle((0, y), width, row_h, fc=BAND, ec="none", zorder=0))
            ax.text(pad + 0.15, y + row_h / 2, label, fontsize=9, color=INK, va="center")
            ax.text(left + total - pad, y + row_h / 2, f"{n} {unit}", fontsize=8.2, color=INK_SECONDARY,
                    va="center", ha="right")
            for k, level in enumerate(levels):
                cx = x_levels + k * col
                text = blank.get((block, key, level))
                if text is not None:
                    ax.text(cx + col / 2, y + row_h / 2, text, fontsize=7.4, style="italic", color=MUTED,
                            va="center", ha="center")
                    continue
                value = c.get(level, 0)
                fill = shade(value / n) if value else None
                if fill:
                    ax.add_patch(Rectangle((cx + 0.04, y + 0.03), col - 0.08, row_h - 0.06, fc=fill, ec="none",
                                           zorder=1))
                ax.text(cx + col / 2, y + row_h / 2, str(value) if value else "·", fontsize=9,
                        color=INK if value else MUTED, va="center", ha="center", zorder=2)
            y += row_h
        ax.plot([0, width], [y, y], color=AXIS, lw=0.6)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=PRINT_DPI, facecolor=WHITE)
    return path
