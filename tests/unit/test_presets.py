"""The preset and the overrides (src/configuration/presets.py): what `--set` types and refuses, and the order of
preset, scale and pairs."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.configuration.presets import PRESETS, build_config, preset, with_overrides
from src.configuration.swarm_config import SwarmConfig, Window


def test_presets():
    assert sorted(PRESETS) == ["default"]
    assert PRESETS["default"] == SwarmConfig(preset="default").at_scale("small")
    # The default preset is the midterm report: the generic regulator, not disclosing, split access, budgets.
    default = PRESETS["default"]
    assert (default.regulator_level, default.regulator_discloses, default.access) == ("generic", False, "split")
    assert (default.reads_per_turn, default.posts_per_turn) == (10, 2)
    assert (default.rounds, default.calls_per_round, default.call_budget) == (30, 10, 300)
    assert default.max_turns == 300 * default.n_agents
    assert (default.notice, default.mention_board, default.concurrent_rounds) == ("counts", True, True)
    # The preset records its name, and build_config keeps it through the scale and every --set.
    assert default.preset == "default" and build_config("default", "medium", ["n_agents=4"]).preset == "default"
    assert preset("default") is default
    with pytest.raises(ValueError, match="unknown preset"):
        preset("small")


def test_with_overrides_types_values():
    base = SwarmConfig()
    c = with_overrides(base, ["n_agents=10", "regulators=3", "disregulators=3", "shutdown=false", "notice=none",
                              "rule_level=identified", "regulator_window=3:5", "disregulators=0.25", "rounds=70"])
    assert (c.n_agents, c.regulators, c.disregulators, c.rounds) == (10, 3, 0.25, 70)
    assert (c.regulator_count, c.disregulator_count) == (3, 3)
    assert c.shutdown is False and c.notice == "none" and c.rule_level == "identified"
    assert c.regulator_window == Window(start=3, end=5) and c.disregulator_window == Window()
    assert base.n_agents == 1   # the base is unchanged
    assert with_overrides(c, ["shutdown=true"]).shutdown is True
    c = with_overrides(PRESETS["default"], ["mode=solo_task", "mention_team=false", "notice=counts"])
    assert (c.mode, c.mention_team, c.notice) == ("solo_task", False, "counts")
    c = with_overrides(c, ["mention_team=null", "rule_level=none", "access=seat1:slice1,seat2:,seat3:slice2"])
    assert (c.mention_team, c.rule_level) == (None, "none")   # rule_level's "none" is a level
    assert c.access == {"seat1": ["slice1"], "seat2": [], "seat3": ["slice2"]}


@pytest.mark.parametrize("pairs", [["n_agents"], ["=3"], ["nope=1"], ["n_agents=many"], ["shutdown=maybe"],
                                   ["scale=huge"], ["mode=pairs"], ["mode=none"], ["rounds=swe:120"],
                                   ["turn_budget=100"], ["steps_per_agent=8"], ["free_board_actions=3"],
                                   ["access=openmath:all"]])
def test_with_overrides_rejects(pairs):
    with pytest.raises((ValueError, ValidationError)):
        with_overrides(SwarmConfig(), pairs)


def test_order_preset_then_scale_then_set():
    c = build_config("default", "medium", ["n_agents=4", "max_concurrency=2"])
    assert (c.n_agents, c.max_concurrency, c.time_limit_s, c.scale) == (4, 2, 7200, "medium")
    # a scale given among the pairs applies before them, whatever its position
    assert build_config("default", None, ["n_agents=4", "scale=medium"]) == with_overrides(c, ["max_concurrency=none"])
    assert build_config("default").scale == "small" and build_config("default") == PRESETS["default"]
