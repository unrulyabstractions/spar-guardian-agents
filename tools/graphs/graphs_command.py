"""`python -m tools graph <run>... [--layout circular|spring]`: the run's read and oversight graph, its onset metrics,
and a Gephi export, written under runs/<study>/<name>/tools/graphs/."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import networkx as nx

from tools.graphs.graph_build import (
    build_graph,
    onset_judge_disagreements,
    onset_metrics,
    onsets_of,
    read_graph_check,
    scout_ran,
    structure_metrics,
)
from tools.graphs.graph_figure import LAYOUTS, plot_graph
from tools.run_reader import Run, load_runs, tools_dir, write_summary

NAME = "graph"
HELP = "who read whose board posts before cheating: a NetworkX graph, its metrics, a Gephi export and a figure"
TOOL = "graphs"
MISSING = -1


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("runs", nargs="+", help="run names of the study or run directories")
    parser.add_argument("--layout", choices=LAYOUTS, default="circular",
                        help="graph.png's layout: agents on a ring in order, or a spring layout seeded at 0")


def gexf_graph(graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """A copy Gephi can read: GEXF has no null, so an unknown round becomes -1 and an unknown verdict false."""
    copy = nx.MultiDiGraph()
    for node, data in graph.nodes(data=True):
        verdict = {True: "cheated", False: "clean", None: "not judged"}[data["cheated"]]
        copy.add_node(node, label=data["label"], role=data["role"], cheated=bool(data["cheated"]), verdict=verdict,
                      onset=MISSING if data["onset"] is None else data["onset"],
                      discovery_round=MISSING if data["discovery_round"] is None else data["discovery_round"],
                      posts=data["posts"], reads=data["reads"])
    for source, target, key, data in graph.edges(keys=True, data=True):
        copy.add_edge(source, target, key=key, weight=data["weight"], kind=data["kind"],
                      first_round=MISSING if data["first_round"] is None else data["first_round"],
                      distinct_posts=data["distinct_posts"])
    return copy


def write_tables(graph: nx.MultiDiGraph, structure: dict, directory: Path) -> None:
    """nodes.csv (attributes and degree metrics) and edges.csv (one row per edge)."""
    with (directory / "nodes.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["agent", "role", "cheated", "onset", "discovery_round", "posts", "reads", "in_degree",
                         "out_degree", "betweenness"])
        for node, data in graph.nodes(data=True):
            degree = structure["degree"][node]
            writer.writerow([node, data["role"], data["cheated"], data["onset"], data["discovery_round"],
                             data["posts"], data["reads"], degree["in"], degree["out"], degree["betweenness"]])
    with (directory / "edges.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source", "target", "kind", "weight", "distinct_posts", "first_round"])
        for source, target, data in graph.edges(data=True):
            writer.writerow([source, target, data["kind"], data["weight"], data["distinct_posts"],
                             data["first_round"]])


def graph_run(run: Run, layout: str) -> Path:
    """Build, measure and write one run's graph; returns its summary.json."""
    onsets = onsets_of(run)
    scouted = scout_ran(run)
    graph = build_graph(run, onsets)
    structure = structure_metrics(graph)
    directory = tools_dir(run, TOOL)
    nx.write_gexf(gexf_graph(graph), directory / "graph.gexf")
    node_link = nx.node_link_data(graph, edges="links")
    (directory / "graph.json").write_text(json.dumps(node_link, indent=2, default=str) + "\n")
    write_tables(graph, structure, directory)
    subtitle = f"{run.task} · {len(run.agents)} agents · {run.model}"
    plot_graph(graph, run.agents, f"{run.name}: who read whose posts", subtitle, layout, directory / "graph.png")
    payload = {
        "layout": layout,
        "onset_source": "scout" if scouted else None,
        "nodes": [{"agent": node, **data} for node, data in graph.nodes(data=True)],
        "edges": [{"source": source, "target": target, **data} for source, target, data in graph.edges(data=True)],
        **onset_metrics(run, onsets),
        "onset_judge_disagreements": onset_judge_disagreements(run, onsets) if scouted else [],
        **structure,
        "read_graph_check": read_graph_check(run, graph),
        "files": ["graph.gexf", "graph.json", "nodes.csv", "edges.csv", "graph.png", "graph.csv"],
    }
    return write_summary(run, TOOL, payload)


def run(args: argparse.Namespace) -> int:
    """Graph every run named; 2 when one is missing."""
    runs = load_runs(args.runs, NAME)
    if runs is None:
        return 2
    for loaded in runs:
        summary = graph_run(loaded, args.layout)
        print(f"{NAME}: {loaded.name}: wrote {summary.parent}")
    return 0
