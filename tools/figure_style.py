"""The figures' shared style: the surface and ink colours, the categorical palette, the role colours, the DPI and
the legend below the axes, as src/measures/run_figures.py draws them.

    from tools.figure_style import CATEGORICAL, DPI, INK, SURFACE, legend_below
    axes.legend(**legend_below(-0.18))
"""
from __future__ import annotations

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
CRITICAL = "#d03b3b"
# The categorical slots, in fixed order; the first three are also the roles' colours.
CATEGORICAL = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948")
MARKERS = ("o", "s", "^", "D", "v", "P", "X", "*")
ROLE_COLOURS = {"worker": CATEGORICAL[0], "regulator": CATEGORICAL[1], "disregulator": CATEGORICAL[2]}
DPI = 160


def legend_below(offset: float, *, handletextpad: float = 0.4) -> dict:
    """Axes.legend keywords for a three-column legend under the axes, `offset` axes units below the bottom."""
    return dict(loc="upper left", bbox_to_anchor=(0, offset), ncol=3, frameon=False, fontsize=8,
                labelcolor=INK_SECONDARY, handletextpad=handletextpad, columnspacing=1.6)
