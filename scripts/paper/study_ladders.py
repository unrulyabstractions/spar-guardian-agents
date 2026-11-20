"""The paper's view of the three ladders: the study's valid runs, each run's Cheating and Collusion rungs
(src/measures/cheating_ladder.py cheating, src/measures/collusion.py collusion_of, all read by code), the ladders'
colours and marks, the table of counts by task that the results tables share, and the check that a drawn table stays
readable at the page's width.
A team or an agent cheated when the measures' one definition says so (cheating_ladder.cheated: CHEATED_FROM or
above). The paper's second measure, accessed the honeypot (ACCESSED_FROM or above), is drawn here and nowhere else.

    valid_runs(study)   # the runs whose status is valid and that are not mock (run_listing.list_collection)
    cell_runs(prefix)   # the valid runs of one cell: <prefix><seed>, then its pilots <prefix>[_]pilot<seed>
    record(path)        # {"cheating": rung, "cheated", "accessed", "passed", "alarm", "pieces",
                        #  "agents": {agent: {worker, rung, episode}}, "influencers": {agent: {role, posts, solicited}},
                        #  "collusion": rung, "collusion_agents": {agent: {role, rung}}, "collusion_applicable"}
    labels(path)        # the run's judge scores per agent (src/measures/judge_labels.py labels)
    save(fig, path)     # writes the PNG; stops when a text would print below MIN_PRINT_PT at the page's width

Nothing is written under runs/, and no model is called.
"""
from __future__ import annotations

import re
from collections import Counter
from functools import cache
from pathlib import Path

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch, Rectangle

from src.configuration.settings import ROOT
from src.measures import judge_labels
from src.measures.cheating_ladder import CHEATED_FROM, cheated, cheating, scanned_run
from src.measures.collusion import RUNGS as COLLUSION
from src.measures.collusion import collusion_of
from src.measures.run_directory import is_influencer
from src.measures.run_listing import Listed, list_collection
from src.measures.rungs import RUNGS as CHEATING
from src.measures.rungs import at_least
from src.measures.team_ladder import posts_of, solicits
from src.utils.figure_style import AXIS, BAND, GOOD, GREY_RAMP, INK, INK_SECONDARY, MUTED, PRINT_DPI, WHITE

STUDY = ROOT / "runs" / "study_exploration_1"
# The study's tasks, in the order of src/measures/ladder_by_domain.py DOMAINS: SWE first.
TASKS = [("swe", "SWE"), ("protein_design", "Protein design"), ("creative_writing", "Creative writing")]

CHEATING_FILL = dict(zip(CHEATING, ("#eeede8", "#dbe5ee", "#a9c1d9", "#cf3a27", "#7d140f")))
"""The Cheating ladder (rungs.RUNGS) in the viewer's colours (tv_case_view.js RAMP): none, looked and tempted calm
blue-grey, attempted and hacked, the cheating rungs, strong red."""
CHEATING_TEXT = {"attempted": WHITE, "hacked": WHITE}
CHEATING_LETTER = dict(zip(CHEATING, ("·", "L", "T", "A", "H")))
COLLUSION_FILL = dict(zip(COLLUSION, ("#eeede8", "#d5ebe0", "#93c9ae", "#c2185b")))
"""The Collusion ladder (collusion.RUNGS) in the viewer's colours: cooperation and collaboration calm green, collusion
strong crimson."""
COLLUSION_TEXT = {"collusion": WHITE}
COLLUSION_SHORT = dict(zip(COLLUSION, ("·", "Co", "Cb", "Cu")))
"""A Collusion rung's mark on a chip; the legend spells each one out."""
CHEATED_LABEL = f"cheated: {CHEATED_FROM} or above"
"""A legend's words for the cheating line, the one the measures draw."""
ACCESSED_FROM = "looked"
"""The paper's second measure: an agent or a team accessed the honeypot when its rung is this or higher (accessed)."""
ACCESSED_LABEL = f"accessed the honeypot: {ACCESSED_FROM} or above"
"""A legend's words for the second measure."""
TEXT_WIDTH_IN = 6.5
"""The paper's text width (letter paper, 1 in margins): a table set at \\linewidth is scaled to it."""
MIN_PRINT_PT = 6.0
"""The smallest text a table may print at the page's width."""


def accessed(rung: str | None) -> bool | None:
    """Whether an agent or a team at this rung accessed the honeypot: the rung is ACCESSED_FROM or above. None for no
    rung."""
    return None if rung is None else at_least(rung, ACCESSED_FROM)


@cache
def valid_runs(study: Path = STUDY) -> tuple[Listed, ...]:
    """The collection's runs whose status is valid and that are not mock, as the measures list them
    (src/measures/run_listing.py list_collection): the one reading of which runs count. Each carries its INDEX.jsonl
    or manifest.jsonl row as `meta`."""
    return tuple(r for r in list_collection(study) if r.status == "valid" and not r.meta.get("mock"))


def cell_runs(prefix: str, study: Path = STUDY) -> list[Path]:
    """The valid runs of one cell, by name: <prefix><seed> in seed order, then the cell's pilots
    (<prefix>pilot<seed> or <prefix>_pilot<seed>)."""
    pattern = re.compile(re.escape(prefix) + r"(_?pilot)?(\d+)")
    found = {r.path: m for r in valid_runs(study) if (m := pattern.fullmatch(r.path.name))}
    return sorted(found, key=lambda path: (bool(found[path].group(1)), int(found[path].group(2))))


@cache
def labels(path: Path) -> dict:
    """The run's judge scores per agent (src/measures/judge_labels.py labels: disclosure and manipulation, each a score
    from 0 to 100 with an excerpt; an agent the judge has not scored is None)."""
    return judge_labels.labels(Path(path))


@cache
def record(path: Path) -> dict:
    """One run's Cheating and Collusion rungs, for the team and for each agent, the pieces its hand-in holds (the
    Cheating ladder's hand_in_pieces), and each influencer's posts and the posts of those that ask a teammate for the
    honeypot (src/measures/team_ladder.py posts_of and solicits). The Collusion ladder is collusion_of on the same
    Cheating ladder, as collusion.run_collusion computes it; "collusion" is the team's rung."""
    run, domain, log, rows, _, team = scanned_run(Path(path))
    result = cheating(run, domain, rows, team or {}, log)
    collusion = collusion_of(Path(path), result)
    roster = (team or {}).get("agents") or []
    posts = posts_of(run.board(team["sample"]), roster) if team else []
    influencers = {agent: {"role": role, "posts": sum(p["sender"] == agent for p in posts),
                           "solicited": sum(p["sender"] == agent and solicits(p, roster, domain) for p in posts)}
                   for agent, role in run.roles().items() if is_influencer(role)}
    applicable = bool(collusion.get("applicable"))
    return {"cheating": result["team"], "cheated": cheated(result["team"]), "accessed": accessed(result["team"]),
            "passed": result["passed"] is True, "alarm": result["alarm"], "pieces": result["hand_in_pieces"],
            "agents": {a: {"worker": r["worker"], "rung": r["rung"], "episode": r["episode"]}
                       for a, r in result["agents"].items()},
            "influencers": influencers,
            "collusion_applicable": applicable,
            "collusion": collusion["team"] if applicable else None,
            "collusion_agents": {a: {"role": r["role"], "rung": r["rung"]}
                                 for a, r in (collusion.get("agents") or {}).items()}}


def save(fig: Figure, path: Path) -> Path:
    """Write a table or figure set at \\linewidth, after checking that every text prints at MIN_PRINT_PT or more
    once the page scales the figure to TEXT_WIDTH_IN."""
    scale = TEXT_WIDTH_IN / fig.get_figwidth()
    texts = [*fig.texts, *(t for ax in fig.axes for t in ax.texts)]
    small = sorted((t.get_fontsize() * scale, t.get_text()) for t in texts if t.get_text().strip())
    if small and small[0][0] < MIN_PRINT_PT:
        raise SystemExit(f"{path.name}: {small[0][1]!r} prints at {small[0][0]:.1f} pt, below {MIN_PRINT_PT} pt")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=PRINT_DPI, facecolor=fig.get_facecolor())
    return path


TILE = 0.3
"""A run's tile: the side of its Cheating chip, in inches; the Collusion chip under it is as wide."""
TILE_GAP = 0.05
"""The space between two tiles of a cell."""
CHAR_IN = 0.0085
"""A character's average width per point of font size, in inches, for laying out text before drawing it."""
LEGEND_PT = 7.0


def text_width(text: str, size: float) -> float:
    """The width a text takes at a font size, in inches, as an estimate for layout."""
    return len(text) * size * CHAR_IN


def rung_chip(ax, x: float, y: float, w: float, h: float, rung: str, size: float = 8.0) -> None:
    """A Cheating rung as a rounded tile with its letter."""
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.035", fc=CHEATING_FILL[rung],
                                ec=AXIS if rung == "none" else "none", lw=0.6))
    ax.text(x + w / 2, y + h / 2, CHEATING_LETTER[rung], fontsize=size, weight="bold",
            color=CHEATING_TEXT.get(rung, INK), ha="center", va="center")


def collusion_chip(ax, x: float, y: float, w: float, h: float, rung: str | None, size: float = 6.5) -> None:
    """A Collusion rung as a pill with its mark (COLLUSION_SHORT); "n/a" where the ladder does not apply."""
    label = "n/a" if rung is None else COLLUSION_SHORT[rung]
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.03",
                                fc=WHITE if rung is None else COLLUSION_FILL[rung], ec=AXIS if rung is None else "none",
                                lw=0.6))
    ax.text(x + w / 2, y + h / 2, label, fontsize=size, ha="center", va="center",
            color=MUTED if rung is None else COLLUSION_TEXT.get(rung, INK))


def tile_height(passed: bool = False, pieces: bool = False) -> float:
    """The height of a run's tile (run_tile) with the rows asked for."""
    return TILE + 0.03 + 0.19 + (0.2 if passed or pieces else 0)


def run_tile(ax, x: float, y: float, run: dict, *, passed: bool = False, pieces: bool = False) -> None:
    """One run (a record): its team's Cheating chip, its Collusion chip under it, then a row with the pieces its
    hand-in holds (`pieces`) and a check when the grader passed it (`passed`)."""
    rung_chip(ax, x, y, TILE, TILE, run["cheating"])
    collusion_chip(ax, x, y + TILE + 0.03, TILE, 0.19, run["collusion"])
    below = y + TILE + 0.03 + 0.19 + 0.11
    mark = ("".join(run["pieces"]) if pieces else "") + ("✓" if passed and run["passed"] else "")
    if mark:
        ax.text(x + TILE / 2, below, mark, fontsize=6.5, weight="bold", ha="center", va="center",
                color=GOOD if mark == "✓" else INK)


def tiles_width(n: int) -> float:
    """The width n tiles take side by side."""
    return n * TILE + max(n - 1, 0) * TILE_GAP


def legend_items(runs: list[dict], *, passed: bool = False, pieces: bool = False, lines: bool = True) -> list[tuple]:
    """The legend of a table of run tiles: the Cheating and Collusion rungs its runs show, then the marks asked for,
    then the two measures' lines (`lines`, for a table that counts them)."""
    items: list[tuple] = [("cheating", rung, rung) for rung in CHEATING if rung in {r["cheating"] for r in runs}]
    shown = {r["collusion"] for r in runs}
    items += [("collusion", rung, f"Collusion: {rung}") for rung in COLLUSION if rung in shown]
    if None in shown:
        items.append(("collusion", None, "Collusion: not applicable"))
    if pieces:
        items.append(("text", "AB", "pieces of the planted fix in the hand-in"))
    if passed:
        items.append(("text", "✓", "the grader passed the hand-in"))
    return items + ([("line", None, CHEATED_LABEL), ("line", None, ACCESSED_LABEL)] if lines else [])


def legend_rows(items: list[tuple], width: float) -> list[list[tuple[float, tuple]]]:
    """The legend's items laid out in rows no wider than `width`: [[(x, item)]]."""
    rows: list[list[tuple[float, tuple]]] = [[]]
    x = 0.0
    for item in items:
        w = (0 if item[0] == "line" else 0.27) + text_width(item[2], LEGEND_PT) + 0.25
        if rows[-1] and x + w > width:
            rows.append([])
            x = 0.0
        rows[-1].append((x, item))
        x += w
    return rows


LEGEND_ROW = 0.24


def draw_legend(ax, x0: float, y0: float, rows: list[list[tuple[float, tuple]]]) -> None:
    """Draw legend_rows from (x0, y0), the first row's centre line."""
    for k, row in enumerate(rows):
        y = y0 + k * LEGEND_ROW
        for x, (kind, key, label) in row:
            x += x0
            if kind == "cheating":
                rung_chip(ax, x, y - 0.09, 0.18, 0.18, key, size=7)
            elif kind == "collusion":
                collusion_chip(ax, x - 0.02, y - 0.08, 0.22, 0.16, key, size=6.0 if key is None else 6.5)
            elif kind == "text":
                ax.text(x + 0.09, y, key, fontsize=6.5, weight="bold", ha="center", va="center",
                        color=GOOD if key == "✓" else INK)
            ax.text(x + (0 if kind == "line" else 0.27), y, label, fontsize=LEGEND_PT, color=INK_SECONDARY,
                    va="center")


def canvas(width: float, height: float, facecolor: str = WHITE) -> tuple[Figure, object]:
    """A figure of the given size in inches, with one axes in inch coordinates from the top left."""
    fig = Figure(figsize=(width, height), dpi=PRINT_DPI, facecolor=facecolor)
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.axis("off")
    return fig, ax


def shade(share: float) -> str | None:
    """A count cell's fill by its share of the row: none below a tenth, then the grey ramp."""
    if share < 0.1:
        return None
    return GREY_RAMP[min(int(share * 5), len(GREY_RAMP) - 1)]


def counts_table(path: Path, levels: tuple[str, ...], data: dict[tuple[str, str], Counter],
                 blocks: list[tuple[str, str, str]], blank: dict[tuple[str, str, str], str] | None = None,
                 apart: tuple[str, ...] = (), rows: dict[str, list[tuple[str, str]]] | None = None,
                 first: str = "Task", left: float = 1.45, notes: dict[tuple[str, str], list[str]] | None = None
                 ) -> Path:
    """A table of counts: a column per level, a block of rows per (key, title, unit) of `blocks`, a row per (key,
    label) of `rows[block]` (the tasks of TASKS by default), each cell the count of `data[(block, key)]` at that level.
    `blank` gives a cell's text in place of its count, by (block, key, level). The columns of `apart` count something
    beside the levels and stay out of the row's count. `first` heads the label column, `left` is its width. `notes`
    gives a row's lines of smaller text under its label, by (block, key)."""
    blank, notes = blank or {}, notes or {}
    rows = rows or {block: TASKS for block, _, _ in blocks}
    total, col = 0.85, max(0.66, text_width(max(map(str, levels), key=len), 8) + 0.12)
    width = left + total + col * len(levels) + 0.1
    head_h, block_h, note_h = 0.42, 0.32, 0.13

    def row_height(block: str, key: str) -> float:
        return 0.27 + note_h * len(notes.get((block, key), []))

    height = head_h + sum(block_h + sum(row_height(block, key) for key, _ in rows[block]) for block, _, _ in blocks) \
        + 0.12
    fig, ax = canvas(width, height)
    pad = 0.15
    x_levels = left + total
    ax.text(pad, head_h / 2, first, fontsize=8, weight="bold", color=INK, va="center")
    ax.text(left + total - pad, head_h / 2, "Count", fontsize=8, weight="bold", color=INK, va="center", ha="right")
    for k, level in enumerate(levels):
        ax.text(x_levels + (k + 0.5) * col, head_h / 2, level, fontsize=8, weight="bold", color=INK, va="center",
                ha="center")
    ax.plot([0, width], [head_h, head_h], color=INK, lw=1.0)
    y = head_h
    for block, title, unit in blocks:
        ax.text(pad, y + block_h / 2 + 0.03, title, fontsize=7.5, style="italic", color=INK_SECONDARY, va="center")
        y += block_h
        for i, (key, label) in enumerate(rows[block]):
            c = data[(block, key)]
            n = sum(v for level, v in c.items() if level not in apart)
            row_h, lines = row_height(block, key), notes.get((block, key), [])
            if i % 2:
                ax.add_patch(Rectangle((0, y), width, row_h, fc=BAND, ec="none", zorder=0))
            ax.text(pad + 0.15, y + (0.135 if lines else row_h / 2), label, fontsize=7.5, color=INK, va="center")
            for m, line in enumerate(lines):
                ax.text(pad + 0.15, y + 0.27 + m * note_h, line, fontsize=7, color=INK_SECONDARY, va="center")
            ax.text(left + total - pad, y + row_h / 2, f"{n} {unit}", fontsize=7, color=INK_SECONDARY,
                    va="center", ha="right")
            for k, level in enumerate(levels):
                cx = x_levels + k * col
                text = blank.get((block, key, level))
                if text is not None:
                    ax.text(cx + col / 2, y + row_h / 2, text, fontsize=7, style="italic", color=MUTED,
                            va="center", ha="center")
                    continue
                value = c.get(level, 0)
                fill = shade(value / n) if value else None
                if fill:
                    ax.add_patch(Rectangle((cx + 0.04, y + 0.03), col - 0.08, row_h - 0.06, fc=fill, ec="none",
                                           zorder=1))
                ax.text(cx + col / 2, y + row_h / 2, str(value) if value else "·", fontsize=8,
                        color=INK if value else MUTED, va="center", ha="center", zorder=2)
            y += row_h
        ax.plot([0, width], [y, y], color=AXIS, lw=0.6)
    return save(fig, path)


def role_table(path: Path, title: str, rows: list[tuple], role: str, *, pieces: bool = False,
               per_row: int | None = None) -> Path:
    """A table of cells with an influencer, the page's width across: a cell per (label, level, *cell prefixes) of
    `rows`, the cells of one label side by side, or `per_row` cells to a row in the order of `rows`. Each cell shows
    its valid runs as tiles (run_tile; with the grader's pass; `pieces` adds the hand-in's
    pieces), under each tile the
    posts of the run's agents of `role` (and, for a disregulator, those that ask a teammate for the honeypot), then the
    teams that cheated and the teams that accessed the honeypot. A cell without a valid run is left out."""
    grid: dict[str, list[tuple[str, str, list[dict]]]] = {}
    for label, level, *prefixes in rows:
        runs = [{"name": p.name, **record(p)} for prefix in prefixes for p in cell_runs(prefix)]
        print(path.stem, label, level, [(r["name"], CHEATING_LETTER[r["cheating"]], COLLUSION_SHORT.get(r["collusion"]),
                                          r["pieces"], int(r["passed"]), posts_mark(r, role)) for r in runs])
        if runs:
            key = label if per_row is None else str(sum(map(len, grid.values())) // per_row)
            grid.setdefault(key, []).append((label, level, runs))
    pad, width = 0.12, TEXT_WIDTH_IN
    col = (width - pad) / max(len(cells) for cells in grid.values())
    shown = [r for cells in grid.values() for _, _, runs in cells for r in runs]
    passed = any(r["passed"] for r in shown)
    tile_h = tile_height(passed=passed, pieces=pieces)
    row_h = 0.3 + tile_h + 0.2 + 0.08
    summary_x = tiles_width(max(len(runs) for cells in grid.values() for _, _, runs in cells)) + 0.15
    items = legend_items(shown, passed=passed, pieces=pieces)
    mark, words = ("8/1", f"the {role}'s posts / those asking for the honeypot") if role == "disregulator" \
        else ("12", f"the {role}'s posts")
    items.insert(-2, ("text", mark, words))
    legend = legend_rows(items, width - 2 * pad)
    title_h = 0.36
    height = title_h + row_h * len(grid) + 0.15 + LEGEND_ROW * len(legend)
    fig, ax = canvas(width, height)
    ax.text(pad, title_h / 2 + 0.02, title, fontsize=9, weight="bold", color=INK, va="center")
    ax.plot([0, width], [title_h, title_h], color=INK, lw=1.0)
    for i, cells in enumerate(grid.values()):
        y0 = title_h + i * row_h
        if i % 2:
            ax.add_patch(Rectangle((0, y0), width, row_h, fc=BAND, ec="none", zorder=0))
        for j, (label, level, runs) in enumerate(cells):
            x0 = pad + j * col
            ax.text(x0, y0 + 0.18, label, fontsize=7.5, weight="bold", color=INK, va="center")
            ax.text(x0 + text_width(label, 7.5) * 1.1 + 0.08, y0 + 0.18, level, fontsize=7, style="italic",
                    color=INK_SECONDARY, va="center")
            for k, run in enumerate(runs):
                x = x0 + k * (TILE + TILE_GAP)
                run_tile(ax, x, y0 + 0.3, run, passed=passed, pieces=pieces)
                ax.text(x + TILE / 2, y0 + 0.3 + tile_h + 0.1, posts_mark(run, role), fontsize=6.5,
                        color=INK_SECONDARY, ha="center", va="center")
            n, x = len(runs), x0 + summary_x
            ax.text(x, y0 + 0.42, f"cheated {sum(r['cheated'] for r in runs)} of {n}", fontsize=7.5, weight="bold",
                    color=INK, va="center")
            ax.text(x, y0 + 0.62, f"accessed {sum(r['accessed'] for r in runs)} of {n}", fontsize=7, color=INK,
                    va="center")
        ax.plot([0, width], [y0 + row_h] * 2, color=AXIS, lw=0.6)
    draw_legend(ax, pad, title_h + row_h * len(grid) + 0.15 + LEGEND_ROW / 2, legend)
    return save(fig, path)


def posts_mark(run: dict, role: str) -> str:
    """The posts of the run's agents of `role`, and for a disregulator those that ask for the honeypot; "" when the
    run has no such agent."""
    agents = [a for a in run["influencers"].values() if a["role"] == role]
    if not agents:
        return ""
    posts = sum(a["posts"] for a in agents)
    return f"{posts}/{sum(a['solicited'] for a in agents)}" if role == "disregulator" else str(posts)
