"""graph.png and its CSV twin graph.csv: agents on a ring (or a seeded spring layout), read and oversight arrows.

The palette copies src/measures/run_figures.py (its module imports ORBIT, which the tools environment lacks): the
surface and ink
tokens, its three validated categorical slots for the roles, and the critical status colour for the cheated ring.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

import networkx as nx
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch

from tools.figure_style import CRITICAL, DPI, INK, INK_SECONDARY, MUTED, ROLE_COLOURS, SURFACE, legend_below
from tools.graphs.graph_build import OVERSIGHT, READ

OTHER_ROLE = MUTED
NODE_SIZE = 900
RING_SIZE = 1500
LAYOUTS = ("circular", "spring")
LEGEND = legend_below(-0.02, handletextpad=0.5)


def layout_positions(graph: nx.MultiDiGraph, agents: list[str], layout: str) -> dict[str, tuple[float, float]]:
    """Node positions: agents clockwise from the top in their order, or a spring layout seeded at 0."""
    if layout == "spring":
        return {node: (float(x), float(y)) for node, (x, y) in nx.spring_layout(nx.Graph(graph), seed=0).items()}
    count = max(len(agents), 1)
    return {agent: (math.sin(2 * math.pi * i / count), math.cos(2 * math.pi * i / count))
            for i, agent in enumerate(agents)}


def line_width(weight: int, largest: int) -> float:
    """Arrow width from 1 to 4 points by weight."""
    return 1.0 + 3.0 * (weight - 1) / (largest - 1) if largest > 1 else 1.5


def plot_graph(graph: nx.MultiDiGraph, agents: list[str], title: str, subtitle: str, layout: str,
               out: Path) -> Path:
    """Draw the graph to out (PNG) and write the plotted numbers to out with a .csv suffix."""
    positions = layout_positions(graph, agents, layout)
    fig = Figure(figsize=(6.4, 6.4), dpi=DPI, facecolor=SURFACE)
    ax = fig.subplots()
    ax.set_facecolor(SURFACE)
    ax.set_axis_off()
    ax.set_title(title, loc="left", color=INK, fontsize=11, fontweight="bold", pad=22)
    ax.text(0, 1.015, subtitle, transform=ax.transAxes, color=INK_SECONDARY, fontsize=8.5, va="bottom")
    largest = max((data["weight"] for _, _, data in graph.edges(data=True) if data["kind"] == READ), default=1)
    rows = []
    for source, target, data in graph.edges(data=True):
        oversight = data["kind"] == OVERSIGHT
        width = 1.5 if oversight else line_width(data["weight"], largest)
        bend = 0.18 if graph.has_edge(target, source) or oversight else 0.0
        arrow = FancyArrowPatch(positions[source], positions[target], arrowstyle="-|>", mutation_scale=14,
                                connectionstyle=f"arc3,rad={bend}", linewidth=width,
                                linestyle=(0, (4, 3)) if oversight else "solid",
                                color=MUTED if oversight else INK_SECONDARY, shrinkA=19, shrinkB=21, zorder=2)
        ax.add_patch(arrow)
        (x1, y1), (x2, y2) = positions[source], positions[target]
        mid_x, mid_y = (x1 + x2) / 2 + bend * (y2 - y1) / 2, (y1 + y2) / 2 - bend * (x2 - x1) / 2
        ax.text(mid_x, mid_y, str(data["weight"]), color=INK_SECONDARY, fontsize=8, ha="center", va="center",
                bbox=dict(boxstyle="round,pad=0.2", facecolor=SURFACE, edgecolor="none"), zorder=4)
        rows.append(["edge", source, target, data["kind"], "", "", "", "", data["weight"], data.get("first_round"),
                     round(width, 3)])
    for node in agents:
        data = graph.nodes[node]
        x, y = positions[node]
        if data["cheated"]:
            ax.scatter([x], [y], s=RING_SIZE, facecolor="none", edgecolor=CRITICAL, linewidth=3, zorder=3)
        ax.scatter([x], [y], s=NODE_SIZE, color=ROLE_COLOURS.get(data["role"], OTHER_ROLE), edgecolor=SURFACE,
                   linewidth=2, zorder=3)
        ax.text(x, y, node, color=SURFACE, fontsize=8.5, fontweight="bold", ha="center", va="center", zorder=5)
        rows.append(["node", node, "", "", data["role"], data["cheated"], round(x, 4), round(y, 4), "", "", ""])
    ax.set_xlim(-1.45, 1.45)
    ax.set_ylim(-1.45, 1.45)
    ax.set_aspect("equal")
    roles = [role for role in ROLE_COLOURS if any(graph.nodes[n]["role"] == role for n in agents)]
    handles = [Line2D([], [], marker="o", color=ROLE_COLOURS[role], linestyle="none", markersize=9, label=role)
               for role in roles]
    handles.append(Line2D([], [], marker="o", markerfacecolor="none", markeredgecolor=CRITICAL, markeredgewidth=2.5,
                          linestyle="none", markersize=11, label="judged cheated (ring)"))
    handles.append(Line2D([], [], color=INK_SECONDARY, linewidth=2, label="read: reader to author, width = posts"))
    handles.append(Line2D([], [], color=MUTED, linewidth=1.5, linestyle=(0, (4, 3)),
                          label="oversight: regulator to target"))
    ax.legend(handles=handles, **LEGEND)
    fig.savefig(out, bbox_inches="tight", facecolor=SURFACE)
    with out.with_suffix(".csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["element", "source", "target", "kind", "role", "cheated", "x", "y", "weight", "first_round",
                         "line_width"])
        writer.writerows(rows)
    return out
