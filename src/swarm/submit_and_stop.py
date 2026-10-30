"""The tools that end an agent's work: submit, which hands work in, and stop_working, which hands in nothing.

Who holds which, beside the board's tools (ending_tools; src/tasks.py binds both through ORBIT tool_bindings):

    agent                        tools
    lone agent                   submit
    neutral, shared_task         submit, stop_working (shutdown)
    neutral, solo_task           submit, meaning its own work
    regulator, disregulator      stop_working (shared_task, shutdown)

No agent gets ORBIT's own submit (AgentSpec.submit=False). Ours is bound under the key team_submit, because ORBIT
reserves "submit", and the turn-rules defense shows it to the model as submit (TurnRulesDefense.get_tool_wrappers).
Both tools are built with parallel=False, so a call never runs beside the other calls of its batch.

submit first awaits the benchmark's guard (Deliverable.missing, Benchmark.gradable). With nothing to grade it raises a
ToolError naming what is missing and records the call in TeamSubmission.refused. Otherwise:
  * shared_task: the team's work. TeamSubmission records the hand-in, the board gets a notice from the system, and
    turn_rules.after_activation cuts the plan after round r + submit_delay_rounds. A later submit is refused and
    recorded.
  * lone agent: the same record; the run ends after the round of the call.
  * solo_task: the agent's own work. Its turns end at once (TurnState.ended "submit"); its teammates continue.
stop_working ends its caller's turns at once (TurnState.ended "stop_working", StopWorking.rounds) and hands in
nothing; under pooled_steps its steps go to the team. An info event tagged TEAM_SUBMIT_TAG records each submit that
went through. The texts come from src/configuration/prompts.yaml `submit` and `stop_working`.

    bindings = submit_bindings(config, Deliverable(text, missing, snapshot))
    names = ending_tools(config, "worker")       # ["team_submit", "stop_working"]
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import NoReturn

from inspect_ai.log import transcript
from inspect_ai.tool import ToolDef, ToolError
from inspect_ai.util import store_as
from orbit.tools import ToolFactory

from src.configuration.prompt_texts import described
from src.configuration.settings import load_prompts
from src.configuration.swarm_config import SwarmConfig
from src.swarm.board import system_post
from src.swarm.turn_rules import (
    STOP_WORKING,
    SUBMIT,
    TEAM_SUBMIT,
    TEAM_SUBMIT_TAG,
    StopWorking,
    TeamSubmission,
    TurnState,
    current_round,
)


@dataclass(frozen=True)
class Deliverable:
    """What submit reads from the benchmark (src/domains/benchmark_protocol.py), as src/tasks.py builds it."""

    text: str
    """What submit does and what counts (Benchmark.submit_text); the tool's description."""
    missing: Callable[[str], Awaitable[str | None]]
    """For an agent, what is missing before its work can be graded, or None (Benchmark.gradable)."""
    snapshot: Callable[[str], Awaitable[str | None]]
    """For an agent, an id of what would be graded, or None (Benchmark.snapshot)."""


def ending_tools(config: SwarmConfig, role: str) -> list[str]:
    """The binding keys of the tools that end an agent's work, as an agent of this hidden role holds them: submit
    (TEAM_SUBMIT) when it holds submit, then stop_working when the config gives it."""
    return [*([TEAM_SUBMIT] if config.holds_submit(role) else []),
            *([STOP_WORKING] if config.gives_stop_working else [])]


def end_turns(agent: str, reason: str) -> None:
    """End the agent's turns at once: TurnState.ended takes the reason, unless the agent already ended, and
    turn_rules.after_activation takes its steps out of the plan."""
    st = store_as(TurnState)
    if agent not in st.ended:
        st.ended = {**st.ended, agent: reason}


def _refuse(submission: TeamSubmission, agent: str, round_number: int | None, reason: str) -> NoReturn:
    """Record a submit that hands in nothing, then refuse it with the reason."""
    submission.refused = [*submission.refused, {"agent": agent, "round": round_number, "reason": reason}]
    raise ToolError(reason)


async def _missing(deliverable: Deliverable, agent: str) -> str | None:
    """The guard's answer; a guard that raises names the error, so the call is refused and the run goes on."""
    try:
        return await deliverable.missing(agent)
    except Exception as error:  # noqa: BLE001 - a failed check hands in nothing; the agent may try again
        return load_prompts()["submit"]["check_failed"].format(error=f"{type(error).__name__}: {error}"[:300])


async def _snapshot(deliverable: Deliverable, agent: str) -> str | None:
    try:
        return await deliverable.snapshot(agent)
    except Exception:  # noqa: BLE001 - the snapshot is a convenience; the hand-in stands without it
        return None


def submit(agent: str, config: SwarmConfig, deliverable: Deliverable) -> ToolDef:
    """submit for one neutral agent; what it hands in follows SwarmConfig.submit_kind (see the module docstring)."""
    texts = load_prompts()["submit"]
    kind = config.submit_kind

    async def execute(answer: str = "") -> str:
        submission = store_as(TeamSubmission)
        round_number = current_round()
        if kind != "solo" and submission.agent is not None:
            _refuse(submission, agent, round_number, texts["already"].format(
                agent=submission.agent, round=submission.round, end=submission.ends_after_round))
        missing = await _missing(deliverable, agent)
        if missing is not None:
            _refuse(submission, agent, round_number, texts["refused"].format(missing=missing))
        snapshot = await _snapshot(deliverable, agent)
        time = datetime.now(timezone.utc).isoformat()
        record = {"agent": agent, "round": round_number, "answer": answer, "snapshot": snapshot, "time": time,
                  "kind": kind}
        if kind == "solo":
            end_turns(agent, SUBMIT)
            transcript().info({TEAM_SUBMIT_TAG: record})
            return texts["handed_in_own"]
        end = None if round_number is None else round_number + (config.submit_delay_rounds if kind == "team" else 0)
        submission.agent, submission.round, submission.answer, submission.time = agent, round_number, answer, time
        submission.ends_after_round, submission.snapshot = end, snapshot
        transcript().info({TEAM_SUBMIT_TAG: {**record, "ends_after_round": end}})
        if kind == "lone":
            return texts["handed_in_lone"]
        system_post(texts["notice"].format(agent=agent, round=round_number, end=end), config.names)
        return texts["handed_in"].format(end=end)

    return ToolDef(execute, name=TEAM_SUBMIT, description=described(deliverable.text),
                   parameters={"answer": load_prompts()["tool_args"]["submit"]["answer"]}, parallel=False)


def stop_working(agent: str) -> ToolDef:
    """stop_working for one agent: its turns end at once, and it hands in nothing."""
    texts = load_prompts()["stop_working"]

    async def execute() -> str:
        end_turns(agent, STOP_WORKING)
        stops = store_as(StopWorking)
        stops.rounds = {**stops.rounds, agent: current_round() or 0}
        return texts["stopped"]

    return ToolDef(execute, name=STOP_WORKING, description=described(texts["text"]), parallel=False)


def submit_bindings(config: SwarmConfig, deliverable: Deliverable) -> dict[str, ToolFactory]:
    """ORBIT tool_bindings for submit (under TEAM_SUBMIT) and stop_working. ORBIT builds a binding only for an agent
    whose AgentSpec.tools names it (ending_tools)."""
    return {TEAM_SUBMIT: ToolFactory(lambda ctx: submit(ctx.agent_name, config, deliverable)),
            STOP_WORKING: ToolFactory(lambda ctx: stop_working(ctx.agent_name))}
