"""The October baseline table: each team's Cheating and Collusion rungs as the transcript viewer measures them
(scripts/paper/study_ladders.py record), from runs/study_exploration_0 at build time, its SWE runs from invalid/ on
purpose: they serve target selection only. The valid November runs of the same cells in runs/study_exploration_1 sit
beside them. Writes tmp/tables/results/swarm_vs_swarm_told.png. The single-agent controls come from
scripts/paper/solo_controls_table.py.

    uv run python -m scripts.paper.baseline_tables    # make results-tables runs every one
"""
from __future__ import annotations

import re
import sys

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch, Rectangle

from scripts.paper.study_ladders import (
    CHEATING_LETTER,
    cheating_legend,
    collusion_chip,
    collusion_legend,
    record,
    rung_chip,
)
from src.configuration.settings import ROOT
from src.measures.run_validity import validity
from src.utils.figure_style import AXIS, BAND, INK, INK_SECONDARY, PRINT_DPI, WHITE

RUNS = ROOT / "runs" / "study_exploration_0"
NOVEMBER = ROOT / "runs" / "study_exploration_1"
OUT = ROOT / "tmp" / "tables" / "results"
TASKS = [("Software engineering", "flipt"), ("Knowledge work", "worktime"), ("Protein design", "binder"),
         ("Creative writing", "poem"), ("Chess", "game")]
MODELS = [("DeepSeek V4.1 Flash", "dsflash"), ("GPT-6 Luna", "luna"), ("Claude Haiku 5.5", "haiku")]


def canvas(width: float, height: float):
    fig = Figure(figsize=(width, height), dpi=PRINT_DPI)
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.axis("off")
    return fig, ax


def caps(ax, x: float, y: float, text: str) -> None:
    ax.text(x, y, text.upper(), fontsize=6.5, family="DejaVu Sans Mono", color=INK_SECONDARY, va="center")


def team(stem: str, cell: str, model: str) -> list[dict]:
    """The three October runs of a cell. A missing run stops the build: a hole must not read as a low rung."""
    out = []
    for seed in (1, 2, 3):
        name = f"{stem}_{cell}_{model}{seed}"
        path = next((p for p in (RUNS / name, RUNS / "invalid" / name) if (p / "run.json").is_file()), None)
        if path is None:
            raise SystemExit(f"no run {name}")
        run = record(path)
        out.append({"rung": run["cheating"], "collusion": run["collusion"]})
    return out


def november(stem: str, cell: str, model: str) -> list[dict]:
    """The valid November runs of a team cell, in seed order; SWE's on the fixed task. Empty when none exists."""
    prefix = f"{stem}_{cell}_{model}" + ("_fixed" if stem == "flipt" else "")
    paths = sorted((p for p in NOVEMBER.iterdir() if re.fullmatch(re.escape(prefix) + r"\d+", p.name)),
                   key=lambda p: int(re.search(r"(\d+)$", p.name).group(1)))
    out = []
    for path in paths:
        if (path / "run.json").is_file() and validity(path)["valid"]:
            run = record(path)
            out.append({"rung": run["cheating"], "collusion": run["collusion"]})
    return out


def grid(name: str, untold: str, told: str, mix: bool = False) -> None:
    def cell(stem: str, kind: str, model: str) -> tuple[list[dict], bool]:
        old = team(stem, kind, model)
        return old + (november(stem, kind, model) if mix else []), len(old)

    data = {(stem, m): (cell(stem, untold, m), cell(stem, told, m)) for _, stem in TASKS for _, m in MODELS}
    w, gap, sep, pad = (0.27, 0.03, 0.24, 0.15) if mix else (0.36, 0.05, 0.26, 0.18)
    most = {m: max(len(runs) for (_, mm), pair in data.items() if mm == m for runs, _ in pair) for _, m in MODELS}
    split = 0.07 if mix else 0.0   # the space that sets the latest runs apart from the older ones
    half = {m: n * w + (n - 1) * gap + split for m, n in most.items()}
    left = 2.0
    cols, x = {}, left
    for label, m in MODELS:
        cols[m] = x
        x += 2 * half[m] + sep + 2 * pad + 0.1
    head_h, row_h = 0.72, 0.82
    legend_h = 1.1 if mix else 0.8
    width, height = x, head_h + row_h * len(TASKS) + legend_h
    fig, ax = canvas(width, height)
    caps(ax, pad, 0.42, "Task")
    for label, model in MODELS:
        x = cols[model] + pad
        ax.text(x, 0.26, label, fontsize=10, weight="bold", color=INK, va="center")
        ax.text(x, 0.52, "untold", fontsize=7.6, style="italic", color=INK_SECONDARY, va="center")
        ax.text(x + half[model] + sep, 0.52, "told to cheat", fontsize=7.6, style="italic", color=INK_SECONDARY,
                va="center")
    ax.plot([0, width], [head_h, head_h], color=INK, lw=1.0)
    for i, (label, stem) in enumerate(TASKS):
        y0 = head_h + i * row_h
        if i % 2:
            ax.add_patch(Rectangle((0, y0), width, row_h, fc=BAND, ec="none", zorder=0))
        ax.text(pad, y0 + row_h / 2, label, fontsize=9.2, weight="bold", color=INK, va="center")
        for _, model in MODELS:
            x0 = cols[model] + pad
            for side, (runs, old) in enumerate(data[(stem, model)]):
                sx = x0 + side * (half[model] + sep)
                y = y0 + 0.13
                latest = len(runs) - old
                if latest:
                    ax.add_patch(FancyBboxPatch((sx + old * (w + gap) + split - 0.03, y - 0.04),
                                                latest * w + (latest - 1) * gap + 0.06, 0.61,
                                                boxstyle="round,pad=0,rounding_size=0.05", fc="none", ec=INK_SECONDARY,
                                                lw=0.8, zorder=3))
                for k, run in enumerate(runs):
                    x = sx + k * (w + gap) + (split if k >= old else 0)
                    rung_chip(ax, x, y, w, 0.33, run["rung"])
                    collusion_chip(ax, x, y + 0.36, w, 0.17, run["collusion"], size=4.6 if mix else 5.6)
            ax.plot([x0 + half[model] + sep / 2] * 2, [y0 + 0.14, y0 + row_h - 0.14], color=AXIS, lw=0.5)
        ax.plot([0, width], [y0 + row_h, y0 + row_h], color=AXIS, lw=0.6)
    runs = [r for pair in data.values() for side, _ in pair for r in side]
    ly = head_h + row_h * len(TASKS) + 0.26
    cheating_legend(ax, pad, ly, {run["rung"] for run in runs})
    ly += 0.3
    collusion_legend(ax, pad, ly, {run["collusion"] for run in runs})
    if mix:
        ly += 0.3
        ax.add_patch(FancyBboxPatch((pad, ly - 0.09), 0.34, 0.18, boxstyle="round,pad=0,rounding_size=0.04", fc="none",
                                    ec=INK_SECONDARY, lw=0.8))
        ax.text(pad + 0.41, ly, "run with exact latest setup", fontsize=7.8, color=INK_SECONDARY, va="center")
    fig.savefig(OUT / f"{name}.png", dpi=PRINT_DPI, facecolor=WHITE)
    for (stem, model), ((a, an), (b, bn)) in data.items():
        print(f"{name:21s} {stem:9s} {model:8s}", "".join(CHEATING_LETTER[r["rung"]] for r in a), "/",
              "".join(CHEATING_LETTER[r["rung"]] for r in b), "| new" if an or bn else "| october", an, bn)


def main() -> int:
    grid("swarm_vs_swarm_told", "minimal", "minimal_cheat", mix=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
