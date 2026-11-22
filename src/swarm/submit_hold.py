"""A regulator's hold on the team's hand-in (SwarmConfig.submit_hold): hold_submit and release_submit.

A worker's submit in a shared_task team hands in the team's work and ends the run submit_delay_rounds rounds later
(src/swarm/submit_and_stop.py). Under submit_hold every regulator holds two more tools while that hand-in waits for the
run's end (turn_rules.holdable):

    hold_submit(reason)     the hand-in is taken back: TeamSubmission's hand-in is emptied, the worker that submitted
                            gets its turns back (TurnState.ended and its exit dropped, SubmitHolds.returns), the plan's
                            cut is lifted (halted_after_round), and after_activation puts the written plan's steps back
                            (SubmitHolds.restore, turn_activation.restored). The run goes on and any worker may submit
                            again. Every agent present hears the presence line presence.held, and the board gets
                            submit_hold.notice from the system; neither names the regulator.
    release_submit(reason)  the hand-in stands and can no longer be held (SubmitHolds.released).

A hand-in neither held nor released stands. A call while no hand-in waits, or after a release, is refused
(turn_rules.Refused, submit_hold.none). Each call that goes through is recorded in SubmitHolds.decisions ({decision,
agent, round, reason, time, submit}) and as an info event tagged HOLD_TAG; run.json carries them as submit_holds. Both
tools are built with parallel=False, as the ending tools are. Workers and disregulators never hold them, and only a
regulator's prompts name them (prompts.yaml roles.submit_hold, tools, tool_args).

    names = hold_tools(config, "regulator")       # ["hold_submit", "release_submit"] under submit_hold, else []
    bindings = hold_bindings(config)
"""
from __future__ import annotations

from datetime import datetime, timezone

from inspect_ai.log import transcript
from inspect_ai.tool import ToolDef
from inspect_ai.util import store_as
from orbit.tools import ToolFactory

from src.configuration.settings import load_prompts
from src.configuration.swarm_config import SwarmConfig
from src.swarm.board import system_post
from src.swarm.turn_rules import (
    HOLD_TAG,
    HOLD_TOOL,
    RELEASE_TOOL,
    Refused,
    SubmitHolds,
    TeamSubmission,
    TurnState,
    current_round,
    holdable,
)

TOOLS = (HOLD_TOOL, RELEASE_TOOL)
HAND_IN = ("agent", "round", "answer", "time", "ends_after_round", "snapshot")   # TeamSubmission's hand-in fields


def hold_tools(config: SwarmConfig, role: str) -> list[str]:
    """The binding keys of the hold tools an agent of this role gets: both for a regulator under submit_hold."""
    return list(TOOLS) if config.submit_hold and role == "regulator" else []


def _decide(agent: str, decision: str, reason: str) -> dict:
    """Record one decision about the waiting hand-in, or refuse the call when none waits."""
    if not holdable():
        raise Refused(load_prompts()["submit_hold"]["none"])
    submission = store_as(TeamSubmission)
    record = {"decision": decision, "agent": agent, "round": current_round(), "reason": reason,
              "time": datetime.now(timezone.utc).isoformat(),
              "submit": {field: getattr(submission, field) for field in HAND_IN}}
    holds = store_as(SubmitHolds)
    holds.decisions = [*holds.decisions, record]
    transcript().info({HOLD_TAG: record})
    return record


def _take_back(submitter: str) -> None:
    """Empty the team's hand-in and give the worker that submitted its turns back; after_activation puts the plan's
    steps back."""
    submission = store_as(TeamSubmission)
    for field in HAND_IN:
        setattr(submission, field, TeamSubmission.model_fields[field].default)
    st = store_as(TurnState)
    st.ended = {a: why for a, why in st.ended.items() if a != submitter}
    st.exits = {a: event for a, event in st.exits.items() if a != submitter}
    st.halted_after_round = None
    holds = store_as(SubmitHolds)
    holds.returns = {**holds.returns, submitter: holds.returns.get(submitter, 0) + 1}
    holds.restore = True


def hold_submit(agent: str, names: list[str]) -> ToolDef:
    """hold_submit for one regulator (see the module docstring)."""
    prompts = load_prompts()

    async def execute(reason: str = "") -> str:
        record = _decide(agent, "hold", reason)
        submitter = record["submit"]["agent"]
        _take_back(submitter)
        system_post(prompts["submit_hold"]["notice"].format(agent=submitter, round=record["submit"]["round"]), names)
        return prompts["submit_hold"]["held"].format(agent=submitter)

    return ToolDef(execute, name=HOLD_TOOL, description=prompts["tools"][HOLD_TOOL],
                   parameters={"reason": prompts["tool_args"][HOLD_TOOL]["reason"]}, parallel=False)


def release_submit(agent: str) -> ToolDef:
    """release_submit for one regulator (see the module docstring)."""
    prompts = load_prompts()

    async def execute(reason: str = "") -> str:
        record = _decide(agent, "release", reason)
        store_as(SubmitHolds).released = True
        return prompts["submit_hold"]["released"].format(agent=record["submit"]["agent"])

    return ToolDef(execute, name=RELEASE_TOOL, description=prompts["tools"][RELEASE_TOOL],
                   parameters={"reason": prompts["tool_args"][RELEASE_TOOL]["reason"]}, parallel=False)


def hold_bindings(config: SwarmConfig) -> dict[str, ToolFactory]:
    """ORBIT tool_bindings for both tools under submit_hold, else none. ORBIT builds a binding only for an agent whose
    AgentSpec.tools names it (hold_tools)."""
    if not config.submit_hold:
        return {}
    return {HOLD_TOOL: ToolFactory(lambda ctx: hold_submit(ctx.agent_name, config.names)),
            RELEASE_TOOL: ToolFactory(lambda ctx: release_submit(ctx.agent_name))}
