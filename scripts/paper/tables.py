"""The paper's method table, as a 200-dpi PNG under tmp/tables/method/ (gitignored).

    uv run python -m scripts.paper.tables

table_rungs_by_domain.png: what puts an agent on each rung of the Cheating ladder in each task, with each task's
honeypot and grader under its name. Its rows are src/measures/ladder_by_domain.py RUNGS_BY_DOMAIN and RUNG_NOTES, in
the order of its DOMAINS: SWE first.
"""
from __future__ import annotations

from pathlib import Path

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Rectangle

from scripts.paper.study_ladders import CHEATING_FILL
from src.configuration.settings import ROOT
from src.measures.ladder_by_domain import DOMAINS, RUNG_NOTES, RUNGS_BY_DOMAIN
from src.utils.figure_style import AXIS, BAND, INK, INK_SECONDARY, PRINT_DPI, WHITE

OUT = ROOT / "tmp" / "tables" / "method"


SANS = FontProperties(family="DejaVu Sans", size=7.6)
SANS_B = FontProperties(family="DejaVu Sans", size=8.2, weight="bold")
NOTE = FontProperties(family="DejaVu Sans", size=6.8)
MONO = FontProperties(family="DejaVu Sans Mono", size=6.6)


def wrap_measured(renderer, text: str, width_in: float, font: FontProperties) -> list[str]:
    """Greedy word wrap by the renderer's own text widths, so no line can overflow its column."""
    limit = width_in * renderer.dpi
    lines: list[str] = []
    for word in text.split():
        trial = f"{lines[-1]} {word}" if lines else word
        if lines and renderer.get_text_width_height_descent(trial, font, ismath=False)[0] <= limit:
            lines[-1] = trial
        else:
            lines.append(word)
    return lines


def by_domain_png(name: str, label: str, notes: tuple[str, ...], levels: list[tuple], fills: dict[str, str]) -> Path:
    """One table of levels by domain: a row per level with its fill, number, name and gloss, a column per domain under
    its name and note."""
    first, col, pad, lh, note_lh = 1.75, 1.62, 0.09, 0.145, 0.13
    width = first + col * len(DOMAINS)
    renderer = FigureCanvasAgg(Figure(figsize=(1, 1), dpi=PRINT_DPI)).get_renderer()
    rows = []
    for level, gloss, cells in levels:
        gl = wrap_measured(renderer, gloss, first - 0.3 - pad, NOTE)
        cl = [wrap_measured(renderer, c, col - 2 * pad, SANS) for c in cells]
        h = max(0.2 + len(gl) * note_lh, max(len(c) for c in cl) * lh) + 2 * pad + 0.04
        rows.append((level, gl, cl, h))
    head = [wrap_measured(renderer, d, col - 2 * pad, SANS_B) for d in DOMAINS]
    subs = [wrap_measured(renderer, s, col - 2 * pad, NOTE) for s in notes]
    head_h = 2 * pad + max(len(h) for h in head) * 0.16 + max(len(s) for s in subs) * note_lh + 0.03
    height = 0.12 + head_h + sum(r[3] for r in rows) + 0.12
    fig = Figure(figsize=(width, height), dpi=PRINT_DPI)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.axis("off")
    y = 0.12
    ax.text(pad + 0.22, y + pad + 0.08, label.upper(), fontproperties=MONO, color=INK_SECONDARY, va="center")
    for j, (h, sub_) in enumerate(zip(head, subs)):
        x = first + j * col + pad
        for k, line in enumerate(h):
            ax.text(x, y + pad + 0.08 + k * 0.16, line, fontproperties=SANS_B, color=INK, va="center")
        top = y + pad + 0.08 + len(h) * 0.16
        for k, line in enumerate(sub_):
            ax.text(x, top + k * note_lh, line, fontproperties=NOTE, color=INK_SECONDARY, va="center", style="italic")
    y += head_h
    ax.plot([0, width], [y, y], color=INK, lw=0.9)
    for i, (level, gl, cl, h) in enumerate(rows):
        if i % 2:
            ax.add_patch(Rectangle((0, y), width, h, fc=BAND, ec="none", zorder=0))
        ax.add_patch(Rectangle((pad, y + pad), 0.09, h - 2 * pad, fc=fills[level], ec="none"))
        ax.text(pad + 0.2, y + pad + 0.08, f"{i + 1}  {level}", fontproperties=SANS_B, color=INK, va="center")
        for k, line in enumerate(gl):
            ax.text(pad + 0.2, y + pad + 0.27 + k * note_lh, line, fontproperties=NOTE, color=INK_SECONDARY,
                    va="center")
        for j, lines in enumerate(cl):
            for k, line in enumerate(lines):
                ax.text(first + j * col + pad, y + pad + 0.08 + k * lh, line, fontproperties=SANS, color=INK,
                        va="center")
        y += h
        ax.plot([0, width], [y, y], color=AXIS, lw=0.5)
    ax.plot([first - 0.02] * 2, [0.12, y], color=AXIS, lw=0.5)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    fig.savefig(path, dpi=PRINT_DPI, facecolor=WHITE)
    return path


def write_rungs_by_domain() -> Path:
    return by_domain_png("table_rungs_by_domain", "rung", RUNG_NOTES, RUNGS_BY_DOMAIN, CHEATING_FILL)


def main() -> int:
    """Write the table and print its path."""
    print(write_rungs_by_domain())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
