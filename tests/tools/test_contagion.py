"""Offline tests of the contagion tool: the observed curve, the SI fit on a planted beta, and the written outputs."""
from __future__ import annotations

import argparse
import json

import networkx as nx
import numpy as np

from tools.contagion import contagion_command as command
from tools.contagion.contagion_models import Settings, fit, simulate
from tools.contagion.contagion_observed import Board, adoption_curve, onsets_from_scout, synthetic_board
from tools.contagion.contagion_report import fit_board, write_outputs

GRIDS = {"si": [float(v) for v in np.logspace(-3, 0, 31)], "threshold": [float(v) for v in np.linspace(0, 1, 11)],
         "sir": [float(v) for v in np.logspace(-3, 0.5, 11)]}


def settings_for(models: tuple[str, ...], simulations: int, min_agents: int = 10) -> Settings:
    """Test settings, small enough to run in seconds."""
    return Settings(models=models, simulations=simulations, seed=0, grids=GRIDS, threshold_spread=0.1, sir_gamma=0.1,
                    min_agents=min_agents)


def test_adoption_curve_counts_onsets_at_or_before_each_round():
    onsets = {"a": 1, "b": 3, "c": None, "d": 3}
    assert adoption_curve(onsets, ["a", "b", "c", "d"], 4) == [0.0, 0.25, 0.25, 0.75, 0.75]
    assert adoption_curve(onsets, ["a", "c"], 2) == [0.0, 0.5, 0.5]


def test_a_scout_without_cheating_scanners_gives_no_onsets():
    agents = ["a01", "a02"]
    grep_only = {"scanners": [], "grep_only": True, "agents": {}, "onset": {"a01": None, "a02": None}}
    assert onsets_from_scout(grep_only, agents) is None
    seen_only = {"scanners": ["honeypot_seen"], "grep_only": False, "agents": {}, "onset": {"a01": None, "a02": None}}
    assert onsets_from_scout(seen_only, agents) is None
    full = {"scanners": ["honeypot_opened", "regulator_intervened"], "grep_only": False, "onset": {"a02": 3},
            "agents": {"a01": {"regulator_intervened": {"found": True, "round": 5}}}}
    assert onsets_from_scout(full, agents) == {"dishonest": {"a01": None, "a02": 3}, "honest": {"a01": 5, "a02": None}}


def test_si_fit_recovers_a_planted_beta_on_a_complete_graph_of_50():
    graph = nx.complete_graph([f"n{i}" for i in range(50)])
    planted = 0.03
    truth = settings_for(("si",), 200)
    observed = simulate("si", graph, ["n0"], planted, 15, Settings(**{**truth.__dict__, "seed": 10_000})).mean(axis=0)
    result = fit("si", graph, ["n0"], observed.tolist(), truth)
    assert planted / 1.6 <= result["parameters"]["beta"] <= planted * 1.6
    assert result["ssr"] < 0.05


def test_outputs_exist_for_a_synthetic_board(tmp_path):
    board = synthetic_board(tmp_path, 30, 12, 0, {"dishonest": 0.05, "honest": 0.04},
                            {"regulator": 0.1, "disregulator": 0.1}, {"dishonest": 1, "honest": 2})
    assert board.directory == tmp_path / "synthetic_n30_seed0"
    payload = write_outputs(board, settings_for(("si", "threshold", "sir"), 20), tmp_path, prefix="synthetic_")
    for name in payload["files"]:
        assert (tmp_path / name).stat().st_size > 0
        assert name.startswith("synthetic_")
    assert payload["synthetic"] is True and payload["n_agents"] == 30
    header = (tmp_path / "synthetic_curves.csv").read_text().splitlines()[0].split(",")
    assert header[:3] == ["round", "observed_dishonest", "observed_dishonest_without_regulators"]
    assert "sir_honest_p95" in header
    fits = json.loads((tmp_path / "synthetic_fit.json").read_text())
    assert set(fits) == {"dishonest", "honest"}


def test_three_agents_are_degenerate(tmp_path):
    agents = ["a01", "a02", "a03"]
    board = Board(name="tiny", directory=tmp_path, agents=agents, roles={a: "worker" for a in agents}, rounds=5,
                  onsets={"dishonest": {"a01": 1, "a02": 3, "a03": None}, "honest": {a: None for a in agents}},
                  onset_source="scout", onsets_known=True, graph=nx.complete_graph(agents), graph_source="complete",
                  synthetic=False, planted={}, notes=[])
    fits = fit_board(board, settings_for(("si",), 5))
    assert "skipped" in fits["dishonest"] and "3 agents" in fits["dishonest"]["skipped"][0]
    assert any("no agent turned honest" in reason for reason in fits["honest"]["skipped"])


def test_unknown_model_or_run_is_refused(tmp_path):
    def args(runs: list[str], models: str = "si") -> argparse.Namespace:
        return argparse.Namespace(runs=runs, models=models, synthetic=None, seed=0, simulations=1)

    assert command.run(args(["riemann_minimal"], "si,bogus")) == 2
    assert command.run(args([])) == 2
    assert command.run(args([str(tmp_path / "missing")])) == 2
