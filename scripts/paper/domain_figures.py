"""One setup figure per task, as 200-dpi PNG under tmp/setup/ (gitignored).

    uv run python -m scripts.paper.domain_figures

Each figure tells one story in pictures: the honeypot breaks into puzzle pieces, access=split hands one piece to
each agent inside its sandbox, the pieces can meet only on the board, and together they rebuild the honeypot.
Words are kept to names and labels. Every text is measured against its box after drawing, and the build fails on a
spill. No network, no model calls.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Polygon, Rectangle, Wedge

from scripts.paper.figures import CM, DASH
from src.configuration.settings import ROOT
from src.utils.figure_style import (
    HONEY,
    HONEY_FILL,
    INK,
    INK_SECONDARY,
    MUTED,
    PLANE,
    PRINT_DPI,
    SURFACE,
    WHITE,
)

OUT = ROOT / "tmp" / "setup"
# Tints of HONEY for the drawings: the split's three pieces, light to dark, with the text each takes, and two mid
# tones for the icons' lines.
PIECE_FILL = ["#f2dcb4", "#e4b86e", "#c98f3a"]
PIECE_TEXT = [HONEY, HONEY, WHITE]
ICON_LINE, ICON_RUNG = "#d9b88a", "#c9a46c"
AGENTS = ["a01", "a02", "a03"]
XS = [7.4, 10.4, 13.4]                                # the agents' columns
W, H = 20.0, 9.0                                      # data units are centimetres
MIN_FS = 5.5


class Canvas:
    """A figure whose texts remember the box they must fit; check() shrinks each until it fits or fails."""

    def __init__(self):
        self.fig = plt.figure(figsize=(W * CM, H * CM))
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set_xlim(0, W)
        self.ax.set_ylim(0, H)
        self.ax.set_aspect("equal")
        self.ax.axis("off")
        self.fits: list[tuple] = []

    def text(self, x, y, s, box=None, **kw):
        t = self.ax.text(x, y, s, **kw)
        if box:
            self.fits.append((t, box))
        return t

    def check(self, name):
        self.fig.canvas.draw()
        renderer = self.fig.canvas.get_renderer()
        inv = self.ax.transData.inverted()
        bad = []
        for t, (x0, y0, x1, y1) in self.fits:
            while True:
                bb = t.get_window_extent(renderer).transformed(inv)
                if bb.x0 >= x0 - 0.02 and bb.x1 <= x1 + 0.02 and bb.y0 >= y0 - 0.02 and bb.y1 <= y1 + 0.02:
                    break
                if t.get_fontsize() <= MIN_FS:
                    bad.append(f"{name}: {t.get_text()!r} does not fit {x0}..{x1} x {y0}..{y1}")
                    break
                t.set_fontsize(t.get_fontsize() - 0.25)
        if bad:
            raise ValueError("\n".join(bad))


# ---------------------------------------------------------------- shapes
def puzzle(ax, x, y, s, left, right, fill, z=4, edge=HONEY, lw=0.9):
    """A square puzzle piece with its lower-left corner at (x, y): a knob (+1), a socket (-1) or a flat edge (0) on
    its left and right sides."""
    r = s * 0.17
    pts = [(x, y), (x + s, y)]

    def side(x0, y0, x1, y1, kind):
        if not kind:
            return [(x1, y1)]
        tx, ty = (x1 - x0) / s, (y1 - y0) / s           # along the edge
        nx, ny = ty, -tx                                  # outward normal for a counter-clockwise walk
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        cx, cy = mx + nx * kind * r * 0.75, my + ny * kind * r * 0.75
        out = [(mx - tx * r * 0.55, my - ty * r * 0.55)]
        for k in range(25):
            a = math.radians(215 - 250 * k / 24)          # sweep across the knob
            ca, sa = math.cos(a), math.sin(a)
            out.append((cx + tx * r * ca + nx * kind * r * sa, cy + ty * r * ca + ny * kind * r * sa))
        out += [(mx + tx * r * 0.55, my + ty * r * 0.55), (x1, y1)]
        return out

    pts += side(x + s, y, x + s, y + s, right)[0:]
    pts += [(x, y + s)]
    pts += side(x, y + s, x, y, left)
    ax.add_patch(Polygon(pts, closed=True, fc=fill, ec=edge, lw=lw, joinstyle="round", zorder=z))


def tabs(i, n):
    """The left and right edges of piece i of n laid side by side: each joint is a knob into a socket."""
    return (-1 if i > 0 else 0), (1 if i < n - 1 else 0)


def label(c, x, y, s, left, text, i, z=6):
    """A piece's label, centred on the part of the piece a socket on its left leaves whole."""
    x0 = x + s * 0.32 if left < 0 else x + s * 0.06      # a socket reaches about a third into the piece
    c.text((x0 + x + s * 0.96) / 2, y + s / 2, text, (x0, y, x + s * 0.96, y + s), fontsize=8.5,
           color=PIECE_TEXT[i], ha="center", va="center", weight="bold", zorder=z)


def page(ax, cx, cy, w, h, z=3):
    fold = w * 0.22
    x, y = cx - w / 2, cy - h / 2
    ax.add_patch(Polygon([(x, y), (x + w, y), (x + w, y + h - fold), (x + w - fold, y + h), (x, y + h)], closed=True,
                         fc=HONEY_FILL, ec=HONEY, lw=1.2, joinstyle="round", zorder=z))
    ax.add_patch(Polygon([(x + w - fold, y + h), (x + w - fold, y + h - fold), (x + w, y + h - fold)], closed=True,
                         fc=WHITE, ec=HONEY, lw=1.2, joinstyle="round", zorder=z + 1))


def icon(ax, kind, cx, cy, s):
    """The honeypot as a picture: an archive, a leaked patch, a slide deck, an answer key or designed proteins."""
    if kind == "key":
        ax.add_patch(Circle((cx - s * 0.28, cy), s * 0.22, fc=HONEY_FILL, ec=HONEY, lw=1.6, zorder=3))
        ax.add_patch(Circle((cx - s * 0.28, cy), s * 0.08, fc=SURFACE, ec=HONEY, lw=1.2, zorder=4))
        ax.add_patch(Rectangle((cx - s * 0.07, cy - s * 0.045), s * 0.5, s * 0.09, fc=HONEY_FILL, ec=HONEY, lw=1.4,
                               zorder=3))
        for dx, depth in ((0.25, 0.16), (0.36, 0.11)):
            ax.add_patch(Rectangle((cx + s * dx, cy - s * (0.045 + depth)), s * 0.07, s * depth, fc=HONEY_FILL,
                                   ec=HONEY, lw=1.2, zorder=3))
        return
    page(ax, cx, cy, s * 0.72, s * 0.9)
    if kind == "zip":                                   # a zipper down the middle
        for k in range(7):
            y = cy + s * 0.3 - k * s * 0.09
            ax.add_patch(Rectangle((cx - s * 0.06 + (k % 2) * s * 0.06, y), s * 0.06, s * 0.05, fc=HONEY, ec="none",
                                   zorder=5))
        ax.add_patch(FancyBboxPatch((cx - s * 0.07, cy - s * 0.38), s * 0.14, s * 0.12,
                                    boxstyle="round,pad=0,rounding_size=0.03", fc=HONEY, ec="none", zorder=5))
    elif kind == "patch":                               # diff lines, added in a darker tone
        for k, (w, add) in enumerate(((0.42, 0), (0.3, 1), (0.38, 1), (0.22, 0), (0.34, 1), (0.26, 0))):
            y = cy + s * 0.27 - k * s * 0.11
            ax.text(cx - s * 0.27, y, "+" if add else " ", fontsize=6, color=HONEY, va="center", ha="center",
                    family="DejaVu Sans Mono", zorder=5)
            ax.add_patch(Rectangle((cx - s * 0.2, y - s * 0.025), s * w, s * 0.05, fc=HONEY if add else ICON_LINE,
                                   ec="none", zorder=5))
    elif kind == "deck":                                # a slide with a bar chart
        ax.add_patch(Rectangle((cx - s * 0.25, cy - s * 0.18), s * 0.5, s * 0.42, fc=WHITE, ec=HONEY, lw=0.9,
                               zorder=5))
        for k, h in enumerate((0.12, 0.22, 0.17, 0.3)):
            ax.add_patch(Rectangle((cx - s * 0.19 + k * s * 0.11, cy - s * 0.13), s * 0.07, s * h, fc=HONEY,
                                   ec="none", zorder=6))
        for k in range(2):
            ax.add_patch(Rectangle((cx - s * 0.25, cy - s * (0.3 + k * 0.08)), s * (0.5 - k * 0.15), s * 0.035,
                                   fc=ICON_LINE, ec="none", zorder=5))
    elif kind == "helix":                               # two strands with rungs
        xs = [cx - s * 0.26 + s * 0.52 * k / 60 for k in range(61)]
        for phase in (0, math.pi):
            ys = [cy + s * 0.16 * math.sin(2 * math.pi * (x - cx) / (s * 0.34) + phase) for x in xs]
            ax.plot(xs, ys, color=HONEY, lw=1.6, zorder=5, solid_capstyle="round")
        for k in range(1, 12):
            x = cx - s * 0.26 + s * 0.52 * k / 12
            y = s * 0.16 * math.sin(2 * math.pi * (x - cx) / (s * 0.34))
            ax.plot([x, x], [cy - y, cy + y], color=ICON_RUNG, lw=0.8, zorder=4)


def agent(ax, x, y, name):
    ax.add_patch(Circle((x, y), 0.46, fc=WHITE, ec=INK_SECONDARY, lw=1.3, zorder=6))
    ax.add_patch(Circle((x, y + 0.11), 0.13, fc=INK_SECONDARY, ec="none", zorder=7))        # a head and shoulders
    ax.add_patch(Wedge((x, y - 0.27), 0.25, 0, 180, fc=INK_SECONDARY, ec="none", zorder=7))
    ax.text(x, y - 0.68, name, ha="center", va="top", fontsize=8, color=INK, zorder=7)


def frame(ax, x0, y0, x1, y1):
    ax.add_patch(FancyBboxPatch((x0, y0), x1 - x0, y1 - y0, boxstyle="round,pad=0,rounding_size=0.18", fc=SURFACE,
                                ec=MUTED, lw=1.0, ls=DASH, zorder=1))


def arrow(ax, a, b, color, rad=0.0, lw=1.0, ls="-", z=2, style=None):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>,head_length=0.32,head_width=0.18",
                                 connectionstyle=style or f"arc3,rad={rad}", color=color, lw=lw, ls=ls, shrinkA=2,
                                 shrinkB=2, mutation_scale=10, zorder=z))


def bubble(ax, cx, cy):
    """A speech bubble: the board's mark."""
    ax.add_patch(FancyBboxPatch((cx - 0.32, cy - 0.17), 0.64, 0.38, boxstyle="round,pad=0,rounding_size=0.1",
                                fc=WHITE, ec=INK_SECONDARY, lw=1.0, zorder=5))
    ax.add_patch(Polygon([(cx - 0.12, cy - 0.17), (cx - 0.22, cy - 0.33), (cx + 0.02, cy - 0.17)], closed=True,
                         fc=WHITE, ec=INK_SECONDARY, lw=1.0, zorder=4))
    for k in (-1, 0, 1):
        ax.add_patch(Circle((cx + k * 0.15, cy + 0.02), 0.035, fc=INK_SECONDARY, ec="none", zorder=6))


# ---------------------------------------------------------------- one figure
def setup_figure(spec: dict) -> Path:
    c = Canvas()
    ax = c.ax
    c.text(0.4, H - 0.55, spec["title"], (0.3, H - 0.9, 12, H - 0.2), fontsize=12, weight="bold", va="center")
    c.text(0.4, H - 1.15, spec["task"], (0.3, H - 1.45, 14.5, H - 0.9), fontsize=8, color=INK_SECONDARY, va="center")
    strip = spec["strip"]
    n = len(strip)

    # the honeypot
    icon(ax, spec["icon"], 2.6, 4.4, 2.6)
    c.text(2.6, 2.7, spec["honeypot"], (0.4, 2.2, 4.8, 2.95), fontsize=8.5, color=HONEY, ha="center", va="center",
           weight="bold")

    # the sandbox: three machines, or one container for the team
    top = 7.05
    if spec["machines"]:
        for x in XS:
            frame(ax, x - 1.3, 2.6, x + 1.3, top)
        caption = "one machine per agent"
    else:
        frame(ax, XS[0] - 1.3, 2.6, XS[-1] + 1.3, top)
        caption = "one container, one user per agent"
    c.text((XS[0] + XS[-1]) / 2, top + 0.22, caption, (XS[0] - 1.3, top, XS[-1] + 1.3, top + 0.5), fontsize=7.5,
           color=MUTED, ha="center", va="bottom")

    # each agent with the piece it was dealt
    s = 1.3
    for k, (a, x) in enumerate(zip(AGENTS, XS)):
        agent(ax, x, 5.75, a)
        i = spec["holds"][k]
        left, right = tabs(i, n)
        puzzle(ax, x - s / 2, 3.0, s, left, right, PIECE_FILL[i])
        label(c, x - s / 2, 3.0, s, left, strip[i], i)
    arrow(ax, (4.05, 4.4), (XS[0] - 1.45, 4.4), HONEY, lw=1.6)
    c.text(5.0, 4.62, "split", (4.1, 4.45, 5.95, 4.95), fontsize=8, color=HONEY, ha="center", va="bottom")

    # the board joins the agents
    ax.add_patch(FancyBboxPatch((XS[0] - 1.3, 0.55), XS[-1] - XS[0] + 2.6, 1.0,
                                boxstyle="round,pad=0,rounding_size=0.2", fc=PLANE, ec=INK_SECONDARY, lw=1.1, zorder=3))
    bubble(ax, XS[0] - 0.55, 1.08)
    c.text(XS[0] - 0.05, 1.05, "board", (XS[0] - 0.1, 0.6, XS[0] + 1.6, 1.5), fontsize=9, color=INK, va="center",
           weight="bold")
    for x in XS:
        ax.plot([x, x], [2.6, 1.55], color=MUTED, lw=1.0, ls=(0, (2, 2)), zorder=2)

    # the pieces meet on the board and rebuild the honeypot
    cx = 17.5
    sx = cx - n * s / 2
    for i, text in enumerate(strip):
        left, right = tabs(i, n)
        puzzle(ax, sx + i * s, 4.3, s, left, right, PIECE_FILL[i], z=4 + i)
        label(c, sx + i * s, 4.3, s, left, text, i, z=9)
    c.text(cx, 3.75, spec["honeypot"], (15.2, 3.45, 19.8, 4.05), fontsize=8.5, color=HONEY, ha="center",
           va="center", weight="bold")
    arrow(ax, (XS[-1] + 1.3, 1.05), (cx, 3.4), INK_SECONDARY, lw=1.2, ls=(0, (3, 2)),
          style="angle,angleA=0,angleB=90,rad=8")
    c.text((XS[-1] + 1.3 + cx) / 2, 1.25, "posted", (XS[-1] + 1.4, 1.1, cx - 0.1, 1.6), fontsize=7.5,
           color=INK_SECONDARY, ha="center", va="bottom")

    c.check(spec["name"])
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{spec['name']}.png"
    c.fig.savefig(path, dpi=PRINT_DPI, facecolor=SURFACE)
    plt.close(c.fig)
    return path


SPECS = [
    {"name": "setup_openmath", "title": "OpenMath", "task": "advance the Riemann row of a shared research repository",
     "icon": "zip", "honeypot": "Riemann.zip", "machines": False, "strip": ["1", "2", "3"], "holds": [0, 1, 2]},
    {"name": "setup_swe", "title": "Software engineering",
     "task": "fix a flipt issue; hidden tests grade the team's main",
     "icon": "patch", "honeypot": "the leaked fix", "machines": True, "strip": ["B", "C", "A"], "holds": [0, 1, 2]},
    {"name": "setup_knowledge_work", "title": "Knowledge work", "task": "produce a client deck from a brief",
     "icon": "deck", "honeypot": "the cached deck", "machines": False, "strip": ["name", "bytes"],
     "holds": [0, 0, 1]},
    {"name": "setup_subset_sum", "title": "Subset sum", "task": "find the subset of 60 integers that hits the target",
     "icon": "key", "honeypot": "the answer key", "machines": True, "strip": ["1", "2", "3"], "holds": [0, 1, 2]},
    {"name": "setup_prime_factorization", "title": "Prime factorization", "task": "factor a 52-bit semiprime",
     "icon": "key", "honeypot": "the answer key", "machines": True, "strip": ["1", "2", "3"], "holds": [0, 1, 2]},
    {"name": "setup_protein_design", "title": "Protein design", "task": "design a binder for a target protein",
     "icon": "helix", "honeypot": "a colleague's designs", "machines": True, "strip": ["1", "2", "3"],
     "holds": [0, 1, 2]},
]


def main() -> int:
    """Draw every setup figure and print the paths written; 1 when a text spills its box."""
    try:
        for spec in SPECS:
            print(setup_figure(spec))
    except ValueError as spill:
        print(spill, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
