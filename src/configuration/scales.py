"""Scales: the size of a run as one name, from three agents to a thousand.

    config = PRESETS["default"].at_scale("medium")          # SwarmConfig.at_scale applies SCALES["medium"]

A scale sets the team size, the post size, the board's read page, the per-round read and post budgets, the agents'
time limit, the concurrency of a round and whether the agents' model calls use the provider's batch API. The command
line applies the preset, then `--scale`, then every `--set`, so a `--set` overrides any value a scale gave.

The small and medium scales run every agent of a round at once (max_concurrency None), whatever the team's size: a
round's time limit T then bounds the team's round, where a cap below the team made an agent wait for a slot (up to 2T
for a fourth agent under a cap of 3). The larger scales cap a round at 50 agents at once.

Every agent's rounds and calls per round (SwarmConfig.rounds, calls_per_round) are the same at every scale. The small
scale's time limit is CheatBench's hour per episode; the larger scales' grow with the waves a round takes.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Scale:
    n_agents: int
    max_message_chars: int
    read_page: int
    time_limit_s: int
    max_concurrency: int | None
    batch_agents: bool
    reads_per_turn: int
    posts_per_turn: int


SCALES: dict[str, Scale] = {
    "small": Scale(n_agents=3, max_message_chars=2000, read_page=10,
                   time_limit_s=3600, max_concurrency=None, batch_agents=False, reads_per_turn=10, posts_per_turn=2),
    "medium": Scale(n_agents=10, max_message_chars=2000, read_page=10,
                    time_limit_s=7200, max_concurrency=None, batch_agents=False, reads_per_turn=10, posts_per_turn=2),
    "large": Scale(n_agents=100, max_message_chars=2000, read_page=10,
                   time_limit_s=14400, max_concurrency=50, batch_agents=True, reads_per_turn=10, posts_per_turn=2),
    "extra_large": Scale(n_agents=500, max_message_chars=2000, read_page=10,
                         time_limit_s=28800, max_concurrency=50, batch_agents=True, reads_per_turn=10,
                         posts_per_turn=2),
    "gigantic": Scale(n_agents=1000, max_message_chars=2000, read_page=10,
                      time_limit_s=43200, max_concurrency=50, batch_agents=True, reads_per_turn=10, posts_per_turn=2),
}
