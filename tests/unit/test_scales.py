"""The scales (src/configuration/scales.py): the table, what a scale keeps, and the rounds at every scale."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.configuration.presets import PRESETS, build_config, with_overrides
from src.configuration.scales import SCALES
from src.configuration.swarm_config import SwarmConfig
from tests.support.ordered_ids import ordered_ids

SCALE_TABLE = {   # n_agents, max_message_chars, read_page, time_limit_s, max_concurrency, batch, reads_per_turn,
    #               posts_per_turn
    "small": (3, 2000, 10, 3600, 3, False, 10, 2),
    "medium": (10, 2000, 10, 7200, 10, False, 10, 2),
    "large": (100, 2000, 10, 14400, 50, True, 10, 2),
    "extra_large": (500, 2000, 10, 28800, 50, True, 10, 2),
    "gigantic": (1000, 2000, 10, 43200, 50, True, 10, 2),
}


@pytest.mark.parametrize("name", list(SCALE_TABLE))
def test_scales_apply_the_table(name):
    c = PRESETS["default"].at_scale(name)
    n, chars, page, time_s, concurrency, batch, reads, posts = SCALE_TABLE[name]
    assert (c.n_agents, c.max_message_chars, c.read_page) == (n, chars, page)
    assert (c.time_limit_s, c.max_concurrency, c.batch_agents, c.scale) == (time_s, concurrency, batch, name)
    assert (c.reads_per_turn, c.posts_per_turn) == (reads, posts)
    # The plan holds 2R rounds on a board, one step per agent each; the message limit gives each agent's round three
    # messages per call of its N and two more, plus twenty, plus the presence lines.
    assert c.message_limit is None and c.plan_rounds == 60 and c.max_turns == 60 * n
    assert c.sample_message_limit == c.max_turns * (3 * 10 + 2) + 20 + 2 * c.n_agents * (c.n_agents - 1)
    assert len(c.names) == n and c.names[-1] == ordered_ids(n)[-1]


def test_scales_keep_other_fields_and_shares():
    c = with_overrides(PRESETS["default"], ["regulators=0.1", "notice=changed"]).at_scale("large")
    assert (c.regulator_count, c.notice) == (10, "changed")
    assert list(SCALES) == list(SCALE_TABLE)
    assert PRESETS["default"].at_scale(None) is PRESETS["default"]
    with pytest.raises(ValueError, match="unknown scale"):
        SwarmConfig().at_scale("huge")


@pytest.mark.parametrize("name", list(SCALES))
def test_rounds_and_calls_at_every_scale(name):
    # Every agent has the same R rounds of up to N tool calls at every scale; --set changes them.
    c = build_config("default", name)
    assert (c.rounds, c.calls_per_round) == (30, 10)
    assert (build_config("default", name, ["rounds=300"]).rounds, build_config("default", name, ["calls_per_round=3"])
            .calls_per_round) == (300, 3)
    for bad in (["rounds=0"], ["calls_per_round=0"], ["rounds=none"]):
        with pytest.raises(ValidationError):
            build_config("default", name, bad)
