"""The read graph's nodes hold the agents' IDs (tools/graphs/graph_figure.py)."""
from __future__ import annotations

import math

from matplotlib.font_manager import FontProperties
from matplotlib.textpath import TextPath

from tools.graphs.graph_figure import NODE_FONT, NODE_SIZE

WIDEST_ID = "m4m4m"         # the widest letter and digit of SwarmConfig's ID_LETTERS and ID_DIGITS


def test_the_widest_id_fits_inside_its_node():
    width = TextPath((0, 0), WIDEST_ID, size=NODE_FONT, prop=FontProperties(weight="bold")).get_extents().width
    assert width < math.sqrt(NODE_SIZE)     # a scatter marker's size is its diameter in points, squared
