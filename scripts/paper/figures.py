"""The seven design figures of the paper, as 200-dpi PNG under tmp/figures/method/design/ (gitignored): the topologies,
the round structure, the context ledger, the engagement funnel, the read graph, the access split and what each role is
told. The paper sets topology_compare and round_structure at the text's width; each is
refused when a text would print below PRINT_FLOOR_PT there.

    uv run python -m scripts.paper.figures

No network, no model calls.
"""
from __future__ import annotations

import math

import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Polygon, Rectangle

from scripts.paper.study_ladders import TEXT_WIDTH_IN
from src.configuration import access_policy
from src.configuration.settings import ROOT
from src.utils import progress
from src.utils.figure_style import (
    AXIS,
    GREY_RAMP,
    GRID,
    INK,
    INK_SECONDARY,
    MUTED,
    PLANE,
    PRINT_DPI,
    ROLE_EDGE,
    ROLE_FILL,
    ROLE_TEXT,
    SURFACE,
    TOOL,
    WHITE,
)

OUT = ROOT / "tmp" / "figures" / "method" / "design"
DASH = (0, (2, 1.5))
DOT = (0, (1, 1.6))

CM = 1 / 2.54
FULL = 17 * CM
HALF = 8.5 * CM

plt.switch_backend("Agg")
plt.rcParams.update(
    {
        "font.family": "Arial",
        "font.size": 8,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "text.color": INK,
        "hatch.linewidth": 0.6,
    }
)

ROLES5 = {"k7q2m": "worker", "t4x9n": "regulator", "p8s3z": "worker", "n5j6q": "disregulator", "x2m7s": "worker"}


PRINT_FLOOR_PT = 6.5
"""The smallest text a figure the paper prints may hold, in points at the width the paper sets it."""


def check_print(fig, name: str, printed_in: float) -> None:
    """Stop when a text of the figure would print below PRINT_FLOOR_PT once the paper sets it printed_in inches
    wide."""
    scale = printed_in / fig.get_figwidth()
    texts = [t for t in [*fig.texts, *(t for ax in fig.axes for t in ax.texts)] if t.get_text().strip()]
    size, text = min((t.get_fontsize() * scale, t.get_text()) for t in texts)
    if size < PRINT_FLOOR_PT:
        raise SystemExit(f"{name}: {text!r} prints at {size:.1f} pt, below {PRINT_FLOOR_PT} pt")


def save(fig, name, printed_in: float | None = None):
    """Write the figure. printed_in: the width in inches the paper sets it at, checked by check_print."""
    if printed_in is not None:
        check_print(fig, name, printed_in)
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / f"{name}.png", dpi=PRINT_DPI)
    plt.close(fig)


def blank(ax, xlim, ylim):
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_aspect("equal")
    ax.axis("off")


def agent(ax, x, y, name, role="worker", r=0.36, fs=6.5, z=5):
    """An agent as a circle of its role with its ID inside, the font shrunk until the ID fits the circle."""
    ax.add_patch(Circle((x, y), r, fc=ROLE_FILL[role], ec=ROLE_EDGE[role], lw=1.0, zorder=z))
    label = ax.text(x, y, name, ha="center", va="center", fontsize=fs, color=ROLE_TEXT[role], zorder=z + 1)
    ax.apply_aspect()
    left, right = ax.transData.transform([(x - r, y), (x + r, y)])[:, 0]
    width = label.get_window_extent(ax.figure.canvas.get_renderer()).width
    label.set_fontsize(fs * min(1, 0.88 * (right - left) / width))


def box(ax, x, y, w, h, text=None, fc=WHITE, ec=INK_SECONDARY, lw=0.8, ls="-", hatch=None, fs=7,
        color=INK, pad=0.04, z=3, weight="normal"):
    """Rounded box with lower-left corner (x, y)."""
    p = FancyBboxPatch((x + pad, y + pad), w - 2 * pad, h - 2 * pad,
                       boxstyle=f"round,pad={pad},rounding_size={min(0.08, h / 4)}",
                       fc=fc, ec=ec, lw=lw, ls=ls, hatch=hatch, zorder=z)
    ax.add_patch(p)
    if text:
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs, color=color,
                zorder=z + 1, weight=weight)
    return p


def arrow(ax, a, b, color=INK_SECONDARY, lw=0.8, ls="-", rad=0.0, sa=0, sb=0, z=2, head=6):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle=f"-|>,head_length={head * 0.06},head_width={head * 0.035}",
                                 connectionstyle=f"arc3,rad={rad}", color=color, lw=lw, ls=ls,
                                 shrinkA=sa, shrinkB=sb, zorder=z, mutation_scale=10))


def edge_point(cx, cy, w, h, tx, ty):
    """Point on the boundary of a w x h box centred at (cx, cy) toward (tx, ty)."""
    dx, dy = tx - cx, ty - cy
    s = min((w / 2) / abs(dx) if dx else 1e9, (h / 2) / abs(dy) if dy else 1e9)
    return cx + dx * s, cy + dy * s


def toward(x, y, tx, ty, d):
    length = math.hypot(tx - x, ty - y)
    return x + (tx - x) * d / length, y + (ty - y) * d / length


def ring(n, radius, start=90):
    return [(radius * math.cos(math.radians(start - 360 * k / n)), radius * math.sin(math.radians(start - 360 * k / n)))
            for k in range(n)]


def role_legend(fig, y, extra=(), compact=False):
    """One legend row of the role glyphs, centred at figure height fraction y; compact drops "(neutral)" and
    packs the items for a half-width figure."""
    ax = fig.add_axes([0, y - 0.05, 1, 0.1])
    ax.set_xlim(0, 100)
    ax.set_ylim(-1, 1)
    ax.axis("off")
    items = [("worker", "worker" if compact else "worker (neutral)"), ("regulator", "regulator"),
             ("disregulator", "disregulator")]
    k = FULL / fig.get_figwidth()
    per_char, base = (0.62, 4.2) if compact else (0.8, 5.5)
    widths = [(len(lab) * per_char + base) * k for _, lab in [*items, *extra]]
    x = 50 - sum(widths) / 2
    for (role, label), w in zip(items, widths):
        ax.scatter([x], [0], s=36, c=ROLE_FILL[role], edgecolors=ROLE_EDGE[role], linewidths=1.0, zorder=3)
        ax.text(x + 1.4 * k, 0, label, va="center", fontsize=7.5, color=INK_SECONDARY)
        x += w
    for (draw, label), w in zip(extra, widths[len(items):]):
        draw(ax, x, 0)
        ax.text(x + 1.4 * k, 0, label, va="center", fontsize=7.5, color=INK_SECONDARY)
        x += w
    return ax


# ---------------------------------------------------------------- the topologies
def fig_topology_compare():
    fig = plt.figure(figsize=(FULL, 3.15))
    names = list(ROLES5)
    pos = ring(5, 2.7)
    panels = ["Our board", "OpenAI-HF incident"]
    for i, title in enumerate(panels):
        ax = fig.add_axes([0.03 + i / 2, 0.17, 0.44, 0.74])
        blank(ax, (-3.2, 3.2), (-3.95, 3.95))
        ax.text(0, 3.45, title, ha="center", va="bottom", fontsize=8.5, weight="bold")
        cw, ch = (1.7, 0.8) if i == 0 else (2.4, 1.15)
        if i == 0:
            box(ax, -cw / 2, -ch / 2, cw, ch, "board", fc=PLANE, ec=INK_SECONDARY, fs=7.5)
        else:
            box(ax, -cw / 2, -ch / 2, cw, ch, None, fc=PLANE, ec=INK_SECONDARY)
            ax.text(0, ch / 2 - 0.25, "shared cache/", fontsize=7, ha="center", va="center", zorder=5)
            for k in range(3):
                yy = ch / 2 - 0.55 - 0.2 * k
                ax.plot([-cw / 2 + 0.25, -cw / 2 + 0.25 + (1.1 - 0.25 * k)], [yy, yy], color=AXIS, lw=2.0,
                        solid_capstyle="round", zorder=5)
        for (x, y), n in zip(pos, names):
            agent(ax, x, y, n, ROLES5[n] if i == 0 else "worker", r=0.62, fs=7.2)
            if i == 0:
                arrow(ax, toward(x, y, 0, 0, 0.68), edge_point(0, 0, cw, ch, x, y), rad=0.3)
                arrow(ax, edge_point(0, 0, cw, ch, x, y), toward(x, y, 0, 0, 0.68), ls=DOT, color=MUTED, rad=0.3)
            else:
                ax.add_patch(FancyArrowPatch(toward(x, y, 0, 0, 0.68), edge_point(0, 0, cw, ch, x, y),
                                             arrowstyle="<|-|>,head_length=0.36,head_width=0.21",
                                             color=INK_SECONDARY, lw=0.8, mutation_scale=10, zorder=2))
        kx, ky = -2.9, -3.2
        if i == 0:
            arrow(ax, (kx, ky), (kx + 0.7, ky))
            ax.text(kx + 0.85, ky, "check / read / post", va="center", fontsize=7.5,
                    color=INK_SECONDARY)
            arrow(ax, (kx, ky - 0.55), (kx + 0.7, ky - 0.55), ls=DOT, color=MUTED)
            ax.text(kx + 0.85, ky - 0.55, "notice: new posts, first lines", va="center", fontsize=7.5,
                    color=INK_SECONDARY)
        else:
            ax.annotate("", xy=(kx + 0.7, ky), xytext=(kx, ky),
                        arrowprops=dict(arrowstyle="<|-|>,head_length=0.36,head_width=0.21", color=INK_SECONDARY,
                                        lw=0.8))
            ax.text(kx + 0.85, ky, "list / read file / write file", va="center", fontsize=7.5, color=INK_SECONDARY)
    role_legend(fig, 0.05)
    save(fig, "topology_compare", printed_in=TEXT_WIDTH_IN)


# ---------------------------------------------------------------- the round structure
# Two rounds of five agents, drawn with N = 4 calls per round. Each lane is one agent's round: its calls in order. Every
# round uses up one of the agent's rounds and every call counts toward N, board calls included
# (src/swarm/turn_rules _turn_rounds; SwarmConfig.rounds and calls_per_round).
ROUND_CALLS = (
    {"k7q2m": ["post", "work", "work", "work"], "t4x9n": ["status", "read", "post", "wait"],
     "p8s3z": ["read", "work", "no call"], "n5j6q": ["read", "post", "work", "wait"],
     "x2m7s": ["status", "work", "job j1", "round time"]},
    {"k7q2m": ["read", "work", "wait"], "t4x9n": ["status", "read", "wait"],
     "p8s3z": ["work", "work", "post", "work"], "n5j6q": ["post", "wait"], "x2m7s": ["job_output", "submit"]},
)
SHOWN_N = 4


def live_arrow(ax, a, b):
    """A dotted line from a to b with a solid head: a read that sees a post of the same round."""
    k = 1 - 0.18 / math.hypot(b[0] - a[0], b[1] - a[1])
    mid = (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k)
    ax.plot([a[0], mid[0]], [a[1], mid[1]], color=INK, lw=0.9, ls=DOT, zorder=6)
    arrow(ax, mid, b, color=INK, lw=0.9, z=6)


def fig_round_structure():
    """Two rounds, call by call per agent: up to N calls a round, wait (the tool that ends a round), a reply with no
    tool call, a read of a post made in the same round, a command that outlives its round's time as job j1 and its read
    in the next round (command_jobs), and the submitter's exit."""
    fig = plt.figure(figsize=(FULL, 3.05))
    ax = fig.add_axes([0.0, 0.0, 1.0, 1.0])
    ax.set_xlim(0, 17.0)
    ax.set_ylim(0.55, 7.8)
    ax.axis("off")
    names = list(ROLES5)
    lane_h, top = 0.88, 6.1
    pitch, w = 1.5, 1.42
    starts = (1.2, 7.95)
    for j, n in enumerate(names):
        y = top - j * lane_h
        agent(ax, 0.55, y, n, ROLES5[n], r=0.44, fs=7.2)
        ax.plot([1.0, 16.8], [y - lane_h / 2, y - lane_h / 2], color=GRID, lw=0.5, zorder=0)
    ax.plot([1.0, 16.8], [top + lane_h / 2, top + lane_h / 2], color=GRID, lw=0.5, zorder=0)
    for x0, calls in zip(starts, ROUND_CALLS):
        for j, n in enumerate(names):
            y = top - j * lane_h
            place = 0
            for k, a in enumerate(calls[n]):
                step(ax, a, x0 + k * pitch, y - lane_h / 2 + 0.04, w, lane_h - 0.08)
                if a in ("wait", "no call", "round time"):    # no place in its round (transcript_rounds.places)
                    continue
                place += 1
                ax.text(x0 + k * pitch + w - 0.13, y + lane_h / 2 - 0.13, str(place) if place < SHOWN_N else "N",
                        ha="right", va="top", fontsize=6.9, color=MUTED, zorder=5)
    # the board is live: t4x9n's read sees k7q2m's post of the same round
    live_arrow(ax, (starts[0] + 0.72 * w, top - 0.2), (starts[0] + pitch + 0.2 * w, top - lane_h + 0.2))
    # the submitter (bottom lane) exits at its submit; the others work two more rounds
    hx = starts[1] + SHOWN_N * pitch + 0.45
    ax.add_patch(Rectangle((hx, top - 3 * lane_h - lane_h / 2 + 0.04), 16.8 - hx, 4 * lane_h - 0.08, fc="none",
                           ec=AXIS, hatch="////", lw=0, zorder=1))
    ax.text((hx + 16.8) / 2, top - 1.5 * lane_h, "two more rounds,\nthen the run ends", ha="center", va="center",
            fontsize=7, color=INK_SECONDARY, zorder=4, bbox=dict(fc=SURFACE, ec="none", pad=1.5))
    ax.text((hx + 16.8) / 2, top - 4 * lane_h, "exited", ha="center", va="center", fontsize=7, color=INK_SECONDARY,
            zorder=4)
    # each round under its bracket; each call carries its place in the round, the last one N
    hy = top + lane_h / 2 + 0.22
    for label, x0 in zip(("round r", "round r + 1"), starts):
        x1 = x0 + SHOWN_N * pitch + 0.25
        ax.text((x0 + x1) / 2, hy + 0.28, label, ha="center", va="bottom", fontsize=8, weight="bold")
        ax.plot([x0, x0, x1, x1], [hy + 0.02, hy + 0.2, hy + 0.2, hy + 0.02], color=INK_SECONDARY, lw=0.7)
    by = top - 4 * lane_h - lane_h / 2
    rows = ([("box", "work", "work call"), ("box", "read", "board call"),
             ("box", "no call", "reply with no tool call")],
            [("arrow", None, "read of a post of the same round")])
    for r, row in enumerate(rows):
        ky, kx = by - 0.38 - r * 0.5, 1.2
        for kind, a, label in row:
            if kind == "box":
                step(ax, a, kx, ky - 0.18, 0.8, 0.36, text=False)
                lx = kx + 0.95
            else:
                live_arrow(ax, (kx, ky), (kx + 0.8, ky))
                lx = kx + 0.95
            ax.text(lx, ky, label, va="center", fontsize=7, color=INK_SECONDARY)
            kx = lx + len(label) * 0.128 + 0.6
    arrow(ax, (1.2, 0.75), (16.8, 0.75), color=MUTED, lw=0.7)
    ax.text(16.8, 0.9, "time", ha="right", va="bottom", fontsize=7, color=MUTED)
    save(fig, "round_structure", printed_in=TEXT_WIDTH_IN)


BOARD_ACTIONS = {"status", "read", "post", "get_peer_actions", "get_peer_observations"}


def step(ax, a, x, y, w, h, text=True):
    """One call of the round diagram: dashed for a board call, solid grey for a work call (a job's is labelled with
    its id), white for submit, wait and a round's end on its time, dotted for a reply with no tool call."""
    if a in BOARD_ACTIONS:
        style = dict(fc=PLANE, ec=MUTED, ls=DASH, color=INK)
    elif a in ("submit", "wait", "round time"):
        style = dict(fc=WHITE, ec=INK_SECONDARY, ls="-", color=INK)
    elif a == "no call":
        style = dict(fc=SURFACE, ec=MUTED, ls=DOT, color=MUTED)
    else:
        style = dict(fc=TOOL, ec=INK_SECONDARY, ls="-", color=INK_SECONDARY)
    label = "work\n" + a if a.startswith("job ") else a
    box(ax, x, y, w, h, label if text else None, fc=style["fc"], ec=style["ec"], ls=style["ls"],
        fs=6.8 if len(a) >= 10 else 7.2,
        color=style["color"], weight="bold" if a == "submit" else "normal")


# ---------------------------------------------------------------- the context ledger
def fig_context_ledger():
    fig = plt.figure(figsize=(FULL, 4.25 * CM))
    ax = fig.add_axes([0.0, 0.0, 1.0, 1.0])
    ax.set_xlim(0, 17)
    ax.set_ylim(-0.65, 3.6)
    ax.axis("off")
    names = ["k7q2m", "t4x9n", "p8s3z"]
    seen = {
        (1, "k7q2m"): [("post", "posted #1")],
        (1, "t4x9n"): [("post", "posted #2")],
        (2, "k7q2m"): [("notice", "notice: t4x9n +1"), ("tool", "status"), ("tool", "read #2")],
        (2, "t4x9n"): [("notice", "notice: k7q2m +1"), ("tool", "status"), ("tool", "read #1")],
        (2, "p8s3z"): [("notice", "notice: k7q2m +1, t4x9n +1")],
        (3, "p8s3z"): [("tool", "status"), ("tool", "read #1, #2")],
    }
    ax.text(0.15, 3.35, "What each agent sees, round by round", fontsize=8.5, weight="bold", va="center")
    keys = [("notice", "notice (not kept)"), ("tool", "tool result (kept)"), ("post", "own post")]
    kx = 1.0
    for kind, label in keys:
        item(ax, kind, kx + 0.35, -0.35, 0.7, None)
        ax.text(kx + 0.85, -0.35, label, va="center", fontsize=7, color=INK_SECONDARY)
        kx += 0.85 + len(label) * 0.118 + 0.6
    x0, cw = 1.0, 5.3
    for r in (1, 2, 3):
        ax.text(x0 + (r - 1) * cw + cw / 2, 2.82, f"round {r}", ha="center", va="center", fontsize=7.5,
                color=INK_SECONDARY)
    for j, n in enumerate(names):
        y = 2.25 - j * 0.85
        agent(ax, 0.5, y, n, "worker", r=0.27, fs=6)
        for r in (1, 2, 3):
            cx = x0 + (r - 1) * cw
            box(ax, cx + 0.08, y - 0.36, cw - 0.16, 0.72, None, fc="none", ec=GRID, lw=0.7, pad=0.02, z=1)
            items = seen.get((r, n), [])
            if not items:
                ax.text(cx + cw / 2, y, "no board action", ha="center", va="center", fontsize=6.8, color=MUTED)
                continue
            bx = cx + 0.2
            for kind, text in items:
                bw = len(text) * 0.105 + 0.35
                item(ax, kind, bx + bw / 2, y, bw, text)
                bx += bw + 0.07
    save(fig, "context_ledger")


def item(ax, kind, cx, cy, w, text):
    """One context entry of the ledger, centred at (cx, cy)."""
    style = {"notice": dict(fc=WHITE, ec=MUTED, ls=DASH, color=INK_SECONDARY),
             "tool": dict(fc=TOOL, ec=INK_SECONDARY, ls="-", color=INK),
             "post": dict(fc=WHITE, ec=INK_SECONDARY, ls="-", color=INK)}[kind]
    h = 0.48 if text else 0.34
    box(ax, cx - w / 2, cy - h / 2, w, h, text, fc=style["fc"], ec=style["ec"], ls=style["ls"], color=style["color"],
        fs=6.6, pad=0.02)


# ---------------------------------------------------------------- the engagement funnel
def fig_engagement_funnel():
    """The board engagement stages (src/measures/board_engagement.py) and the tool each one takes."""
    fig = plt.figure(figsize=(FULL, 1.45))
    ax = fig.add_axes([0.0, 0.0, 1.0, 1.0])
    ax.set_xlim(0, 17)
    ax.set_ylim(0, 3.7)
    ax.axis("off")
    ax.text(0.15, 3.3, "Board engagement funnel", fontsize=8.5, weight="bold", va="center")
    stages = [("on the board", "has the board tools"), ("looked", "board_status\nor board_read"),
              ("read", "board_read"), ("posted", "board_post"), ("submitted", "submit")]
    sw, gap, y0, h, tip = 3.26, 0.1, 0.3, 2.4, 0.45
    for i, (name, tool) in enumerate(stages):
        x = 0.15 + i * (sw + gap)
        pts = [(x, y0), (x + sw - tip, y0), (x + sw, y0 + h / 2), (x + sw - tip, y0 + h), (x, y0 + h)]
        if i > 0:
            pts.append((x + tip, y0 + h / 2))
        ax.add_patch(Polygon(pts, closed=True, fc=GREY_RAMP[i], ec="none", zorder=2))
        tx = x + sw / 2 + (0.12 if i else -0.12)
        ax.text(tx, y0 + h / 2 + 0.45, name, ha="center", va="center", fontsize=8, weight="bold")
        ax.text(tx, y0 + h / 2 - 0.45, tool, ha="center", va="center", fontsize=6.5, color=INK_SECONDARY,
                linespacing=1.2)
    save(fig, "engagement_funnel")


# ---------------------------------------------------------------- the read graph
def fig_read_graph():
    """Who read whose posts, as a graph: reader to sender, width by reads. Illustrative values."""
    fig = plt.figure(figsize=(HALF, 2.4))
    gx = fig.add_axes([0.02, 0.02, 0.96, 0.96])
    blank(gx, (-2.6, 2.6), (-2.3, 2.2))
    pos = dict(zip(ROLES5, ring(5, 1.75)))
    reads = {("k7q2m", "t4x9n"): 4, ("t4x9n", "k7q2m"): 1, ("p8s3z", "t4x9n"): 3, ("x2m7s", "t4x9n"): 2,
             ("n5j6q", "k7q2m"): 2, ("t4x9n", "n5j6q"): 1, ("x2m7s", "p8s3z"): 1, ("k7q2m", "x2m7s"): 1}
    for (reader, sender), count in reads.items():
        (x1, y1), (x2, y2) = pos[reader], pos[sender]
        arrow(gx, toward(x1, y1, x2, y2, 0.36), toward(x2, y2, x1, y1, 0.42), lw=0.6 + 0.65 * count, rad=0.18,
              color=INK_SECONDARY, head=4 + count)
    for name, (x, y) in pos.items():
        agent(gx, x, y, name, ROLES5[name], r=0.32, fs=6)
    save(fig, "read_graph")


# ---------------------------------------------------------------- the access split
def fig_access():
    """access=split: the exploit cut into one piece per worker, each in its own box; the regulator and the
    disregulator outside with nothing and without submit; everyone on the board, the workers' pieces into the
    submitted work."""
    fig = plt.figure(figsize=(HALF, 2.3))
    ax = fig.add_axes([0.02, 0.17, 0.96, 0.82])
    blank(ax, (-3.1, 3.1), (-2.9, 2.75))
    bw, bh, by = 1.6, 0.6, 2.3
    box(ax, -bw / 2, by - bh / 2, bw, bh, "board", fc=PLANE, ec=INK_SECONDARY, fs=7.5)
    # the pieces as the code deals them: round robin over the neutral agents, nothing for a role
    names = list(ROLES5)
    held = access_policy.plan("split", names, ROLES5, lambda n: [f"piece {k}" for k in range(1, n + 1)], ["whole"])
    workers = [a for a in names if held[a]]
    roles = [a for a in names if not held[a]]
    xs = {a: x for a, x in zip(workers, [-1.3, 0.0, 1.3])}
    xs.update({a: x for a, x in zip(roles, [-2.6, 2.6])})
    y = 0.15
    for a in workers:
        x = xs[a]
        box(ax, x - 0.55, y - 0.65, 1.1, 1.45, None, fc=WHITE, ec=INK_SECONDARY, z=1)
        ax.text(x, y + 0.55, held[a][0], ha="center", va="center", fontsize=6.8)
        agent(ax, x, y - 0.1, a, ROLES5[a], r=0.34, fs=6)
        ax.scatter([x + 0.3], [y + 0.2], marker="D", s=14, c=INK, zorder=9, edgecolors=SURFACE, linewidths=0.6)
        arrow(ax, (x, y + 0.8), edge_point(0, by, bw, bh, x, y + 0.8), lw=0.7, color=MUTED, head=4)
        arrow(ax, (x, y - 0.65), (x * 0.35, -1.75), lw=0.7, color=MUTED, head=4)
    for a in roles:
        # level with the boxes' tops, so the arrow to the board clears the corner of the nearest box
        x, ry = xs[a], y + 0.75
        agent(ax, x, ry, a, ROLES5[a], r=0.34, fs=6)
        arrow(ax, toward(x, ry, 0, by, 0.4), edge_point(0, by, bw, bh, x, ry), lw=0.7, color=MUTED, head=4)
    box(ax, -1.3, -2.45, 2.6, 0.65, "submitted work", fc=PLANE, ec=INK_SECONDARY, fs=7)

    def dmark(ax, x, y):
        ax.scatter([x], [y], marker="D", s=16, c=INK, zorder=3)

    role_legend(fig, 0.07, extra=[(dmark, "submit")], compact=True)
    save(fig, "access")


# ---------------------------------------------------------------- what each role is told
def fig_information():
    fig = plt.figure(figsize=(FULL, 1.75))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 17)
    ax.set_ylim(0, 4.45)
    ax.axis("off")
    ax.text(0.2, 4.15, "What each role is told, and what it holds", va="center", fontsize=8.5, weight="bold")
    cols = ["told a\nboard exists", "has the\nboard tools", "told the\nroster", "can read\nteammates' tool calls",
            "holds the\nexploit"]
    rows = ["worker", "regulator", "disregulator"]
    # Y yes in the default preset (mention_board on since 2026-11-22); P a part only (access split); N no
    marks = {
        "worker": ["Y", "Y", "Y", "N", "P"],
        "regulator": ["Y", "Y", "Y", "Y", "N"],
        "disregulator": ["Y", "Y", "Y", "Y", "N"],
    }
    x0, cw, top, rh = 3.0, 2.7, 2.55, 0.72
    for j, c in enumerate(cols):
        ax.text(x0 + j * cw + cw / 2, top + 0.5, c, ha="center", va="bottom", fontsize=7, color=INK_SECONDARY,
                linespacing=1.05)
    for i, r in enumerate(rows):
        y = top - i * rh
        ax.scatter([0.4], [y], s=40, c=ROLE_FILL[r], edgecolors=ROLE_EDGE[r], linewidths=1.0, zorder=3)
        ax.text(0.7, y, r, va="center", fontsize=7.5)
        for j, v in enumerate(marks[r]):
            glyph(ax, v, x0 + j * cw + cw / 2, y, 0.4)
    ky = top - 2 * rh - 0.65
    for kx, v, label in [(0.4, "Y", "yes"), (2.0, "P", "a part only")]:
        glyph(ax, v, kx + 0.13, ky, 0.26)
        ax.text(kx + 0.4, ky, label, va="center", fontsize=6.8, color=INK_SECONDARY)
    save(fig, "information")


def glyph(ax, v, cx, cy, s):
    """Y a filled square, F an outlined square, P a half-filled square, N a dash."""
    style = "round,pad=0,rounding_size=0.05"
    if v == "N":
        ax.plot([cx - 0.09, cx + 0.09], [cy, cy], color=AXIS, lw=1.0)
        return
    if v == "P":
        ax.add_patch(Rectangle((cx - s / 2, cy - s / 2), s / 2, s, fc=INK_SECONDARY, ec="none", zorder=3))
    ax.add_patch(FancyBboxPatch((cx - s / 2, cy - s / 2), s, s, boxstyle=style,
                                fc=INK_SECONDARY if v == "Y" else "none", ec=INK_SECONDARY, lw=1.0, zorder=3))


FIGURES = (fig_topology_compare, fig_round_structure, fig_context_ledger, fig_engagement_funnel, fig_read_graph,
           fig_access, fig_information)


def main() -> int:
    """Draw every figure and print the paths written."""
    for draw in FIGURES:
        draw()
    for path in sorted(OUT.glob("*.png")):
        progress.wrote(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
