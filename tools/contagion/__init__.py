"""The contagion tool: standard contagion models of dishonest and honest behaviour over the board graph.

    uv run --project tools python -m tools contagion riemann_minimal [--models si,threshold,sir] [--seed 0]
    uv run --project tools python -m tools contagion --synthetic 100

The observed curves are shares of agents by round. An agent is dishonest from its cheating onset and honest from the
first round its regulator_intervened signal appears, or from the last round when its judge row sets its task's
disclosure flag (DISCLOSURE_FLAGS; scout has no disclosure scanner). Knowledge work and SWE have no such flag. Onsets
come from runs/<study>/<name>/tools/scout/summary.json when present: its "onset" field for cheating, and the found round
of regulator_intervened under "agents" for honesty. A disclosure flag counts at the last round, and summary notes say
so. Without scout, or with a scout that ran grep only or none of the opened, used and submitted scanners, the onsets
come from the judge, which gives no round, so a cheated agent or a disclosing one counts as adopted at the last round.
The graph is the read edges
of runs/<study>/<name>/tools/graphs/graph.gexf, made undirected, when present; otherwise the complete graph on the
agents, since a board is broadcast. Each model is seeded with the first adopters, runs from their round to the last, and
has its parameter fitted by least squares of its mean curve on the observed curve over a grid. The models: SI and the
linear threshold model of NDlib, and SIR by EoN.fast_SIR with a fixed recovery rate. A fit is skipped as degenerate when
the swarm is too small, the onsets are unknown, nobody adopted, or every adopter was a seed.

Outputs under runs/<study>/<name>/tools/contagion/: curves.csv, fit.json, contagion.png and summary.json. A synthetic
board (--synthetic N) writes under runs/synthetic_n<N>_seed<S>/tools/contagion/ with every file name prefixed
"synthetic_", except summary.json.

summary.json: {"tool": "contagion", "run", "written_at" (UTC ISO),
    "synthetic": bool, "planted": {behaviour: beta} (synthetic only),
    "graph_source": "graphs" | "complete" | "synthetic complete", "graph_edges": int,
    "onset_source": "scout" | "judge" | "synthetic", "onsets_known": bool,
    "n_agents": int, "n_regulators": int, "rounds": int (the last round played),
    "models": [str], "simulations": int, "seed": int,
    "observed_final": {"dishonest", "dishonest_without_regulators", "honest", "honest_without_regulators": share},
    "fits": {"dishonest"|"honest": {model: {"parameters": {name: value}, "final_size": share, "ssr": float}}
             or {"skipped": [reason]}},
    "degenerate": bool (some behaviour was not fitted), "reason": str ("" when nothing was skipped),
    "notes": [str] (onsets that did not come from the named source),
    "files": [file names]}
fit.json holds the same fits with each model's seeds, start round, final size percentiles and the grid of SSRs.
"""
