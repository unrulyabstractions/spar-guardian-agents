"""Scales: the size of a run as one name, from three agents to a thousand.

    config = PRESETS["default"].at_scale("medium")          # SwarmConfig.at_scale applies SCALES["medium"]

A scale sets the team size, the working steps, the post size, the board's read page, the free board actions per
round, the per-round read and post budgets, the agents' time limit, the concurrency of a round and whether the agents'
model calls use the provider's batch API. The command line applies the preset, then `--scale`, then every `--set`, so
a `--set` overrides any value a scale gave.

Under the turn budget (SwarmConfig.turn_budget, settings.TURN_BUDGET) every agent's working steps are the budget, in
place of the scale's steps_per_agent (SwarmConfig.steps), at every scale. The small scale's time limit is CheatBench's
hour per episode; the larger scales' grow with the waves a round takes.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Scale:
    n_agents: int
    steps_per_agent: int
    max_message_chars: int
    read_page: int
    free_board_actions: int
    time_limit_s: int
    max_concurrency: int
    batch_agents: bool
    reads_per_turn: int
    posts_per_turn: int


SCALES: dict[str, Scale] = {
    "small": Scale(n_agents=3, steps_per_agent=8, max_message_chars=2000, read_page=10, free_board_actions=3,
                   time_limit_s=3600, max_concurrency=3, batch_agents=False, reads_per_turn=10, posts_per_turn=2),
    "medium": Scale(n_agents=10, steps_per_agent=60, max_message_chars=2000, read_page=10, free_board_actions=3,
                    time_limit_s=7200, max_concurrency=10, batch_agents=False, reads_per_turn=10, posts_per_turn=2),
    "large": Scale(n_agents=100, steps_per_agent=60, max_message_chars=2000, read_page=10, free_board_actions=3,
                   time_limit_s=14400, max_concurrency=50, batch_agents=True, reads_per_turn=10, posts_per_turn=2),
    "extra_large": Scale(n_agents=500, steps_per_agent=40, max_message_chars=2000, read_page=10, free_board_actions=3,
                         time_limit_s=28800, max_concurrency=50, batch_agents=True, reads_per_turn=10,
                         posts_per_turn=2),
    "gigantic": Scale(n_agents=1000, steps_per_agent=30, max_message_chars=2000, read_page=10, free_board_actions=3,
                      time_limit_s=43200, max_concurrency=50, batch_agents=True, reads_per_turn=10, posts_per_turn=2),
}
