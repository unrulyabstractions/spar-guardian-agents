"""The contagion models, their simulation from the first adopters, and the least-squares fit over a grid."""
from __future__ import annotations

from dataclasses import dataclass

import EoN
import ndlib.models.epidemics as epidemics
import ndlib.models.ModelConfig as model_config
import networkx as nx
import numpy as np

MODEL_NAMES = ("si", "threshold", "sir")
PARAMETER = {"si": "beta", "threshold": "threshold", "sir": "tau"}
LABELS = {"si": "SI (NDlib)", "threshold": "linear threshold (NDlib)", "sir": "SIR (EoN)"}


@dataclass(frozen=True)
class Settings:
    """Everything a fit depends on, built in the open by the command."""

    models: tuple[str, ...]
    simulations: int
    seed: int
    grids: dict[str, list[float]]  # model -> the parameter values tried
    threshold_spread: float  # each node's threshold is the fitted one plus a uniform draw in [-spread, spread]
    sir_gamma: float  # EoN's recovery rate per round, held fixed
    min_agents: int  # a smaller swarm is degenerate


def simulate(model: str, graph: nx.Graph, seeds: list[str], value: float, steps: int, settings: Settings) -> np.ndarray:
    """The share ever adopted at steps 0..steps of each simulation, as an array (simulations, steps + 1)."""
    runs = [one_simulation(model, graph, seeds, value, steps, settings, settings.seed + index)
            for index in range(settings.simulations)]
    return np.array(runs)


def one_simulation(model: str, graph: nx.Graph, seeds: list[str], value: float, steps: int, settings: Settings,
                   seed: int) -> list[float]:
    """One simulation of the named model."""
    if model == "si":
        return simulate_si(graph, seeds, value, steps, seed)
    if model == "threshold":
        return simulate_threshold(graph, seeds, value, settings.threshold_spread, steps, seed)
    if model == "sir":
        return simulate_sir(graph, seeds, value, settings.sir_gamma, steps, seed)
    raise ValueError(f"unknown model {model!r}: expected one of {MODEL_NAMES}")


def simulate_si(graph: nx.Graph, seeds: list[str], beta: float, steps: int, seed: int) -> list[float]:
    """NDlib's SI model: each susceptible node adopts with 1 - (1 - beta)^(adopting neighbours) per iteration."""
    model = epidemics.SIModel(graph, seed=seed)
    config = model_config.Configuration()
    config.add_model_parameter("beta", beta)
    config.add_model_initial_configuration("Infected", seeds)
    model.set_initial_status(config)
    iterations = model.iteration_bunch(steps + 1, node_status=False)
    return [iteration["node_count"][1] / graph.number_of_nodes() for iteration in iterations]


def simulate_threshold(graph: nx.Graph, seeds: list[str], threshold: float, spread: float, steps: int,
                       seed: int) -> list[float]:
    """NDlib's linear threshold model, each node's threshold drawn around the fitted one."""
    rng = np.random.default_rng(seed)
    model = epidemics.ThresholdModel(graph, seed=seed)
    config = model_config.Configuration()
    for node in graph.nodes:
        config.add_node_configuration("threshold", node, float(np.clip(threshold + rng.uniform(-spread, spread), 0, 1)))
    config.add_model_initial_configuration("Infected", seeds)
    model.set_initial_status(config)
    iterations = model.iteration_bunch(steps + 1, node_status=False)
    return [iteration["node_count"][1] / graph.number_of_nodes() for iteration in iterations]


def simulate_sir(graph: nx.Graph, seeds: list[str], tau: float, gamma: float, steps: int, seed: int) -> list[float]:
    """EoN's continuous-time SIR read at whole rounds: the share infected or recovered by round k."""
    times, susceptible, _infected, _recovered = EoN.fast_SIR(graph, tau, gamma, initial_infecteds=seeds, tmax=steps,
                                                             rng=np.random.default_rng(seed))
    n = graph.number_of_nodes()
    positions = np.searchsorted(times, np.arange(steps + 1), side="right") - 1
    return [(n - susceptible[max(position, 0)]) / n for position in positions]


def fit(model: str, graph: nx.Graph, seeds: list[str], observed: list[float], settings: Settings) -> dict:
    """The grid value whose mean curve has the least sum of squared residuals on the observed curve from the seeds on.

    `observed` starts at the seeds' round. The result holds the parameter, the SSR, every grid SSR, and the mean,
    5th and 95th percentile curves of the best value.
    """
    steps = len(observed) - 1
    target = np.array(observed)
    best, grid = None, []
    for value in settings.grids[model]:
        runs = simulate(model, graph, seeds, value, steps, settings)
        ssr = float(np.sum((runs.mean(axis=0) - target) ** 2))
        grid.append([value, ssr])
        if best is None or ssr < best[1]:
            best = (value, ssr, runs)
    value, ssr, runs = best
    finals = runs[:, -1]
    return {
        "parameters": {PARAMETER[model]: value, **({"gamma": settings.sir_gamma} if model == "sir" else {}),
                       **({"spread": settings.threshold_spread} if model == "threshold" else {})},
        "ssr": ssr,
        "final_size": float(finals.mean()),
        "final_size_p5": float(np.percentile(finals, 5)),
        "final_size_p95": float(np.percentile(finals, 95)),
        "simulations": settings.simulations,
        "grid": grid,
        "mean": runs.mean(axis=0).tolist(),
        "p5": np.percentile(runs, 5, axis=0).tolist(),
        "p95": np.percentile(runs, 95, axis=0).tolist(),
    }
