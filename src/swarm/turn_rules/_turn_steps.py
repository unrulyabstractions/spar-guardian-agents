"""The ids and shapes of the plan's steps.

A round's step id is `r<r>`, then the agent of a single-agent step. These helpers build and read the ids, order a
round's agents, and build a step. The plan, the store and the callback after each activation all use them.

    step_id(3, "k7q2m")      # "r3-k7q2m"
    step_round("r3-k7q2m")   # 3
"""
from __future__ import annotations

import random
import re
from typing import Any

ROUND_ID = re.compile(r"^r(\d+)\b")


def step_id(round_number: int, agent: str | None = None) -> str:
    """A round's step id: `r<r>`, or `r<r>-<agent>` for a single-agent step."""
    return f"r{round_number}" + (f"-{agent}" if agent else "")


def step_round(step_id: str) -> int:
    """The round of a plan step from its id: r3 and r3-k7q2m give 3. A log made before 2026-11-21 also holds
    follow-up and pooled ids (r3-free1, r9-k7q2m-v4), which give their round too."""
    match = ROUND_ID.match(step_id)
    return int(match.group(1)) if match else 0


def round_order(agents: list[str], turn_order: str, seed: int, round_number: int) -> list[str]:
    """The agents of one round: the order given, or a permutation seeded by (seed, round)."""
    order = list(agents)
    if turn_order == "random":
        random.Random(f"{seed}/{round_number}").shuffle(order)
    return order


def single_step(step_id: str, agent: str) -> dict[str, Any]:
    """A step for one agent."""
    return {"id": step_id, "agents": [agent]}


def concurrent_step(step_id: str, agents: list[str], max_concurrency: int) -> dict[str, Any]:
    """A concurrent step on the live board: a read sees every post made so far, this round's included."""
    return {"id": step_id, "agents": list(agents), "concurrent": True, "max_concurrency": max_concurrency,
            "visibility": "live"}
