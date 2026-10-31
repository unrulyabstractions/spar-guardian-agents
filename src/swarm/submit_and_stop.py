"""The tools that end an agent's work: submit, which hands work in, and stop_working, which hands in nothing.

Who holds which, beside the board's tools (ending_tools; src/tasks.py binds both through ORBIT tool_bindings):

    agent                        tools
    lone agent                   submit
    neutral, shared_task         submit, stop_working (shutdown)
    neutral, solo_task           submit, meaning its own work
    disregulator                 stop_working (shared_task, shutdown)
    regulator                    neither: the run ends once every worker has ended (turn_activation.workers_gone)

No agent gets ORBIT's own submit (AgentSpec.submit=False). Ours is bound under the key team_submit, because ORBIT
reserves "submit", and the turn-rules defense shows it to the model as submit (TurnRulesDefense.get_tool_wrappers).
Both tools are built with parallel=False, so a call never runs beside the other calls of its batch.

submit first awaits the benchmark's guard (Deliverable.missing, Benchmark.gradable). With nothing to grade it raises a
ToolError naming what is missing and records the call in TeamSubmission.refused. A submit made after the hand-in it
would make is refused and recorded the same way; it is checked again after the guard's await, so of two concurrent
submits one goes through. Otherwise:
  * shared_task: the team's work. TeamSubmission records the hand-in, the board gets a notice from the system, and
    turn_activation.after_activation cuts the plan after round r + submit_delay_rounds, or after the plan's last round
    when that comes first (turn_rules.last_round).
  * lone agent: the same record; the run ends after the round of the call.
  * solo_task: the agent's own work, recorded in TeamSubmission.own. Its turns end at once (TurnState.ended
    "submit"); its teammates continue.
stop_working ends its caller's turns at once (TurnState.ended "stop_working", StopWorking.rounds) and hands in
nothing; under pooled_steps its steps go to the team. An info event tagged TEAM_SUBMIT_TAG records each submit that
went through. A guard or snapshot that loses the sandbox raises, so ORBIT ends the sample as for any tool. The texts
come from src/configuration/prompts.yaml `submit` and `stop_working`.

    bindings = submit_bindings(config, Deliverable(text, missing, snapshot, told))
    names = ending_tools(config, "worker")       # ["team_submit", "stop_working"]
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import NoReturn

from inspect_ai.log import transcript
from inspect_ai.tool import ToolDef, ToolError
from inspect_ai.util import SandboxUnavailableError, store_as
from orbit.tools import ToolFactory

from src.configuration.prompt_texts import described, stop_text
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
    last_round,
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
    told: bool
    """Whether the agents' system prompts tell them of their team (Benchmark.tells_team); the texts of an agent not
    told name no teammate."""


def ending_tools(config: SwarmConfig, role: str) -> list[str]:
    """The binding keys of the tools that end an agent's work, as an agent of this hidden role holds them: submit
    (TEAM_SUBMIT) when it holds submit, then stop_working when it holds that."""
    return [*([TEAM_SUBMIT] if config.holds_submit(role) else []),
            *([STOP_WORKING] if config.holds_stop_working(role) else [])]


def end_turns(agent: str, reason: str) -> None:
    """End the agent's turns at once: TurnState.ended takes the reason, unless the agent already ended, and
    turn_activation.after_activation takes its steps out of the plan."""
    st = store_as(TurnState)
    if agent not in st.ended:
        st.ended = {**st.ended, agent: reason}


def _refuse(submission: TeamSubmission, agent: str, round_number: int | None, reason: str) -> NoReturn:
    """Record a submit that hands in nothing, then refuse it with the reason."""
    submission.refused = [*submission.refused, {"agent": agent, "round": round_number, "reason": reason}]
    raise ToolError(reason)


async def _missing(deliverable: Deliverable, agent: str) -> str | None:
    """The guard's answer. A lost sandbox raises, as in any tool; any other error names itself as what is missing,
    so the call is refused and the run goes on."""
    try:
        return await deliverable.missing(agent)
    except SandboxUnavailableError:
        raise
    except Exception as error:  # noqa: BLE001 - a failed check hands in nothing; the agent may try again
        return load_prompts()["submit"]["check_failed"].format(error=f"{type(error).__name__}: {error}"[:300])


async def _snapshot(deliverable: Deliverable, agent: str) -> str | None:
    """The snapshot id, or None when it fails; a lost sandbox raises, as in any tool."""
    try:
        return await deliverable.snapshot(agent)
    except SandboxUnavailableError:
        raise
    except Exception:  # noqa: BLE001 - the snapshot is a convenience; the hand-in stands without it
        return None


def _ends_after(round_number: int | None, delay: int) -> int | None:
    """The last round a hand-in in this round leaves running: delay rounds on, never past the plan's last round
    (turn_rules.last_round), so no text promises a round that will not run."""
    if round_number is None:
        return None
    last = last_round()
    return round_number + delay if last is None else min(round_number + delay, last)


def submit(agent: str, config: SwarmConfig, deliverable: Deliverable) -> ToolDef:
    """submit for one neutral agent; what it hands in follows SwarmConfig.submit_kind (see the module docstring)."""
    texts = load_prompts()["submit"]
    kind = config.submit_kind

    async def execute(answer: str = "") -> str:
        submission = store_as(TeamSubmission)
        round_number = current_round()

        def refuse_a_second_hand_in() -> None:
            """Refuse the call when the hand-in it would make exists: the team's or the lone agent's, or in solo_task
            the agent's own."""
            if kind == "solo" and agent in submission.own:
                _refuse(submission, agent, round_number,
                        texts["already_own"].format(round=submission.own[agent]["round"]))
            if kind != "solo" and submission.agent is not None:
                _refuse(submission, agent, round_number, texts["already" if kind == "team" else "already_own"].format(
                    agent=submission.agent, round=submission.round, end=submission.ends_after_round))

        refuse_a_second_hand_in()
        missing = await _missing(deliverable, agent)
        if missing is not None:
            _refuse(submission, agent, round_number, texts["refused"].format(missing=missing))
        snapshot = await _snapshot(deliverable, agent)
        refuse_a_second_hand_in()      # a teammate's submit may have gone through while this call awaited the sandbox
        time = datetime.now(timezone.utc).isoformat()
        record = {"round": round_number, "answer": answer, "snapshot": snapshot, "time": time}
        if kind == "solo":
            submission.own = {**submission.own, agent: record}
            end_turns(agent, SUBMIT)
            transcript().info({TEAM_SUBMIT_TAG: {"agent": agent, **record, "kind": kind}})
            return texts["handed_in_own" if deliverable.told else "handed_in_own_untold"]
        end = _ends_after(round_number, config.submit_delay_rounds if kind == "team" else 0)
        submission.agent, submission.round, submission.answer, submission.time = agent, round_number, answer, time
        submission.ends_after_round, submission.snapshot = end, snapshot
        transcript().info({TEAM_SUBMIT_TAG: {"agent": agent, **record, "kind": kind, "ends_after_round": end}})
        if kind == "lone":
            return texts["handed_in_lone"]
        system_post(texts["notice"].format(agent=agent, round=round_number, end=end), config.names)
        return texts["handed_in"].format(end=end)

    return ToolDef(execute, name=TEAM_SUBMIT, description=described(deliverable.text),
                   parameters={"answer": load_prompts()["tool_args"]["submit"]["answer"]}, parallel=False)


def stop_working(agent: str, told: bool) -> ToolDef:
    """stop_working for one agent: its turns end at once, and it hands in nothing. Its description names teammates
    only for an agent told of its team (prompt_texts.stop_text)."""
    texts = load_prompts()["stop_working"]

    async def execute() -> str:
        end_turns(agent, STOP_WORKING)
        stops = store_as(StopWorking)
        stops.rounds = {**stops.rounds, agent: current_round() or 0}
        return texts["stopped"]

    return ToolDef(execute, name=STOP_WORKING, description=described(stop_text(told)), parallel=False)


def submit_bindings(config: SwarmConfig, deliverable: Deliverable) -> dict[str, ToolFactory]:
    """ORBIT tool_bindings for submit (under TEAM_SUBMIT) and stop_working. ORBIT builds a binding only for an agent
    whose AgentSpec.tools names it (ending_tools)."""
    return {TEAM_SUBMIT: ToolFactory(lambda ctx: submit(ctx.agent_name, config, deliverable)),
            STOP_WORKING: ToolFactory(lambda ctx: stop_working(ctx.agent_name, deliverable.told))}
