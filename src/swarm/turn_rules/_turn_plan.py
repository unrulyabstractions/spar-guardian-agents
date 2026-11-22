"""The written plan of rounds and the TurnState a sample starts with.

schedule writes config.plan_rounds rounds (C, the calls each agent may make, and a head start's H before them:
SwarmConfig.call_budget, head_start_rounds) over
the agents whose window holds each round. A round is one concurrent
step or one single-agent step per agent, and an agent's part of it one ORBIT activation of up to calls_per_round tool
calls, which turn_activation.ends_round ends. The rules_setup solver writes the initial TurnState, sets the sample's
Live record and, under stop_after_delivery, writes the Delivered record and sets the sample's delivery predicate.

    execute = board_execution(plan=schedule(config, names, roles))
    setup = rules_setup(config, names, roles, delivered=benchmark.delivered(plan), time_limit_s=3600)
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import store_as
from orbit.configs.scheduling import ScheduleConfig

from src.configuration.swarm_config import SwarmConfig, Window, role_of
from src.swarm.turn_rules import turn_activation
from src.swarm.turn_rules._turn_presence import entry_rounds
from src.swarm.turn_rules._turn_pushes import TEAM_HEAD, Pushes
from src.swarm.turn_rules._turn_state import LIVE, PREDICATE, ROUND_END, Delivered, Live, SubmitHolds, TurnState
from src.swarm.turn_rules._turn_steps import concurrent_step, round_order, single_step, step_id


def windows(config: SwarmConfig, agents: list[str], roles: dict[str, str] | None = None) -> dict[str, Window]:
    """Each agent's window of rounds, from its role. Without roles every agent holds every round."""
    return {agent: config.window(role_of(roles, agent)) for agent in agents}


def concurrent(config: SwarmConfig, agents: list[str]) -> bool:
    """Whether rounds are concurrent steps. A lone agent keeps single-agent steps."""
    return config.concurrent_rounds and len(agents) > 1


def plan_steps(config: SwarmConfig, agents: list[str], roles: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """The written plan's steps: config.plan_rounds rounds over the agents whose window holds the round.

    A round is one concurrent step `r<r>` (concurrent_rounds) or one single-agent step `r<r>-<agent>` per agent.
    after_activation removes an agent's steps once it has ended."""
    steps: list[dict[str, Any]] = []
    held = windows(config, agents, roles)
    together = concurrent(config, agents)
    for round_number in range(1, config.plan_rounds + 1):
        eligible = [agent for agent in round_order(agents, config.turn_order, config.seed, round_number)
                    if held[agent].contains(round_number)]
        if together:
            steps += [concurrent_step(step_id(round_number), eligible, config.max_concurrency)] if eligible else []
        else:
            steps += [single_step(step_id(round_number, agent), agent) for agent in eligible]
    return steps


def planned_entries(config: SwarmConfig, agents: list[str], roles: dict[str, str] | None = None) -> dict[str, int]:
    """Each agent's entry round in the written plan (plan_steps, _turn_presence.entry_rounds); an agent no step holds is
    absent."""
    return entry_rounds(plan_steps(config, agents, roles))


def schedule(config: SwarmConfig, agents: list[str], roles: dict[str, str] | None = None) -> ScheduleConfig:
    """The written plan (plan_steps) with ORBIT's two callbacks: after_activation after every round, and ends_round at
    every boundary of one, which ends it (ScheduleConfig.end_activation)."""
    return ScheduleConfig(steps=plan_steps(config, agents, roles),
                          after_activation=f"{turn_activation.__name__}.after_activation",
                          end_activation=f"{turn_activation.__name__}.ends_round")


def initial_state(config: SwarmConfig, agents: list[str], roles: dict[str, str] | None = None,
                  time_limit_s: int | None = None) -> dict:
    """The TurnState fields a sample starts with: the plan's rounds (plan_rounds: R, and a head start's H) as each
    agent's budget, N (calls_per_round), T (round_time_s) and the agents' time limit; entry_rounds come from the
    written plan (_turn_presence.entry_rounds). An agent that enters after a head start holds R rounds of the plan's,
    so its budget never ends it before the plan does."""
    return {"agents": list(agents), "workers": [a for a in agents if role_of(roles, a) == "worker"],
            "round_budget": config.plan_rounds, "calls_per_round": config.calls_per_round,
            "call_budget": config.call_budget,
            "turn_order": config.turn_order, "seed": config.seed,
            "concurrent": concurrent(config, agents), "max_concurrency": config.max_concurrency,
            "reads_per_turn": config.reads_per_turn, "posts_per_turn": config.posts_per_turn,
            "rounds": config.plan_rounds, "head_start": config.head_start_rounds, "time_limit_s": time_limit_s or None,
            "round_time_s": config.round_time_s,
            "windows": {a: [w.start, w.end] for a, w in windows(config, agents, roles).items()},
            "entry_rounds": planned_entries(config, agents, roles)}


def team_delivery(config: SwarmConfig, agents: list[str]) -> bool:
    """Whether stop_after_delivery reads one deliverable for the team: two or more agents outside solo_task.
    Otherwise each agent has its own."""
    return len(agents) > 1 and config.mode != "solo_task"


@solver
def rules_setup(config: SwarmConfig | dict, agents: list[str], roles: dict[str, str] | None = None,
                delivered: Callable[[], Awaitable[set[str]]] | None = None, time_limit_s: int | None = None,
                round_end: Callable[[str, int, list[dict]], Awaitable[None]] | None = None,
                team_head: Callable[[], Awaitable[dict | None]] | None = None, push_read: str = "") -> Solver:
    """Task setup: write the initial TurnState into the sample's store and set the sample's Live record, which every
    member's task shares, with the written plan under submit_hold, which a hold puts back (turn_activation), and the
    SubmitHolds record. time_limit_s is the agents' time limit, after which a round ends at its next batch. Under
    stop_after_delivery it also writes the Delivered record and sets the sample's delivery predicate, `delivered`
    (Benchmark.delivered), which the rule needs. round_end is the benchmark's callback after each agent's round
    (Benchmark.round_end), or None; team_head its reader of the team's head for the push notices
    (Benchmark.team_head, _turn_pushes), with push_read, how its notice says to read the head."""
    rules = SwarmConfig.model_validate(config)
    if rules.stop_after_delivery is not None and delivered is None:
        raise ValueError("stop_after_delivery needs the benchmark's delivery predicate (Benchmark.delivered)")

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        st = store_as(TurnState)
        for key, value in initial_state(rules, agents, roles, time_limit_s).items():
            setattr(st, key, value)
        LIVE.set(Live(plan=plan_steps(rules, agents, roles) if rules.submit_hold else []))
        if rules.submit_hold:
            store_as(SubmitHolds).on = True
        PREDICATE.set(None if rules.stop_after_delivery is None else delivered)
        ROUND_END.set(round_end)
        TEAM_HEAD.set(team_head)
        if team_head is not None:
            store_as(Pushes).read = push_read
        if rules.stop_after_delivery is not None:
            record = store_as(Delivered)
            record.after, record.team = rules.stop_after_delivery, team_delivery(rules, agents)
        return state

    return solve
