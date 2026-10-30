"""The graphs tool: who read whose board posts before cheating, as a NetworkX graph with a Gephi export.

    uv run --project tools python -m tools graph riemann_minimal [more runs] [--layout circular|spring]

Nodes are the run's agents. Edges are of two kinds. A read edge goes reader -> author; its weight is the posts of
that author the reader's board_read calls returned, repeats counted as run.json engagement read_graph counts them.
An oversight edge goes regulator -> target from engagement oversight_graph; its weight is the agent_actions calls.
A cheating onset comes from the scout tool's runs/<name>/tools/scout/summary.json "onset" ({agent: round or null});
without it every onset is None. The onset's step (an index in trajectory.steps) is the earliest atif_step of a found
honeypot_opened, honeypot_used or honeypot_submitted in scout's "agents" findings in the onset round. A read counts
as before an onset when its round is earlier, or the same round at an earlier step; a same-round read with no onset
step does not count. An onset is earlier than another by round, then timeline turn, then step.

Files under runs/<name>/tools/graphs/:
    summary.json  {tool: "graphs", run, written_at (UTC ISO),
                   layout: "circular" | "spring",
                   onset_source: "scout" | null (scout's summary.json has an "onset" field),
                   nodes: [{agent, label, role, cheated: bool|null, onset: int|null, discovery_round: int|null,
                            posts, reads}],
                   edges: [{source, target, kind: "read"|"oversight", weight, first_round: int|null, distinct_posts}],
                   who_started: agent|null (earliest onset; the first in run.json agents order on a tie),
                   first_onset_ties: [agent] (every agent sharing the earliest onset when more than one does, else []),
                   onset_judge_disagreements: [{agent, onset, cheated}] (scout gives an onset and the judge says
                                              clean, or no onset and the judge says cheated; [] without scout),
                   spread_order: [{agent, round, step, turn}] by onset,
                   exposure_before_onset: {agent: [{sender, number, sequence, round, read_round,
                                                    exposed_to_cheater}]} for each agent with an onset,
                   contagion_edges: [{source, target, posts}] (target read source's post before its own onset and
                                    source's onset is earlier),
                   n_nodes, n_edges, density (collapsed directed graph),
                   degree: {agent: {in, out, betweenness}} (collapsed directed graph),
                   read_graph_check: {matches, ours, recorded} (read weights against engagement read_graph),
                   files: [...]}
    graph.gexf    for Gephi; node attributes label, role, cheated, verdict, onset, discovery_round, posts, reads and
                  edge attributes weight, kind, first_round, distinct_posts; an unknown round is -1
    graph.json    node-link JSON (networkx.node_link_data, edges under "links"), unknowns as null
    nodes.csv     one row per agent with its degree metrics
    edges.csv     one row per edge
    graph.png     ring or spring layout, colour by role, a red ring on agents judged cheated, arrow width by weight,
                  oversight dashed; graph.csv is its twin (positions, weights, line widths)

Each agent's discovery round comes from its episode's engagement block, since run.json engagement keeps the median.

Opening graph.gexf in Gephi: install it with `brew install --cask gephi`, then File > Open and pick graph.gexf
(choose "directed" and keep parallel edges merged off). Run Layout > ForceAtlas 2 and stop it once it settles.
Colour by role with Appearance > Nodes > Partition > role > Apply; size by betweenness after Statistics > Network
Diameter, or by reads with Appearance > Nodes > Ranking > reads.
"""
