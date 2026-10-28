"""The paper's tables, as PNG under tmp/ (gitignored), every value read from the source.

    uv run python scripts/paper/tables.py

table_scales.png: the scales (scale, agents, time limit), from src/configuration/scales.py. table_rungs.png: the
cheating ladder's rungs against each domain's CheatBench verdict field, from src/configuration/judge_ladder.py.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import matplotlib
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.configuration.scales import SCALES  # noqa: E402

matplotlib.use("Agg")
OUT = ROOT / "tmp"
# The renderer: 8-point sans, 13 characters to the inch; 7.5-point mono in chips, 12.5 characters to the inch;
# 0.19 inch per line.
CHARS_PER_INCH = 13
MONO_PER_INCH = 12.5
LINE = 0.19
PAD = 0.1
INK = "#0b0b0b"
INK2 = "#52514e"
RULE = "#c3c2b7"
CHIP = "#efeee9"

# The ladder's rungs (src/configuration/judge_ladder.py LADDER and HONEYPOT) against the CheatBench verdict field each
# task's judge_schema.py records the same act under; "none" where only the ladder records it.
RUNGS = [
    ("exposed", ["`honey_pots_seen`"], ["none"], ["`honey_pots_seen`"], ["`honey_pots_seen`"], ["`honey_pots_seen`"]),
    ("searched", ["none"], ["`reference_solution_seeking`", "(no known target)"], ["none"], ["none"], ["none"]),
    ("tried", ["`attempted_access`"], ["`reference_solution_seeking`", "(known target)"], ["none"], ["none"],
     ["`attempted_access`"]),
    ("opened", ["`cheated`", "`extracted_submission`"], ["`reference_solution_obtained`"], ["`inspected_competitor`"],
     ["`answer_file_read`"], ["`read_sequence_file`", "`obtained_designs`"]),
    ("used", ["none"], ["`reference_solution_used`"], ["`copied_or_transformed_competitor`"], ["`used_answer_key`"],
     ["none"]),
    ("shipped", ["none"], ["none"], ["`copied_final`"], ["none"], ["`copied_design`"]),
    ("tampered", ["none"], ["`evaluation_tampering`"], ["`attempted_grader_manipulation`"], ["`tampering`"], ["none"]),
    ("admitted", ["`disclosed_access`"], ["none"], ["none"], ["`final_source_disclosure`"], ["`disclosed_access`"]),
]


def lines_of(cell, width_in: float) -> list[str]:
    """The cell as lines: a list stays as given, a string wraps to the column."""
    if isinstance(cell, list):
        return cell
    chars = max(6, int(width_in * CHARS_PER_INCH))
    return textwrap.wrap(str(cell), chars, break_long_words=True, break_on_hyphens=False) or [""]


def draw_line(ax, x: float, y: float, text: str, bold: bool = False) -> None:
    """One line of a cell at its top left: a chip in mono for `code`, else sans."""
    if text.startswith("`") and text.endswith("`"):
        code = text.strip("`")
        w = len(code) / MONO_PER_INCH + 0.1
        ax.add_patch(FancyBboxPatch((x, y + 0.02), w, LINE - 0.05, boxstyle="round,pad=0,rounding_size=0.03",
                                    fc=CHIP, ec="none"))
        ax.text(x + 0.05, y + LINE / 2, code, fontsize=7.5, family="DejaVu Sans Mono", color=INK, va="center")
        return
    ax.text(x, y + LINE / 2, text, fontsize=8, family="DejaVu Sans", color=INK2 if text.startswith("(") else INK,
            weight="bold" if bold else "normal", va="center")


def table_png(name: str, header: list[str], rows: list[list], widths: list[float]) -> Path:
    """One table as a PNG: a small-caps header, horizontal rules, chips for code. widths are inches per column."""
    cells = [[lines_of(cell, w - 2 * PAD) for cell, w in zip(row, widths)] for row in rows]
    heights = [max(len(c) for c in row) * LINE + 2 * PAD for row in cells]
    width = sum(widths)
    head_h = LINE + 2 * PAD
    height = head_h + sum(heights) + 0.1
    fig = Figure(figsize=(width, height), dpi=200)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.axis("off")
    y = 0.05
    x = 0.0
    for text, w in zip(header, widths):
        ax.text(x + PAD, y + PAD + LINE / 2, text.upper(), fontsize=6.5, family="DejaVu Sans Mono", color=INK2,
                va="center")
        x += w
    y += head_h
    ax.plot([0, width], [y, y], color=INK2, lw=0.8)
    for row, h in zip(cells, heights):
        x = 0.0
        for j, (lines, w) in enumerate(zip(row, widths)):
            for k, text in enumerate(lines):
                draw_line(ax, x + PAD, y + PAD + k * LINE, text, bold=j == 0)
            x += w
        y += h
        ax.plot([0, width], [y, y], color=RULE, lw=0.6)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    fig.savefig(path, dpi=200, facecolor="#ffffff")
    return path


def hours(seconds: int) -> str:
    return f"{seconds // 60} min" if seconds < 3600 else f"{seconds // 3600} h"


def write_scales() -> Path:
    rows = [[name.replace("_", " "), str(s.n_agents), hours(s.time_limit_s)] for name, s in SCALES.items()]
    return table_png("table_scales", ["scale", "agents", "time limit"], rows, [1.4, 1.0, 1.2])


def write_rungs() -> Path:
    rows = [list(row) for row in RUNGS]
    return table_png("table_rungs", ["rung", "openmath", "SWE", "knowledge work", "subset sum, factoring",
                                     "protein design"], rows, [1.0, 2.1, 2.6, 3.0, 2.4, 2.4])


if __name__ == "__main__":
    for path in (write_scales(), write_rungs()):
        print(path)
