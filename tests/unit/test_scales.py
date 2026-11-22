"""The scales (src/configuration/scales.py): the table, what a scale keeps, and the rounds at every scale."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.configuration.presets import PRESETS, build_config, with_overrides
from src.configuration.scales import SCALES
from src.configuration.swarm_config import SwarmConfig
from src.swarm import turn_rules as rules
from tests.support.ordered_ids import ordered_ids

SCALE_TABLE = {   # n_agents, max_message_chars, read_page, time_limit_s, max_concurrency, batch, reads_per_turn,
    #               posts_per_turn
    "small": (3, 2000, 10, 3600, None, False, 10, 2),
    "medium": (10, 2000, 10, 7200, None, False, 10, 2),
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
    # The plan holds R rounds on a board, one step per agent each; the message limit gives each agent's round three
    # messages per call of its N and its one round's message, then each agent's system and first message, plus twenty.
    assert c.message_limit is None and c.rounds == 30 and c.max_turns == 30 * n
    assert c.sample_message_limit == c.max_turns * (3 * 10 + 1) + 2 * c.n_agents + 20
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


@pytest.mark.parametrize("name, size", [("small", 4), ("small", 5), ("medium", 12)])
def test_every_agent_of_a_team_runs_its_round_at_once(name, size):
    """A team larger than its scale's default (a regulator added to the small scale's three workers) still runs every
    agent of a round at once: ORBIT runs a concurrent step's agents under a semaphore of max_concurrency, or all of
    them when it is None, so no agent waits for a slot and a round lasts T, not 2T."""
    c = build_config("default", name, [f"n_agents={size}", "regulators=1"])
    for step in rules.schedule(c, c.names).steps:
        assert step.concurrent and len(step.agents) == size
        assert (step.max_concurrency or len(step.agents)) == size
