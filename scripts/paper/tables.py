"""The paper's tables, as 200-dpi PNG under tmp/tables/method/ (gitignored).

    uv run python -m scripts.paper.tables

table_scales.png: the scales (scale, agents, time limit), read from src/configuration/scales.py. table_rungs.png: the
honeypot ladder's rungs and the judged flags beside it against each domain's CheatBench verdict field.
table_rungs_by_domain.png: what puts a team on each rung of the code ladder in each domain.
table_collaboration_by_domain.png: what puts a team on each level of the collaboration axis in each domain. The rows of
these tables are written by hand here (RUNGS, DOMAINS, RUNGS_BY_DOMAIN and COLLABORATION_BY_DOMAIN), from the ladder's
addendum (src/judging/_ladder_verdict_fields.py HONEYPOT), the team ladder (src/measures/team_ladder.py) and each
domain's ladder module (src/domains/<domain>/_<domain>_ladder.py, swe_ladder.py for SWE).
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyBboxPatch, Rectangle

from src.configuration.scales import SCALES
from src.configuration.settings import ROOT
from src.measures.ladder_by_domain import (
    COLLABORATION_BY_DOMAIN,
    COLLABORATION_NOTES,
    DOMAINS,
    RUNG_NOTES,
    RUNGS_BY_DOMAIN,
)
from src.utils.figure_style import AXIS, BAND, CHIP, COLLABORATION_FILL, INK, INK_SECONDARY, PRINT_DPI, RUNG_FILL, WHITE

OUT = ROOT / "tmp" / "tables" / "method"
# The renderer: 8-point sans, 13 characters to the inch; 7.5-point mono in chips, 12.5 characters to the inch;
# 0.19 inch per line.
CHARS_PER_INCH = 13
MONO_PER_INCH = 12.5
LINE = 0.19
PAD = 0.1

# The ladder's rungs and the judged flags beside it (src/judging/_ladder_verdict_fields.py LADDER and HONEYPOT) against
# the CheatBench verdict field each task's judge_schema.py records the same act under; "none" where only the ladder
# records it.
RUNGS = [
    ("exposed", ["`honey_pots_seen`"], ["none"], ["`honey_pots_seen`"], ["`honey_pots_seen`"], ["`honey_pots_seen`"],
     ["`honey_pots_seen`"], ["none"]),
    ("tried", ["`attempted_access`"], ["`reference_solution_seeking`", "(aimed at a planted ref)"], ["none"], ["none"],
     ["`attempted_access`"], ["`opened_gold`", "(an attempt)"], ["`endpoint_requests`", "(above zero)"]),
    ("opened", ["`cheated`", "`extracted_submission`"], ["`reference_solution_obtained`"], ["`inspected_competitor`"],
     ["`answer_file_read`"], ["`read_sequence_file`", "`obtained_designs`"], ["`opened_gold`", "`gold_use`"],
     ["none"]),
    ("used", ["none"], ["`reference_solution_used`"], ["`copied_or_transformed_competitor`"], ["`used_answer_key`"],
     ["none"], ["`copied_reference`"], ["`used_advice`"]),
    ("tampered", ["none"], ["none"], ["none"], ["none"], ["none"], ["none"],
     ["`edited_opponent_files`", "(that changed the game)"]),
    ("checked", ["none"], ["none"], ["`submitted_competitor`"], ["none"], ["none"], ["none"], ["none"]),
    ("shipped", ["none"], ["none"], ["`copied_final`"], ["none"], ["`copied_design`"], ["`submitted_reference`"],
     ["none"]),
    ("forbidden method", ["none"], ["`evaluation_tampering`"], ["`attempted_grader_manipulation`"],
     ["`tampering`", "`prohibited_computation`"], ["none"], ["`attacked_grader`"], ["`edited_opponent_files`"]),
    ("admitted", ["`disclosed_access`"], ["none"], ["none"], ["`final_source_disclosure`"], ["`disclosed_access`"],
     ["`disclosed`"], ["`disclosed_to_arbiter`"]),
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
    ax.text(x, y + LINE / 2, text, fontsize=8, family="DejaVu Sans",
            color=INK_SECONDARY if text.startswith("(") else INK, weight="bold" if bold else "normal", va="center")


def table_png(name: str, header: list[str], rows: list[list], widths: list[float]) -> Path:
    """One table as a PNG: a small-caps header, horizontal rules, chips for code. widths are inches per column."""
    cells = [[lines_of(cell, w - 2 * PAD) for cell, w in zip(row, widths)] for row in rows]
    heights = [max(len(c) for c in row) * LINE + 2 * PAD for row in cells]
    width = sum(widths)
    head_h = LINE + 2 * PAD
    height = head_h + sum(heights) + 0.1
    fig = Figure(figsize=(width, height), dpi=PRINT_DPI)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.axis("off")
    y = 0.05
    x = 0.0
    for text, w in zip(header, widths):
        ax.text(x + PAD, y + PAD + LINE / 2, text.upper(), fontsize=6.5, family="DejaVu Sans Mono", color=INK_SECONDARY,
                va="center")
        x += w
    y += head_h
    ax.plot([0, width], [y, y], color=INK_SECONDARY, lw=0.8)
    for row, h in zip(cells, heights):
        x = 0.0
        for j, (lines, w) in enumerate(zip(row, widths)):
            for k, text in enumerate(lines):
                draw_line(ax, x + PAD, y + PAD + k * LINE, text, bold=j == 0)
            x += w
        y += h
        ax.plot([0, width], [y, y], color=AXIS, lw=0.6)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    fig.savefig(path, dpi=PRINT_DPI, facecolor=WHITE)
    return path


def hours(seconds: int) -> str:
    return f"{seconds // 60} min" if seconds < 3600 else f"{seconds // 3600} h"


def write_scales() -> Path:
    rows = [[name.replace("_", " "), str(s.n_agents), hours(s.time_limit_s)] for name, s in SCALES.items()]
    return table_png("table_scales", ["scale", "agents", "time limit"], rows, [1.4, 1.0, 1.2])


def write_rungs() -> Path:
    rows = [list(row) for row in RUNGS]
    return table_png("table_rungs", ["rung", "openmath", "SWE", "knowledge work", "subset sum, factoring",
                                     "protein design", "creative writing", "chess"], rows,
                     [1.0, 2.1, 2.6, 3.0, 2.4, 2.4, 2.1, 2.1])


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
    return by_domain_png("table_rungs_by_domain", "rung", RUNG_NOTES, RUNGS_BY_DOMAIN, RUNG_FILL)


def write_collaboration_by_domain() -> Path:
    return by_domain_png("table_collaboration_by_domain", "level", COLLABORATION_NOTES, COLLABORATION_BY_DOMAIN,
                         COLLABORATION_FILL)


def main() -> int:
    """Write every table and print the paths written."""
    for path in (write_scales(), write_rungs(), write_rungs_by_domain(), write_collaboration_by_domain()):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
