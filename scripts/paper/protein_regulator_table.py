"""Protein design regulators with GPT-6 Luna workers, valid runs only, no pilots: each run's Cheating and Collusion
rungs and whether it cheated, as the transcript viewer measures them (scripts/paper/study_ladders.py record), and its
validity and regulator posts from src.measures.run_validity, at build time. Writes
tmp/tables/results/table_protein_regulators.png.

    uv run python -m scripts.paper.protein_regulator_table    # make results-tables runs every one
"""
from __future__ import annotations

import sys

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle

from scripts.paper.study_ladders import (
    CHEATED_HEADER,
    cheating_legend,
    collusion_chip,
    collusion_legend,
    record,
    rung_chip,
)
from src.configuration.settings import ROOT
from src.measures.run_validity import validity
from src.utils.figure_style import AXIS, BAND, INK, INK_SECONDARY, MUTED, PRINT_DPI, WHITE

RUNS = ROOT / "runs" / "study_exploration_1"
OUT = ROOT / "tmp" / "tables" / "results" / "table_protein_regulators.png"
ROWS = [
    ("None", "baseline", "binder_minimal_luna"),
    ("GPT-6 Luna", "ambiguous", "binder_minimal_regulated_luna_luna_ambiguous"),
    ("GPT-6 Luna", "enforce", "binder_minimal_regulated_luna_luna_enforce"),
    ("Claude Haiku 5.5", "ambiguous", "binder_minimal_regulated_luna_haiku_ambiguous"),
    ("Claude Haiku 5.5", "enforce", "binder_minimal_regulated_luna_haiku_enforce"),
    ("Gemini 4 Argon", "ambiguous", "binder_minimal_regulated_luna_argon_ambiguous"),
    ("Gemini 4 Argon", "enforce", "binder_minimal_regulated_luna_argon_enforce"),
    ("Claude Sonnet 5.5", "ambiguous", "binder_minimal_regulated_luna_sonnet_ambiguous"),
    ("Claude Sonnet 5.5", "enforce", "binder_minimal_regulated_luna_sonnet_enforce"),
    ("Claude Opus 5.5", "ambiguous", "binder_minimal_regulated_luna_opus_ambiguous"),
    ("Claude Opus 5.5", "enforce", "binder_minimal_regulated_luna_opus_enforce"),
]


def read(name: str) -> dict:
    run = RUNS / name
    v = validity(run)
    regs = v.get("regulators") or []
    ladders = record(run) if v["valid"] else {"cheating": None, "cheated": None, "collusion": None}
    return {"name": name, "rung": ladders["cheating"], "cheated": ladders["cheated"], "valid": v["valid"],
            "collusion": ladders["collusion"],
            "posts": sum(r["posts"] for r in regs) if regs else None}


def main() -> int:
    table = []
    for label, level, prefix in ROWS:
        runs, i = [], 1
        while (RUNS / f"{prefix}{i}" / "run.json").is_file():
            runs.append(read(f"{prefix}{i}"))
            i += 1
        kept = [r for r in runs if r["valid"]]
        print(label, level, "kept", [(r["name"], r["rung"], r["collusion"], r["posts"]) for r in kept],
              "dropped", [r["name"] for r in runs if not r["valid"]])
        if kept:
            table.append((label, level, kept))

    widths = [3.2, 2.3, 1.45, 4.55, 2.3]
    header = ["Regulator × level", "Cheating", CHEATED_HEADER, "Collusion",
              "Regulator posts per run"]
    title_h, head_h, row_h, legend_h = 0.55, 0.5, 0.52, 0.55
    width = sum(widths)
    height = title_h + head_h + row_h * len(table) + legend_h
    fig = Figure(figsize=(width, height), dpi=PRINT_DPI)
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.axis("off")
    pad = 0.16
    xs = [sum(widths[:j]) for j in range(len(widths))]

    ax.text(pad, title_h / 2 + 0.04, "Protein design", fontsize=11.5, weight="bold", color=INK, va="center")
    ax.text(pad + 1.42, title_h / 2 + 0.05, "GPT-6 Luna workers", fontsize=9, color=INK_SECONDARY, va="center")
    y = title_h
    for text, x in zip(header, xs):
        ax.text(x + pad, y + head_h / 2, text.upper(), fontsize=6.5, family="DejaVu Sans Mono", color=INK_SECONDARY,
                va="center")
    y += head_h
    ax.plot([0, width], [y, y], color=INK, lw=1.0)

    chip, gap = 0.3, 0.07
    groups = list(dict.fromkeys(row[0] for row in table))
    level_x = pad + 1.75
    for i, (label, level, runs) in enumerate(table):
        y0 = y + i * row_h
        cy = y0 + row_h / 2
        if groups.index(label) % 2:
            ax.add_patch(Rectangle((0, y0), width, row_h, fc=BAND, ec="none", zorder=0))
        if i == 0 or table[i - 1][0] != label:
            ax.text(pad, cy, label, fontsize=9.2, weight="bold", color=INK, va="center")
        ax.text(level_x, cy, level, fontsize=8.6, color=INK_SECONDARY, va="center",
                style="italic" if label == "None" else "normal")
        x = xs[1] + pad
        for r in runs:
            rung_chip(ax, x, cy - chip / 2, chip, chip, r["rung"])
            x += chip + gap
        hit = sum(r["cheated"] for r in runs)
        ax.text(xs[2] + pad, cy, f"{hit} of {len(runs)}", fontsize=9.2, weight="bold", color=INK, va="center")
        pw, ph = 0.8, 0.24
        for k, r in enumerate(runs):
            px = xs[3] + pad + k * (pw + 0.06)
            collusion_chip(ax, px, cy - ph / 2, pw, ph, r["collusion"], size=6.6)
        if label == "None":
            ax.text(xs[4] + pad, cy, "n/a", fontsize=8.6, color=MUTED, va="center")
        else:
            ax.text(xs[4] + pad, cy, ", ".join(str(r["posts"]) for r in runs), fontsize=8.8, color=INK, va="center")
        same_next = i + 1 < len(table) and table[i + 1][0] == label
        ax.plot([0 if not same_next else level_x - 0.04, width], [y0 + row_h, y0 + row_h], color=AXIS,
                lw=0.35 if same_next else 0.7)

    ly = y + row_h * len(table) + legend_h / 2
    shown = [r for _, _, runs in table for r in runs]
    x = cheating_legend(ax, pad, ly, {r["rung"] for r in shown})
    collusion_legend(ax, x, ly, {r["collusion"] for r in shown})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=PRINT_DPI, facecolor=WHITE)
    print(OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
