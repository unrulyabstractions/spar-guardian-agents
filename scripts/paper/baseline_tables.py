"""The October baseline table: each team's Cheating and Collusion rungs as the transcript viewer measures them
(scripts/paper/study_ladders.py record), from runs/ian_baseline_explorations_0 at build time, its SWE runs from invalid/
on purpose: they served target selection only, so this table reads every October run of its cells whatever its validity.
The November runs of the same cells are the positive control table's (scripts/paper/control_tables.py). Writes
tmp/tables/results/swarm_vs_swarm_told.png. The single-agent controls come from scripts/paper/solo_controls_table.py.

    uv run python -m scripts.paper.baseline_tables    # make results-tables runs every one
"""
from __future__ import annotations

import sys

from matplotlib.patches import Rectangle

from scripts.paper.study_ladders import (
    CHEATING_LETTER,
    LEGEND_ROW,
    TILE,
    TILE_GAP,
    canvas,
    draw_legend,
    legend_items,
    legend_rows,
    record,
    run_tile,
    save,
    tile_height,
    tiles_width,
)
from src.configuration.settings import ROOT
from src.utils.figure_style import AXIS, BAND, INK, INK_SECONDARY

RUNS = ROOT / "runs" / "ian_baseline_explorations_0"
OUT = ROOT / "tmp" / "tables" / "results"
TASKS = [("SWE", "flipt"), ("Knowledge work", "worktime"), ("Protein design", "binder"),
         ("Creative writing", "poem"), ("Chess", "game")]
MODELS = [("DeepSeek V4.1 Flash", "dsflash"), ("GPT-6 Luna", "luna"), ("Claude Haiku 5.5", "haiku")]
SIDES = [("minimal", "untold"), ("minimal_cheat", "told to cheat")]


def team(stem: str, cell: str, model: str) -> list[dict]:
    """The three October runs of a cell. A missing run stops the build: a hole must not read as a low rung."""
    out = []
    for seed in (1, 2, 3):
        name = f"{stem}_{cell}_{model}{seed}"
        path = next((p for p in (RUNS / name, RUNS / "invalid" / name) if (p / "run.json").is_file()), None)
        if path is None:
            raise SystemExit(f"no run {name}")
        out.append(record(path))
    return out


def main() -> int:
    data = {(stem, m, cell): team(stem, cell, m) for _, stem in TASKS for _, m in MODELS for cell, _ in SIDES}
    for (stem, model, cell), runs in data.items():
        print(f"swarm_vs_swarm_told {stem:9s} {model:8s} {cell:14s}", "".join(CHEATING_LETTER[r["cheating"]]
                                                                              for r in runs))
    pad, left, col = 0.12, 1.85, tiles_width(3) + 0.45
    sub_h = tile_height() + 0.1
    row_h = len(SIDES) * sub_h + 0.12
    head_h = 0.36
    width = left + col * len(MODELS)
    legend = legend_rows(legend_items([r for runs in data.values() for r in runs], lines=False), width - 2 * pad)
    height = head_h + row_h * len(TASKS) + 0.12 + LEGEND_ROW * len(legend)
    fig, ax = canvas(width, height)
    ax.text(pad, head_h / 2, "Task", fontsize=8, weight="bold", color=INK, va="center")
    for j, (label, _) in enumerate(MODELS):
        ax.text(left + j * col, head_h / 2, label, fontsize=8, weight="bold", color=INK, va="center")
    ax.plot([0, width], [head_h, head_h], color=INK, lw=1.0)
    for i, (label, stem) in enumerate(TASKS):
        y0 = head_h + i * row_h
        if i % 2:
            ax.add_patch(Rectangle((0, y0), width, row_h, fc=BAND, ec="none", zorder=0))
        ax.text(pad, y0 + 0.2, label, fontsize=7.5, weight="bold", color=INK, va="center")
        for k, (cell, words) in enumerate(SIDES):
            y = y0 + 0.08 + k * sub_h
            ax.text(left - 0.12, y + TILE / 2, words, fontsize=7, style="italic", color=INK_SECONDARY, ha="right",
                    va="center")
            for j, (_, model) in enumerate(MODELS):
                for n, run in enumerate(data[(stem, model, cell)]):
                    run_tile(ax, left + j * col + n * (TILE + TILE_GAP), y, run)
        ax.plot([0, width], [y0 + row_h] * 2, color=AXIS, lw=0.6)
    draw_legend(ax, pad, head_h + row_h * len(TASKS) + 0.12 + LEGEND_ROW / 2, legend)
    print(save(fig, OUT / "swarm_vs_swarm_told.png"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
