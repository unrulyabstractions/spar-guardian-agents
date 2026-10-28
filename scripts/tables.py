"""The paper's two configuration tables, as PNG under tmp/ (gitignored), every value read from the source.

    uv run python scripts/tables.py

table_scales.png: the scales (scale, agents, time limit), from src/configuration/scales.py. table_turn_budget.png:
each domain's turn budget per agent (settings.TURN_BUDGETS).
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import matplotlib
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.configuration import settings  # noqa: E402
from src.configuration.scales import SCALES  # noqa: E402

matplotlib.use("Agg")
OUT = ROOT / "tmp"
DOMAIN_LABEL = {"openmath": "openmath", "swe": "SWE", "knowledge_work": "knowledge work"}
# The renderer: 8-point sans, 13 characters to the inch, 0.17 inch per line.
CHARS_PER_INCH = 13
LINE = 0.17
PAD = 0.08
INK = "#0b0b0b"
RULE = "#c3c2b7"
HEAD = "#f2f1ec"


def wrap(text: str, width_in: float) -> list[str]:
    """The cell's text as lines that fit its column."""
    chars = max(6, int(width_in * CHARS_PER_INCH))
    return textwrap.wrap(str(text), chars, break_long_words=True, break_on_hyphens=False) or [""]


def table_png(name: str, header: list[str], rows: list[list[str]], widths: list[float]) -> Path:
    """One table as a PNG: a bold header row, wrapped cells. widths are column widths in inches."""
    cells = [[wrap(cell, w - 2 * PAD) for cell, w in zip(row, widths)] for row in [header, *rows]]
    heights = [max(len(c) for c in row) * LINE + 2 * PAD for row in cells]
    width = sum(widths)
    height = sum(heights) + 0.1
    fig = Figure(figsize=(width, height), dpi=200)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.axis("off")
    y = 0.05
    for r, (row, h) in enumerate(zip(cells, heights)):
        x = 0.0
        for lines, w in zip(row, widths):
            ax.add_patch(Rectangle((x, y), w, h, facecolor=HEAD if r == 0 else "#ffffff", edgecolor=RULE,
                                   linewidth=0.6))
            ax.text(x + PAD, y + PAD, "\n".join(lines), fontsize=8, family="DejaVu Sans", color=INK,
                    weight="bold" if r == 0 else "normal", va="top", ha="left", linespacing=1.25)
            x += w
        y += h
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    fig.savefig(path, dpi=200, facecolor="#ffffff")
    return path


def hours(seconds: int) -> str:
    return f"{seconds // 60} min" if seconds < 3600 else f"{seconds // 3600} h"


def write_scales() -> Path:
    rows = [[name.replace("_", " "), str(s.n_agents), hours(s.time_limit_s)] for name, s in SCALES.items()]
    return table_png("table_scales", ["scale", "agents", "time limit"], rows, [1.4, 1.0, 1.2])


def write_turn_budget() -> Path:
    rows = [[DOMAIN_LABEL[d], str(b)] for d, b in settings.TURN_BUDGETS.items()]
    return table_png("table_turn_budget", ["domain", "turn budget per agent"], rows, [1.7, 2.0])


if __name__ == "__main__":
    for path in (write_scales(), write_turn_budget()):
        print(path)
