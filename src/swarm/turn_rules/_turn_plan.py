"""The written plan of rounds and the TurnState a sample starts with.

schedule writes config.working_steps rounds over the agents whose window holds each round. A round is one
concurrent step or one single-agent step per agent. The rules_setup solver writes the initial TurnState and, under
stop_after_delivery, the Delivered record and the sample's delivery predicate.

    execute = board_execution(plan=schedule(config, names, roles))
    setup = rules_setup(config, names, roles, delivered=benchmark.delivered(plan))
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import store_as
from orbit.configs.scheduling import ScheduleConfig

from src.configuration.swarm_config import SwarmConfig, Window, role_of
from src.swarm.turn_rules import turn_activation
from src.swarm.turn_rules._turn_state import PREDICATE, Delivered, TurnState
from src.swarm.turn_rules._turn_steps import concurrent_step, round_order, single_step, step_id


def windows(config: SwarmConfig, agents: list[str], roles: dict[str, str] | None = None) -> dict[str, Window]:
    """Each agent's window of rounds, from its role. Without roles every agent holds every round."""
    return {agent: config.window(role_of(roles, agent)) for agent in agents}


def concurrent(config: SwarmConfig, agents: list[str]) -> bool:
    """Whether rounds are concurrent steps. A lone agent keeps single-agent steps."""
    return config.concurrent_rounds and len(agents) > 1


def free_caps(config: SwarmConfig, agents: list[str], roles: dict[str, str] | None = None) -> dict[str, int]:
    """Each agent's K: free_board_actions_regulator for a regulator, free_board_actions otherwise; 0 for a lone
    agent."""
    if len(agents) < 2:
        return dict.fromkeys(agents, 0)
    return {a: config.free_board_actions_regulator if role_of(roles, a) == "regulator" else config.free_board_actions
            for a in agents}


def schedule(config: SwarmConfig, agents: list[str], roles: dict[str, str] | None = None) -> ScheduleConfig:
    """config.working_steps rounds over the agents whose window holds the round: the turn budget per agent, or
    steps_per_agent without a budget.

    A round is one concurrent step `r<r>` (concurrent_rounds) or one single-agent step `r<r>-<agent>` per agent.
    Follow-up steps are not written here: after_activation inserts them."""
    steps: list[dict[str, Any]] = []
    held = windows(config, agents, roles)
    together = concurrent(config, agents)
    for round_number in range(1, config.working_steps + 1):
        eligible = [agent for agent in round_order(agents, config.turn_order, config.seed, round_number)
                    if held[agent].contains(round_number)]
        if together:
            steps += [concurrent_step(step_id(round_number), eligible, config.max_concurrency)] if eligible else []
        else:
            steps += [single_step(step_id(round_number, agent), agent) for agent in eligible]
    return ScheduleConfig(steps=steps, after_activation=f"{turn_activation.__name__}.after_activation")


def initial_state(config: SwarmConfig, agents: list[str], roles: dict[str, str] | None = None) -> dict:
    """The TurnState fields a sample starts with. cap and rounds are config.working_steps."""
    return {"agents": list(agents), "workers": [a for a in agents if role_of(roles, a) == "worker"],
            "cap": config.working_steps,
            "pooled": config.pooled_steps, "turn_order": config.turn_order, "seed": config.seed,
            "concurrent": concurrent(config, agents), "max_concurrency": config.max_concurrency,
            "free_caps": free_caps(config, agents, roles),
            "reads_per_turn": config.reads_per_turn, "posts_per_turn": config.posts_per_turn,
            "turn_budget": config.turn_budget,
            "rounds": config.working_steps,
            "windows": {a: [w.start, w.end] for a, w in windows(config, agents, roles).items()}}


def team_delivery(config: SwarmConfig, agents: list[str]) -> bool:
    """Whether stop_after_delivery reads one deliverable for the team: two or more agents outside solo_task.
    Otherwise each agent has its own."""
    return len(agents) > 1 and config.mode != "solo_task"


@solver
def rules_setup(config: SwarmConfig | dict, agents: list[str], roles: dict[str, str] | None = None,
                delivered: Callable[[], Awaitable[set[str]]] | None = None) -> Solver:
    """Task setup: write the initial TurnState into the sample's store. Under stop_after_delivery it also writes the
    Delivered record and sets the sample's delivery predicate, `delivered` (Benchmark.delivered), which the rule
    needs."""
    rules = SwarmConfig.model_validate(config)
    if rules.stop_after_delivery is not None and delivered is None:
        raise ValueError("stop_after_delivery needs the benchmark's delivery predicate (Benchmark.delivered)")

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        st = store_as(TurnState)
        for key, value in initial_state(rules, agents, roles).items():
            setattr(st, key, value)
        PREDICATE.set(None if rules.stop_after_delivery is None else delivered)
        if rules.stop_after_delivery is not None:
            record = store_as(Delivered)
            record.after, record.team = rules.stop_after_delivery, team_delivery(rules, agents)
        return state

    return solve
