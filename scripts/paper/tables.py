"""The paper's method table, as a 200-dpi PNG under tmp/tables/method/ (gitignored).

    uv run python -m scripts.paper.tables

table_rungs_by_domain.png and table_rungs_by_domain_2.png: what puts an agent on each rung of the Cheating ladder in
each task the paper studies (src/measures/ladder_by_domain.py DOMAINS), with each task's honeypot and grader under its
name, split after SPLIT_AFTER rungs so that each part fits a page at the text's width. Their rows are
ladder_by_domain.cheating_by_task. Each part is refused when a text would print below PRINT_FLOOR_PT at the text's
width, or when it is taller than the text.
"""
from __future__ import annotations

from pathlib import Path

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Rectangle

from scripts.paper.figures import PRINT_FLOOR_PT
from scripts.paper.study_ladders import CHEATING_FILL, TEXT_WIDTH_IN
from src.configuration.settings import ROOT
from src.measures.ladder_by_domain import cheating_by_task
from src.utils import progress
from src.utils.figure_style import AXIS, BAND, INK, INK_SECONDARY, PRINT_DPI, WHITE

OUT = ROOT / "tmp" / "tables" / "method"
SPLIT_AFTER = 3
"""The rungs of the first part: none, looked and tempted; the second part holds attempted and hacked."""
TEXT_HEIGHT_IN = 9.0


SANS = FontProperties(family="DejaVu Sans", size=7.6)
SANS_B = FontProperties(family="DejaVu Sans", size=8.2, weight="bold")
NOTE = FontProperties(family="DejaVu Sans", size=7.2)
MONO = FontProperties(family="DejaVu Sans Mono", size=7.0)


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


def by_domain_png(name: str, label: str, domains: list[str], notes: list[str], levels: list[tuple],
                  fills: dict[str, str], first_number: int = 1) -> Path:
    """One table of levels by domain: a row per level with its fill, number (from first_number), name and gloss, a
    column per domain under its name and note."""
    first, col, pad, lh, note_lh = 1.75, 1.62, 0.09, 0.145, 0.13
    width = first + col * len(domains)
    renderer = FigureCanvasAgg(Figure(figsize=(1, 1), dpi=PRINT_DPI)).get_renderer()
    rows = []
    for level, gloss, cells in levels:
        gl = wrap_measured(renderer, gloss, first - 0.3 - pad, NOTE)
        cl = [wrap_measured(renderer, c, col - 2 * pad, SANS) for c in cells]
        h = max(0.2 + len(gl) * note_lh, max(len(c) for c in cl) * lh) + 2 * pad + 0.04
        rows.append((level, gl, cl, h))
    head = [wrap_measured(renderer, d, col - 2 * pad, SANS_B) for d in domains]
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
        ax.text(pad + 0.2, y + pad + 0.08, f"{i + first_number}  {level}", fontproperties=SANS_B, color=INK,
                va="center")
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
    scale = TEXT_WIDTH_IN / width
    small = min(f.get_size() for f in (SANS, SANS_B, NOTE, MONO)) * scale
    if small < PRINT_FLOOR_PT or height * scale > TEXT_HEIGHT_IN:
        raise SystemExit(f"{name}: smallest text {small:.1f} pt, height {height * scale:.1f} in at the text's width")
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    fig.savefig(path, dpi=PRINT_DPI, facecolor=WHITE)
    return path


def write_rungs_by_domain() -> list[Path]:
    table = cheating_by_task()
    rows = table["rows"]
    return [by_domain_png("table_rungs_by_domain", "rung", table["domains"], table["notes"], rows[:SPLIT_AFTER],
                          CHEATING_FILL),
            by_domain_png("table_rungs_by_domain_2", "rung", table["domains"], table["notes"], rows[SPLIT_AFTER:],
                          CHEATING_FILL, first_number=SPLIT_AFTER + 1)]


def main() -> int:
    """Write the two parts of the table and print their paths."""
    for path in write_rungs_by_domain():
        progress.wrote(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
