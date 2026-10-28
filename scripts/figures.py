"""The six design figures of the paper, as 200-dpi PNG under tmp/ (gitignored).

    uv run python scripts/figures.py

No network, no model calls.
"""

import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Polygon, Rectangle  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tmp"
sys.path.insert(0, str(ROOT))

from src.configuration import access as access_policy  # noqa: E402

# Palette: reference dataviz palette, light mode. Roles take the first two categorical slots;
# workers are neutral (white fill, ink ring). Structures are neutral greys.
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASE = "#c3c2b7"
SURF = "#fcfcfb"
PLANE = "#f0efec"
TOOL = "#e6e5df"
REG = "#2a78d6"
DIS = "#eb6834"
ROLE_FILL = {"worker": "#ffffff", "regulator": REG, "disregulator": DIS}
ROLE_EDGE = {"worker": INK2, "regulator": REG, "disregulator": DIS}
ROLE_TEXT = {"worker": INK, "regulator": "#ffffff", "disregulator": "#ffffff"}
DASH = (0, (2, 1.5))
DOT = (0, (1, 1.6))

CM = 1 / 2.54
FULL = 17 * CM
HALF = 8.5 * CM

plt.rcParams.update(
    {
        "font.family": "Arial",
        "font.size": 8,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "figure.facecolor": SURF,
        "axes.facecolor": SURF,
        "savefig.facecolor": SURF,
        "text.color": INK,
        "hatch.linewidth": 0.6,
    }
)

ROLES5 = {"a01": "worker", "a02": "regulator", "a03": "worker", "a04": "disregulator", "a05": "worker"}


def save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / f"{name}.png", dpi=200)
    plt.close(fig)


def blank(ax, xlim, ylim):
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_aspect("equal")
    ax.axis("off")


def agent(ax, x, y, name, role="worker", r=0.36, fs=6.5, z=5):
    ax.add_patch(Circle((x, y), r, fc=ROLE_FILL[role], ec=ROLE_EDGE[role], lw=1.0, zorder=z))
    ax.text(x, y, name, ha="center", va="center", fontsize=fs, color=ROLE_TEXT[role], zorder=z + 1)


def box(ax, x, y, w, h, text=None, fc="#ffffff", ec=INK2, lw=0.8, ls="-", hatch=None, fs=7,
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


def arrow(ax, a, b, color=INK2, lw=0.8, ls="-", rad=0.0, sa=0, sb=0, z=2, head=6):
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
        ax.text(x + 1.4 * k, 0, label, va="center", fontsize=7.5, color=INK2)
        x += w
    for (draw, label), w in zip(extra, widths[len(items):]):
        draw(ax, x, 0)
        ax.text(x + 1.4 * k, 0, label, va="center", fontsize=7.5, color=INK2)
        x += w
    return ax


# ---------------------------------------------------------------- figure 1: topologies
def fig_topology_compare():
    fig = plt.figure(figsize=(FULL, 2.85))
    names = list(ROLES5)
    pos = ring(5, 2.4)
    panels = ["Pull board (ours)", "Incident (METR 2026)"]
    for i, title in enumerate(panels):
        ax = fig.add_axes([0.03 + i / 2, 0.17, 0.44, 0.74])
        blank(ax, (-3.2, 3.2), (-3.95, 3.45))
        ax.text(0, 3.0, title, ha="center", va="bottom", fontsize=8.5, weight="bold")
        cw, ch = (1.7, 0.8) if i == 0 else (2.1, 1.15)
        if i == 0:
            box(ax, -cw / 2, -ch / 2, cw, ch, "board", fc=PLANE, ec=INK2, fs=7.5)
        else:
            box(ax, -cw / 2, -ch / 2, cw, ch, None, fc=PLANE, ec=INK2)
            ax.text(0, ch / 2 - 0.25, "shared cache/", fontsize=6.5, ha="center", va="center", zorder=5)
            for k in range(3):
                yy = ch / 2 - 0.55 - 0.2 * k
                ax.plot([-cw / 2 + 0.25, -cw / 2 + 0.25 + (1.1 - 0.25 * k)], [yy, yy], color=BASE, lw=2.0,
                        solid_capstyle="round", zorder=5)
        for (x, y), n in zip(pos, names):
            agent(ax, x, y, n, ROLES5[n] if i == 0 else "worker")
            if i == 0:
                arrow(ax, toward(x, y, 0, 0, 0.42), edge_point(0, 0, cw, ch, x, y), rad=0.3)
                arrow(ax, edge_point(0, 0, cw, ch, x, y), toward(x, y, 0, 0, 0.42), ls=DOT, color=MUTED, rad=0.3)
            else:
                ax.add_patch(FancyArrowPatch(toward(x, y, 0, 0, 0.42), edge_point(0, 0, cw, ch, x, y),
                                             arrowstyle="<|-|>,head_length=0.36,head_width=0.21",
                                             color=INK2, lw=0.8, mutation_scale=10, zorder=2))
        kx, ky = -2.9, -3.2
        if i == 0:
            arrow(ax, (kx, ky), (kx + 0.7, ky))
            ax.text(kx + 0.85, ky, "board_status / board_read / board_post", va="center", fontsize=7.5, color=INK2)
            arrow(ax, (kx, ky - 0.55), (kx + 0.7, ky - 0.55), ls=DOT, color=MUTED)
            ax.text(kx + 0.85, ky - 0.55, "notice: new posts per sender", va="center", fontsize=7.5, color=INK2)
        else:
            ax.annotate("", xy=(kx + 0.7, ky), xytext=(kx, ky),
                        arrowprops=dict(arrowstyle="<|-|>,head_length=0.36,head_width=0.21", color=INK2, lw=0.8))
            ax.text(kx + 0.85, ky, "list / read file / write file", va="center", fontsize=7.5, color=INK2)
    role_legend(fig, 0.05)
    save(fig, "topology_compare")


# ---------------------------------------------------------------- figure 2: round structure
def fig_round_structure():
    """One round, its follow-up steps and the next round, step by step per agent."""
    fig = plt.figure(figsize=(FULL, 2.3))
    ax = fig.add_axes([0.0, 0.0, 1.0, 1.0])
    ax.set_xlim(0, 17.0)
    ax.set_ylim(0.6, 6.4)
    ax.axis("off")
    names = list(ROLES5)
    lane_h, top = 0.62, 5.05
    cols = [("r", 1.2, 3.6), ("f1", 5.0, 1.45), ("f2", 6.55, 1.45), ("f3", 8.1, 1.45), ("r1", 9.85, 3.6)]
    acts = {
        "r": {"a01": "post", "a02": "work", "a03": "status", "a04": "work", "a05": "read"},
        "f1": {"a01": "work", "a03": "read", "a05": "post"},
        "f2": {"a03": "post", "a05": "work"},
        "f3": {"a03": "read"},
        "r1": {"a01": "work", "a02": "agent_actions", "a03": "work", "a04": "read", "a05": "team_submit"},
    }
    for j, n in enumerate(names):
        y = top - j * lane_h
        agent(ax, 0.6, y, n, ROLES5[n], r=0.24, fs=5.8)
        ax.plot([1.0, 15.8], [y - lane_h / 2, y - lane_h / 2], color=GRID, lw=0.5, zorder=0)
    ax.plot([1.0, 15.8], [top + lane_h / 2, top + lane_h / 2], color=GRID, lw=0.5, zorder=0)
    for key, x, w in cols:
        for j, n in enumerate(names):
            y = top - j * lane_h
            a = acts[key].get(n)
            if a is None:
                continue
            step(ax, a, x, y - lane_h / 2 + 0.04, w, lane_h - 0.08)
    ax.add_patch(Rectangle((13.6, top - 4 * lane_h - lane_h / 2 + 0.04), 2.2, 5 * lane_h - 0.08, fc="none",
                           ec=BASE, hatch="////", lw=0, zorder=1))
    ax.text(14.7, top - 2 * lane_h, "run over", ha="center", va="center", fontsize=7, color=INK2, zorder=4,
            bbox=dict(fc=SURF, ec="none", pad=1.5))
    # a round is each agent's step and, for an agent whose step ended with a board action, its free follow-up steps
    hy = top + lane_h / 2 + 0.12
    for label, x0, x1 in (("round r", 1.2, 9.55), ("round r + 1", 9.85, 13.45)):
        ax.text((x0 + x1) / 2, hy + 0.28, label, ha="center", va="bottom", fontsize=8, weight="bold")
        ax.plot([x0, x0, x1, x1], [hy + 0.02, hy + 0.2, hy + 0.2, hy + 0.02], color=INK2, lw=0.7)
    by = top - 4 * lane_h - lane_h / 2 - 0.15
    ky, kx = by - 0.35, 1.2
    for a, label in [("work", "charged step"), ("read", "free step")]:
        step(ax, a, kx, ky - 0.2, 0.8, 0.4, text=False)
        ax.text(kx + 0.95, ky, label, va="center", fontsize=7, color=INK2)
        kx += 0.95 + len(label) * 0.128 + 0.45
    arrow(ax, (1.2, 0.8), (15.8, 0.8), color=MUTED, lw=0.7)
    ax.text(15.8, 0.95, "time", ha="right", va="bottom", fontsize=7, color=MUTED)
    save(fig, "round_structure")


BOARD_ACTIONS = {"status", "read", "post", "agent_actions"}


def step(ax, a, x, y, w, h, text=True):
    """One step of the round diagram: dashed if it ends with a board action (free), solid grey if charged."""
    if a in BOARD_ACTIONS:
        style = dict(fc=PLANE, ec=MUTED, ls=DASH, color=INK)
    elif a == "team_submit":
        style = dict(fc="#ffffff", ec=INK2, ls="-", color=INK)
    else:
        style = dict(fc=TOOL, ec=INK2, ls="-", color=INK2)
    box(ax, x, y, w, h, a if text else None, fc=style["fc"], ec=style["ec"], ls=style["ls"], fs=6.5,
        color=style["color"], weight="bold" if a == "team_submit" else "normal")


# ---------------------------------------------------------------- figure 3: context ledger
def fig_context_ledger():
    fig = plt.figure(figsize=(FULL, 4.25 * CM))
    ax = fig.add_axes([0.0, 0.0, 1.0, 1.0])
    ax.set_xlim(0, 17)
    ax.set_ylim(-0.65, 3.6)
    ax.axis("off")
    names = ["a01", "a02", "a03"]
    seen = {
        (1, "a01"): [("post", "posted #1")],
        (1, "a02"): [("post", "posted #2")],
        (2, "a01"): [("notice", "notice: a02 +1"), ("tool", "status"), ("tool", "read #2")],
        (2, "a02"): [("notice", "notice: a01 +1"), ("tool", "status"), ("tool", "read #1")],
        (2, "a03"): [("notice", "notice: a01 +1, a02 +1")],
        (3, "a03"): [("tool", "status"), ("tool", "read #1, #2")],
    }
    ax.text(0.15, 3.35, "What each agent sees, round by round", fontsize=8.5, weight="bold", va="center")
    keys = [("notice", "notice (not kept)"), ("tool", "tool result (kept)"), ("post", "own post")]
    kx = 1.0
    for kind, label in keys:
        item(ax, kind, kx + 0.35, -0.35, 0.7, None)
        ax.text(kx + 0.85, -0.35, label, va="center", fontsize=7, color=INK2)
        kx += 0.85 + len(label) * 0.118 + 0.6
    x0, cw = 1.0, 5.3
    for r in (1, 2, 3):
        ax.text(x0 + (r - 1) * cw + cw / 2, 2.82, f"round {r}", ha="center", va="center", fontsize=7.5, color=INK2)
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
    style = {"notice": dict(fc="#ffffff", ec=MUTED, ls=DASH, color=INK2),
             "tool": dict(fc=TOOL, ec=INK2, ls="-", color=INK),
             "post": dict(fc="#ffffff", ec=INK2, ls="-", color=INK)}[kind]
    h = 0.48 if text else 0.34
    box(ax, cx - w / 2, cy - h / 2, w, h, text, fc=style["fc"], ec=style["ec"], ls=style["ls"], color=style["color"],
        fs=6.6, pad=0.02)


# ---------------------------------------------------------------- figure 4: engagement funnel
def fig_engagement_funnel():
    """The board engagement stages (src/measures/engagement.py) and the tool each one takes."""
    fig = plt.figure(figsize=(FULL, 1.45))
    ax = fig.add_axes([0.0, 0.0, 1.0, 1.0])
    ax.set_xlim(0, 17)
    ax.set_ylim(0, 3.7)
    ax.axis("off")
    ax.text(0.15, 3.3, "Board engagement funnel", fontsize=8.5, weight="bold", va="center")
    stages = [("on the board", "has the board tools"), ("looked", "board_status\nor board_read"),
              ("read", "board_read"), ("posted", "board_post"), ("submitted", "team_submit")]
    fills = ["#efeee9", "#e1e0d9", "#d4d3cc", "#c7c6be", "#b9b8b0"]
    sw, gap, y0, h, tip = 3.26, 0.1, 0.3, 2.4, 0.45
    for i, (name, tool) in enumerate(stages):
        x = 0.15 + i * (sw + gap)
        pts = [(x, y0), (x + sw - tip, y0), (x + sw, y0 + h / 2), (x + sw - tip, y0 + h), (x, y0 + h)]
        if i > 0:
            pts.append((x + tip, y0 + h / 2))
        ax.add_patch(Polygon(pts, closed=True, fc=fills[i], ec="none", zorder=2))
        tx = x + sw / 2 + (0.12 if i else -0.12)
        ax.text(tx, y0 + h / 2 + 0.45, name, ha="center", va="center", fontsize=8, weight="bold")
        ax.text(tx, y0 + h / 2 - 0.45, tool, ha="center", va="center", fontsize=6.5, color=INK2, linespacing=1.2)
    save(fig, "engagement_funnel")


def fig_read_graph():
    """Who read whose posts, as a graph: reader to sender, width by reads. Illustrative values."""
    fig = plt.figure(figsize=(HALF, 2.4))
    gx = fig.add_axes([0.02, 0.02, 0.96, 0.96])
    blank(gx, (-2.6, 2.6), (-2.3, 2.2))
    pos = dict(zip(ROLES5, ring(5, 1.75)))
    reads = {("a01", "a02"): 4, ("a02", "a01"): 1, ("a03", "a02"): 3, ("a05", "a02"): 2, ("a04", "a01"): 2,
             ("a02", "a04"): 1, ("a05", "a03"): 1, ("a01", "a05"): 1}
    for (reader, sender), count in reads.items():
        (x1, y1), (x2, y2) = pos[reader], pos[sender]
        arrow(gx, toward(x1, y1, x2, y2, 0.36), toward(x2, y2, x1, y1, 0.42), lw=0.6 + 0.65 * count, rad=0.18,
              color=INK2, head=4 + count)
    for name, (x, y) in pos.items():
        agent(gx, x, y, name, ROLES5[name], r=0.32, fs=6)
    save(fig, "read_graph")


# ---------------------------------------------------------------- figure 5: access
def fig_access():
    """access=split: the exploit cut into one piece per worker, each in its own box; the regulator and the
    disregulator outside with nothing; everyone on the board and the team's main."""
    fig = plt.figure(figsize=(HALF, 2.3))
    ax = fig.add_axes([0.02, 0.17, 0.96, 0.82])
    blank(ax, (-3.1, 3.1), (-2.9, 2.75))
    bw, bh, by = 1.6, 0.6, 2.3
    box(ax, -bw / 2, by - bh / 2, bw, bh, "board", fc=PLANE, ec=INK2, fs=7.5)
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
        box(ax, x - 0.55, y - 0.65, 1.1, 1.45, None, fc="#ffffff", ec=INK2, z=1)
        ax.text(x, y + 0.55, held[a][0], ha="center", va="center", fontsize=6.8)
        agent(ax, x, y - 0.1, a, ROLES5[a], r=0.34, fs=6)
        ax.scatter([x + 0.3], [y + 0.2], marker="D", s=14, c=INK, zorder=9, edgecolors=SURF, linewidths=0.6)
        arrow(ax, (x, y + 0.8), edge_point(0, by, bw, bh, x, y + 0.8), lw=0.7, color=MUTED, head=4)
        arrow(ax, (x, y - 0.65), (x * 0.35, -1.75), lw=0.7, color=MUTED, head=4)
    for a in roles:
        x = xs[a]
        agent(ax, x, y - 0.1, a, ROLES5[a], r=0.34, fs=6)
        arrow(ax, toward(x, y - 0.1, 0, by, 0.4), edge_point(0, by, bw, bh, x, y - 0.1), lw=0.7, color=MUTED, head=4)
        arrow(ax, toward(x, y - 0.1, x * 0.35, -1.75, 0.4), (x * 0.35, -1.75), lw=0.7, color=MUTED, head=4)
    box(ax, -1.0, -2.45, 2.0, 0.65, "team's main", fc=PLANE, ec=INK2, fs=7)

    def dmark(ax, x, y):
        ax.scatter([x], [y], marker="D", s=16, c=INK, zorder=3)

    role_legend(fig, 0.07, extra=[(dmark, "team_submit")], compact=True)
    save(fig, "access")


# ---------------------------------------------------------------- figure 6: information
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
    # Y yes in the default preset; F only if mention_board is on; P a part only (access split); N no
    marks = {
        "worker": ["F", "Y", "Y", "N", "P"],
        "regulator": ["Y", "Y", "Y", "Y", "N"],
        "disregulator": ["Y", "Y", "Y", "N", "N"],
    }
    x0, cw, top, rh = 3.0, 2.7, 2.55, 0.72
    for j, c in enumerate(cols):
        ax.text(x0 + j * cw + cw / 2, top + 0.5, c, ha="center", va="bottom", fontsize=7, color=INK2,
                linespacing=1.05)
    for i, r in enumerate(rows):
        y = top - i * rh
        ax.scatter([0.4], [y], s=40, c=ROLE_FILL[r], edgecolors=ROLE_EDGE[r], linewidths=1.0, zorder=3)
        ax.text(0.7, y, r, va="center", fontsize=7.5)
        for j, v in enumerate(marks[r]):
            glyph(ax, v, x0 + j * cw + cw / 2, y, 0.4)
    ky = top - 2 * rh - 0.65
    for kx, v, label in [(0.4, "Y", "yes"), (2.0, "F", "only with mention_board on"), (5.6, "P", "a part only")]:
        glyph(ax, v, kx + 0.13, ky, 0.26)
        ax.text(kx + 0.4, ky, label, va="center", fontsize=6.8, color=INK2)
    save(fig, "information")


def glyph(ax, v, cx, cy, s):
    """Y a filled square, F an outlined square, P a half-filled square, N a dash."""
    style = "round,pad=0,rounding_size=0.05"
    if v == "N":
        ax.plot([cx - 0.09, cx + 0.09], [cy, cy], color=BASE, lw=1.0)
        return
    if v == "P":
        ax.add_patch(Rectangle((cx - s / 2, cy - s / 2), s / 2, s, fc=INK2, ec="none", zorder=3))
    ax.add_patch(FancyBboxPatch((cx - s / 2, cy - s / 2), s, s, boxstyle=style, fc=INK2 if v == "Y" else "none",
                                ec=INK2, lw=1.0, zorder=3))


if __name__ == "__main__":
    fig_topology_compare()
    fig_round_structure()
    fig_context_ledger()
    fig_engagement_funnel()
    fig_read_graph()
    fig_access()
    fig_information()
    print("\n".join(str(p) for p in sorted(OUT.glob("*.png"))))
