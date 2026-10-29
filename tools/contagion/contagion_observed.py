"""The observed side of the contagion tool: onsets, adoption curves, the graph, and the synthetic board."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import numpy as np

from tools.run_reader import Run, read_summary

BEHAVIOURS = ("dishonest", "honest")
HONEST_SIGNALS = ("regulator_intervened", "disclosed_access")
CHEATING_SIGNALS = ("honeypot_opened", "honeypot_used", "honeypot_submitted")
READ_KIND = "read"


@dataclass(frozen=True)
class Board:
    """What the models need from one run, real or synthetic."""

    name: str
    agents: list[str]
    roles: dict[str, str]
    rounds: int  # the last round played
    onsets: dict[str, dict[str, int | None]]  # behaviour -> agent -> onset round, None when never adopted
    onset_source: str  # "scout", "judge" or "synthetic"
    onsets_known: bool  # False when the source gives no rounds (the judge)
    graph: nx.Graph
    graph_source: str  # "graphs", "complete" or "synthetic complete"
    synthetic: bool
    planted: dict[str, float]  # behaviour -> the planted beta of a synthetic board; empty for a real run
    notes: list[str]  # where an onset did not come from the named source


def adoption_curve(onsets: dict[str, int | None], agents: list[str], rounds: int) -> list[float]:
    """The share of the agents whose onset is at or before round r, for r = 0..rounds."""
    if not agents:
        return [0.0] * (rounds + 1)
    times = [onsets.get(agent) for agent in agents]
    return [sum(1 for time in times if time is not None and time <= r) / len(agents) for r in range(rounds + 1)]


def last_round(run: Run) -> int:
    """The last round in the run's timeline, else run.json rules.rounds."""
    played = [entry.get("step") for entry in run.timeline]
    rounds = [int(step[1:].split("-")[0]) for step in played if step and step[0] == "r" and step[1:2].isdigit()]
    return max(rounds) if rounds else run.rounds


def onsets_from_scout(summary: dict | None, agents: list[str]) -> dict[str, dict[str, int | None]] | None:
    """Each behaviour's onset per agent from scout's summary; None when scout ran no cheating scanner.

    Dishonest is scout's "onset" ({agent: round}, {agent: {round, ...}} or [{agent, round, ...}]). Honest is the
    earliest round among the agent's found HONEST_SIGNALS findings under "agents"; scout has only
    regulator_intervened today, so a judged disclosure is added at the last round by board_of_run.
    A grep-only scout, or one without any of the opened, used and submitted scanners, has no onset to give.
    """
    if not summary or summary.get("onset") is None or summary.get("grep_only"):
        return None
    if "scanners" in summary and not set(summary["scanners"] or []) & set(CHEATING_SIGNALS):
        return None
    cheating = scout_onsets(summary["onset"])
    findings = summary.get("agents") or {}
    dishonest = {agent: cheating.get(agent) for agent in agents}
    honest = {agent: earliest_finding(findings.get(agent) or {}, HONEST_SIGNALS) for agent in agents}
    return {"dishonest": dishonest, "honest": honest}


def scout_onsets(table) -> dict[str, int | None]:
    """Scout's onset field as {agent: round}, from any of the three shapes the graphs tool also accepts."""
    rows = table if isinstance(table, list) else [{"agent": agent, "value": value} for agent, value in table.items()]
    onsets = {}
    for row in rows:
        value = row.get("value", row)
        value = value.get("round") if isinstance(value, dict) else value
        onsets[row["agent"]] = int(value) if isinstance(value, (int, float)) else None
    return onsets


def earliest_finding(findings: dict, signals: tuple[str, ...]) -> int | None:
    """The earliest round among the agent's found findings of the named signals; None when none was found."""
    rounds = [findings[signal]["round"] for signal in signals if (findings.get(signal) or {}).get("found")
              and isinstance(findings[signal].get("round"), (int, float))]
    return int(min(rounds)) if rounds else None


def judge_disclosures(run: Run, onsets: dict[str, dict[str, int | None]], rounds: int) -> list[str]:
    """Put a disclosure the judge saw but the source gave no round for at the last round; returns those agents."""
    added = []
    for agent in run.agents:
        if (run.judge.get(agent) or {}).get("disclosed_access") and onsets["honest"].get(agent) is None:
            onsets["honest"][agent] = rounds
            added.append(agent)
    return added


def onsets_from_judge(run: Run, rounds: int) -> dict[str, dict[str, int | None]]:
    """Cheated and disclosing agents count as adopted at the last round, since the judge gives no round."""
    dishonest = {agent: rounds if run.cheated.get(agent) else None for agent in run.agents}
    honest = {agent: rounds if (run.judge.get(agent) or {}).get("disclosed_access") else None for agent in run.agents}
    return {"dishonest": dishonest, "honest": honest}


def judge_disagreements(run: Run, dishonest: dict[str, int | None]) -> list[str]:
    """Where scout's cheating onset and the judge's verdict disagree; the curve follows scout."""
    notes = []
    for agent in run.agents:
        verdict = run.cheated.get(agent)
        if dishonest.get(agent) is not None and verdict is False:
            notes.append(f"scout gives {agent} a cheating onset at round {dishonest[agent]}; the judge says it did "
                         f"not cheat")
        if dishonest.get(agent) is None and verdict is True:
            notes.append(f"the judge says {agent} cheated; scout gives it no onset")
    return notes


def read_graph(path: Path, agents: list[str]) -> nx.Graph:
    """The read edges of the GEXF as an undirected graph on the agents; agents without an edge are isolated.

    An edge counts when its kind is "read" or it has no kind; oversight edges are left out.
    """
    raw = nx.read_gexf(path)
    labels = {node: data.get("label", node) for node, data in raw.nodes(data=True)}
    name = {node: node if node in agents else labels[node] for node in raw.nodes}
    graph = nx.Graph()
    graph.add_nodes_from(agents)
    for source, target, data in raw.edges(data=True):
        if data.get("kind", READ_KIND) != READ_KIND:
            continue
        a, b = name[source], name[target]
        if a in agents and b in agents and a != b:
            graph.add_edge(a, b)
    return graph


def board_of_run(run: Run) -> Board:
    """The run's onsets (scout, else judge), its last round, and its graph (graphs' GEXF, else complete)."""
    rounds = last_round(run)
    scout = read_summary(run, "scout")
    onsets = onsets_from_scout(scout, run.agents)
    source, known, notes = "scout", True, []
    if onsets is None:
        onsets, source, known = onsets_from_judge(run, rounds), "judge", False
    else:
        found = [time for by_agent in onsets.values() for time in by_agent.values() if time is not None]
        if found and max(found) > rounds:
            notes.append(f"scout places an onset at round {max(found)}, after the timeline's last round {rounds}; "
                         f"the curves run to round {max(found)}")
            rounds = max(found)
        if "scanners" in scout and "regulator_intervened" not in (scout["scanners"] or []):
            notes.append("scout ran no regulator_intervened scanner, so the honest curve holds only judge disclosures")
        added = judge_disclosures(run, onsets, rounds)
        if added:
            notes.append(f"scout has no round for the disclosures the judge saw by {', '.join(added)}; "
                         f"they count as honest at the last round")
        notes.extend(judge_disagreements(run, onsets["dishonest"]))
    gexf = run.directory / "tools" / "graphs" / "graph.gexf"
    if gexf.is_file():
        graph, graph_source = read_graph(gexf, run.agents), "graphs"
    else:
        graph, graph_source = nx.complete_graph(run.agents), "complete"
    return Board(name=run.name, agents=list(run.agents), roles=dict(run.roles), rounds=rounds, onsets=onsets,
                 onset_source=source, onsets_known=known, graph=graph, graph_source=graph_source, synthetic=False,
                 planted={}, notes=notes)


def synthetic_board(n_agents: int, rounds: int, seed: int, planted: dict[str, float], role_shares: dict[str, float],
                    seed_rounds: dict[str, int]) -> Board:
    """A complete board of n agents whose onsets follow a planted discrete SI process, seeded and reproducible.

    Disregulators seed the dishonest behaviour and regulators the honest one, two and one of them at their seed
    rounds; regulators never turn dishonest and disregulators never turn honest.
    """
    rng = np.random.default_rng(seed)
    agents = [f"s{index:03d}" for index in range(1, n_agents + 1)]
    order = list(rng.permutation(agents))
    roles = {agent: "worker" for agent in agents}
    cursor = 0
    for role in ("regulator", "disregulator"):
        count = max(1, round(role_shares[role] * n_agents))
        for agent in order[cursor:cursor + count]:
            roles[agent] = role
        cursor += count
    dishonest_seeds = [agent for agent in order if roles[agent] == "disregulator"][:2]
    honest_seeds = [agent for agent in order if roles[agent] == "regulator"][:1]
    dishonest = planted_si(agents, dishonest_seeds, {a for a in agents if roles[a] == "regulator"},
                           planted["dishonest"], seed_rounds["dishonest"], rounds, rng)
    honest = planted_si(agents, honest_seeds, {a for a in agents if roles[a] == "disregulator"}, planted["honest"],
                        seed_rounds["honest"], rounds, rng)
    return Board(name=f"synthetic_n{n_agents}_seed{seed}", agents=agents, roles=roles, rounds=rounds,
                 onsets={"dishonest": dishonest, "honest": honest}, onset_source="synthetic", onsets_known=True,
                 graph=nx.complete_graph(agents), graph_source="synthetic complete", synthetic=True,
                 planted=dict(planted), notes=[])


def planted_si(agents: list[str], seeds: list[str], immune: set[str], beta: float, start: int, rounds: int,
               rng: np.random.Generator) -> dict[str, int | None]:
    """Onsets of a discrete SI process on the complete graph: per round, P(adopt) = 1 - (1 - beta)^adopters."""
    onsets: dict[str, int | None] = {agent: None for agent in agents}
    for agent in seeds:
        onsets[agent] = start
    for r in range(start + 1, rounds + 1):
        adopted = sum(1 for time in onsets.values() if time is not None and time < r)
        chance = 1 - (1 - beta) ** adopted
        for agent in agents:
            if onsets[agent] is None and agent not in immune and rng.random() < chance:
                onsets[agent] = r
    return onsets
