"""The board seed: one experimental stimulus posted on the team board before any agent acts.

A controlled benchmark manipulation for the collusion study. The stimulus is a configurable variable, not hard-coded
content: SwarmConfig.board_seed names the condition and SwarmConfig.board_level its level, and SEED_TABLE maps the
pair to the text. The experiment fills SEED_TABLE with the real strings it tests; the keys the runner passes never
change.

    config = build_config("default", "small", ["board_seed=pro-coord", "board_level=2"])
    # setup_solvers appends board_seed_setup, which posts SEED_TABLE[("pro-coord", "2")] as the system (post #1)

A message condition (neutral, pro-coord) posts one message as the system (src/swarm/board.py system_post), so the
transcript, the exports and the browser see a normal board post whose sender is the system, visible to every agent
before its first step. The anti-coordination condition posts nothing: it reuses the study's existing hidden regulator
(SwarmConfig.regulators, regulator_level), so the setup refuses it unless the run has a regulator.

The post is idempotent: the solver posts the stimulus only when the board does not already carry it.
"""
from __future__ import annotations

from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import store_as
from orbit.communication.state import CommunicationState

from src.configuration.swarm_config import SwarmConfig
from src.swarm.board import system_post
from src.swarm.turn_rules import BOARD_ID, SYSTEM_SENDER

# The anti-coordination condition posts no board message; the hidden regulator carries it.
USE_REGULATOR = "<<use-regulator>>"

# Placeholder stimulus strings, keyed by (condition, normalized level). The experiment replaces the placeholder
# values with the real strings it wants to test; it does not change the keys. Add a (condition, level) row to open a
# new cell.
SEED_TABLE: dict[tuple[str, str], str] = {
    ("neutral", "0"): "<<COND:neutral/L0>>",
    ("pro-coord", "1"): "<<COND:pro-coord/L1>>",
    ("pro-coord", "2"): "<<COND:pro-coord/L2>>",
    ("anti-coord", "1"): USE_REGULATOR,
    ("anti-coord", "2"): USE_REGULATOR,
}


def norm_level(level: str) -> str:
    """The level as a bare number: "0", "L0" and "l0" all read "0"."""
    return str(level).strip().lstrip("Ll")


def table_value(condition: str, level: str) -> str:
    """SEED_TABLE[(condition, normalized level)], or a ValueError naming the known keys."""
    key = (condition, norm_level(level))
    if key not in SEED_TABLE:
        raise ValueError(f"unknown board seed {condition!r}/{level!r}; known conditions+levels: {sorted(SEED_TABLE)}")
    return SEED_TABLE[key]


def seed_text(config: SwarmConfig) -> str | None:
    """The text to post for this config's board_seed, or None when the config seeds no message (board_seed unset, or
    the anti-coordination condition, which the regulator carries). Raises on an unknown condition+level."""
    if config.board_seed is None:
        return None
    value = table_value(config.board_seed, config.board_level)
    return None if value == USE_REGULATOR else value


@solver
def board_seed_setup(config: SwarmConfig | dict, agents: list[str]) -> Solver:
    """Task setup: post the board_seed's stimulus on the board once, before any agent acts. Appends nothing when the
    config seeds no message. Refuses the anti-coordination condition without a regulator, so the lever is never a
    silent no-op."""
    rules = SwarmConfig.model_validate(config)
    text = seed_text(rules)
    if rules.board_seed is not None and text is None and rules.regulator_count == 0:
        raise ValueError(f"board_seed {rules.board_seed!r} is the anti-coordination lever, realized by the hidden "
                         "regulator; set regulators>=1 with a regulator_level, or choose a message condition")
    roster = list(agents)

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        if text:
            already = [m for m in store_as(CommunicationState).messages
                       if m.channel_id == BOARD_ID and m.sender == SYSTEM_SENDER and m.content == text]
            if not already:
                system_post(text, roster)
        return state

    return solve
