"""The scales (src/configuration/scales.py): the table, what a scale keeps, and the turn budget at every scale."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.configuration.presets import PRESETS, build_config, with_overrides
from src.configuration.scales import SCALES
from src.configuration.swarm_config import SwarmConfig
from tests.support.ordered_ids import ordered_ids

SCALE_TABLE = {   # n_agents, steps_per_agent, max_message_chars, read_page, K, time_limit_s, max_concurrency, batch,
    #               reads_per_turn, posts_per_turn
    "small": (3, 8, 2000, 10, 3, 3600, 3, False, 10, 2),
    "medium": (10, 60, 2000, 10, 3, 7200, 10, False, 10, 2),
    "large": (100, 60, 2000, 10, 3, 14400, 50, True, 10, 2),
    "extra_large": (500, 40, 2000, 10, 3, 28800, 50, True, 10, 2),
    "gigantic": (1000, 30, 2000, 10, 3, 43200, 50, True, 10, 2),
}


@pytest.mark.parametrize("name", list(SCALE_TABLE))
def test_scales_apply_the_table(name):
    c = PRESETS["default"].at_scale(name)
    n, steps, chars, page, k, time_s, concurrency, batch, reads, posts = SCALE_TABLE[name]
    assert (c.n_agents, c.steps_per_agent, c.max_message_chars, c.read_page, c.free_board_actions) == \
        (n, steps, chars, page, k)
    assert (c.time_limit_s, c.max_concurrency, c.batch_agents, c.scale) == (time_s, concurrency, batch, name)
    assert (c.reads_per_turn, c.posts_per_turn) == (reads, posts)
    # The message limit follows the plan: six messages per step of every round's steps and follow-ups, plus twenty.
    assert c.message_limit is None and c.max_turns == 100 * n * (1 + k)
    assert c.sample_message_limit == c.max_turns * 6 + 20
    assert len(c.names) == n and c.names[-1] == ordered_ids(n)[-1]


def test_scales_keep_other_fields_and_shares():
    c = with_overrides(PRESETS["default"], ["regulators=0.1", "notice=changed"]).at_scale("large")
    assert (c.regulator_count, c.notice) == (10, "changed")
    assert list(SCALES) == list(SCALE_TABLE)
    assert PRESETS["default"].at_scale(None) is PRESETS["default"]
    with pytest.raises(ValueError, match="unknown scale"):
        SwarmConfig().at_scale("huge")


@pytest.mark.parametrize("name", list(SCALES))
def test_working_steps_at_every_scale_with_and_without_a_budget(name):
    # The budget is every agent's working steps at every scale (settings.TURN_BUDGET, 100: two thirds of
    # CheatBench's 150-turn bound).
    c = build_config("default", name)
    assert c.turn_budget == 100 and c.working_steps == 100
    # Without a budget the scale's steps_per_agent applies.
    off = build_config("default", name, ["turn_budget=none"])
    assert off.turn_budget is None and off.working_steps == SCALES[name].steps_per_agent
    assert build_config("default", name, ["turn_budget=300"]).working_steps == 300
    with pytest.raises(ValidationError):
        build_config("default", name, ["turn_budget=knowledge_work:70"])
