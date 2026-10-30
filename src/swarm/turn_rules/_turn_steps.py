"""The ids and shapes of the plan's steps.

A round's step id starts with `r<r>`, then the agent of a single-agent step, then `-v<revision>` for a step the
callback pooled. A follow-up step ends with `-free<k>`. These helpers build and read the ids, order a round's
agents, and build a step. The plan, the store and the callback after each activation all use them.

    step_id(3, "a01", revision=4)  # "r3-a01-v4"
    step_round("r3-a01-free2")    # 3
    free_index("r3-a01-free2")    # 2
"""
from __future__ import annotations

import random
import re
from typing import Any

ROUND_ID = re.compile(r"^r(\d+)\b")
FREE_ID = re.compile(r"-free(\d+)$")


def step_id(round_number: int, agent: str | None = None, revision: int | None = None) -> str:
    """A round's step id: `r<r>`, `r<r>-<agent>` for a single-agent step, then `-v<revision>` for a pooled step."""
    return f"r{round_number}" + (f"-{agent}" if agent else "") + (f"-v{revision}" if revision is not None else "")


def follow_up_id(step: str, k: int) -> str:
    """The id of the k-th follow-up after a step: its id without a follow-up suffix, then `-free<k>`."""
    return f"{FREE_ID.sub('', step)}-free{k}"


def step_round(step_id: str) -> int:
    """The round of a plan step from its id: r3, r3-a01, r3-free1, r3-a01-free2, r9-a01-v4 give 3, 3, 3, 3, 9."""
    match = ROUND_ID.match(step_id)
    return int(match.group(1)) if match else 0


def free_index(step_id: str) -> int:
    """k of a follow-up step `...-free<k>`, or 0 for any other step."""
    match = FREE_ID.search(step_id)
    return int(match.group(1)) if match else 0


def is_round_step(step_id: str) -> bool:
    """A round's own step, not a follow-up step. The plan holds steps_per_agent of them per agent; whether one is
    charged depends on what it did (step_charge)."""
    return not free_index(step_id)


def round_order(agents: list[str], turn_order: str, seed: int, round_number: int) -> list[str]:
    """The agents of one round: declaration order, or a permutation seeded by (seed, round)."""
    order = list(agents)
    if turn_order == "random":
        random.Random(f"{seed}/{round_number}").shuffle(order)
    return order


def single_step(step_id: str, agent: str) -> dict[str, Any]:
    """A step for one agent."""
    return {"id": step_id, "agents": [agent]}


def concurrent_step(step_id: str, agents: list[str], max_concurrency: int) -> dict[str, Any]:
    """A concurrent step on a snapshot of the board as of its start."""
    return {"id": step_id, "agents": list(agents), "concurrent": True, "max_concurrency": max_concurrency,
            "visibility": "snapshot"}
