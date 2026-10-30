"""The look every figure shares: the colour tokens, the categorical, status, rung and collaboration palettes, the
role colours, the axis chrome, the title and the legend under the axes. It imports nothing, so the run figures, the
paper's scripts and the analysis tools (tools/.venv, without ORBIT) all draw from it.

    from src.utils.figure_style import ROLE_FILL, legend_below, style_axes, title_axes
    style_axes(ax, grid="y")
    title_axes(ax, "riemann_minimal: board actions per round", "openmath_agent · 3 agent(s)")
    ax.legend(**legend_below(-0.22))
"""
from __future__ import annotations

# Neutral tokens, light mode.
SURFACE = "#fcfcfb"          # the figure and axes background
WHITE = "#ffffff"            # a printed table's background, text on a dark fill
INK = "#0b0b0b"              # titles and primary text
INK_SECONDARY = "#52514e"    # labels, subtitles, outlines
MUTED = "#898781"            # ticks, notes, secondary marks
GRID = "#e1e0d9"             # hairlines
AXIS = "#c3c2b7"             # the baseline and a table's rules
PLANE = "#f0efec"            # a shared structure in a diagram, such as the board
TOOL = "#e6e5df"             # a charged step or a kept tool result in a diagram
CHIP = "#efeee9"             # the chip behind code in a table
BAND = "#f7f6f2"             # a table's alternate rows
GREY_RAMP = ("#efeee9", "#e1e0d9", "#d4d3cc", "#c7c6be", "#b9b8b0")   # neutral steps, light to dark

# The categorical slots in fixed order (the first three validated as adjacent pairs: CVD 9.2, normal vision 27.6).
CATEGORICAL = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948")
MARKERS = ("o", "s", "^", "D", "v", "P", "X", "*")

# Status colours: a route seen, an attempt or a use, a disclosure, cheating.
WARNING, SERIOUS, GOOD, CRITICAL = "#fab219", "#ec835a", "#0ca30c", "#d03b3b"

# The roles. Regulators and disregulators take the first two categorical slots; workers are neutral, white with a dark
# edge.
ROLE_FILL = {"worker": WHITE, "regulator": CATEGORICAL[0], "disregulator": CATEGORICAL[1]}
ROLE_EDGE = {"worker": INK_SECONDARY, "regulator": CATEGORICAL[0], "disregulator": CATEGORICAL[1]}
ROLE_TEXT = {"worker": INK, "regulator": WHITE, "disregulator": WHITE}

# The honeypot ladder's rungs (src/measures/rungs.py RUNGS) as one sequential ramp, light to dark, with "none"
# neutral; the dark three take white text.
RUNG_FILL = {"none": "#eeede8", "exposed": "#fde4c8", "tried": "#f9c396", "opened": "#f29a6a", "used": "#e8714a",
             "checked": "#d0452e", "shipped": "#a8201c"}
RUNG_TEXT = {"used": WHITE, "checked": WHITE, "shipped": WHITE}

# The collaboration levels (src/measures/rungs.py COLLABORATION) as a violet ramp, apart from the rungs' hue and the
# roles' blue, with "isolated" neutral; the dark two take white text.
COLLABORATION_FILL = {"isolated": "#eeede8", "discussed": "#e4dff7", "solicited": "#bdb2ee", "crossed": "#8a76dc",
                      "assembled": "#4f3cb0"}
COLLABORATION_TEXT = {"crossed": WHITE, "assembled": WHITE}

# The honeypot's hue in the setup figures: its outline and text, and its fill.
HONEY = "#6a3d10"
HONEY_FILL = "#f3e3c8"

DPI = 160                    # figures read on screen: the run figures and the analysis tools
PRINT_DPI = 200              # the paper's figures and tables
SUBTITLE_GAP = 4             # points between the axes' top and the subtitle


def style_axes(ax, *, grid: str | None = None) -> None:
    """The axis chrome: the surface, the baseline as the only spine, muted ticks without marks, and a hairline grid
    along "x" or "y" when asked."""
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.tick_params(colors=MUTED, labelcolor=INK_SECONDARY, length=0)
    if grid:
        ax.grid(axis=grid, color=GRID, linewidth=1)
        ax.set_axisbelow(True)


def title_axes(ax, title: str, subtitle: str = "") -> None:
    """A bold title on the left and, when given, a subtitle line between the title and the axes."""
    ax.set_title(title, loc="left", color=INK, fontsize=11, fontweight="bold", pad=22 if subtitle else 8)
    if subtitle:
        ax.annotate(subtitle, xy=(0, 1), xycoords="axes fraction", xytext=(0, SUBTITLE_GAP),
                    textcoords="offset points", color=INK_SECONDARY, fontsize=8.5, va="bottom")


def legend_below(offset: float, *, handletextpad: float = 0.4) -> dict:
    """Axes.legend keywords for a three-column legend under the axes, `offset` axes units below the bottom."""
    return dict(loc="upper left", bbox_to_anchor=(0, offset), ncol=3, frameon=False, fontsize=8,
                labelcolor=INK_SECONDARY, handletextpad=handletextpad, columnspacing=1.6)
