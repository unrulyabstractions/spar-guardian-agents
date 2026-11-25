"""A worker's unsubmit (SwarmConfig.unsubmit): it takes the team's hand-in back.

Under unsubmit (SwarmConfig.gives_unsubmit: a shared_task team whose workers hold submit) a worker's submit hands in
the team's work and ends nobody's turns, its caller's included (src/swarm/submit_and_stop.py). The run ends
submit_delay_rounds rounds after the submit, and the snapshot taken at the submit is graded. Every worker holds one
more tool, with no limit on its calls:

    unsubmit(reason)    the standing hand-in is taken back: TeamSubmission's hand-in is emptied, the plan's cut is
                        lifted (halted_after_round), and after_activation puts the written plan's steps back
                        (Unsubmits.restore, turn_activation.restored). The run goes on, any worker may submit again,
                        and a new submit starts a new wait of submit_delay_rounds. The board gets unsubmit.notice
                        from the system, and every agent present hears it in its next round (turn_rules presence).

The submitter may take back its own hand-in. A call while no hand-in stands is refused (turn_rules.Refused,
unsubmit.none). Each call that goes through is recorded in Unsubmits.unsubmits ({agent, round, reason, time, submit:
the hand-in it took back, seq}) and as an info event tagged UNSUBMIT_TAG; run.json carries them as unsubmits, beside
each hand-in (Unsubmits.submits, which record_submit writes). With no hand-in standing at the end the task grades its
shared result as it is then. The tool is built with parallel=False, as the ending tools are. Regulators and
disregulators never hold it, and no text of theirs names it.

    names = unsubmit_tools(config, "worker")       # ["unsubmit"] under gives_unsubmit, else []
    bindings = unsubmit_bindings(config, told=True)
"""
from __future__ import annotations

from datetime import datetime, timezone

from inspect_ai.log import transcript
from inspect_ai.tool import ToolDef
from inspect_ai.util import store_as
from orbit.tools import ToolFactory

from src.configuration.prompt_texts import described, unsubmit_text
from src.configuration.settings import load_prompts
from src.configuration.swarm_config import SwarmConfig
from src.swarm.board import system_post
from src.swarm.turn_rules import (
    UNSUBMIT_TAG,
    UNSUBMIT_TOOL,
    Refused,
    TeamSubmission,
    TurnState,
    Unsubmits,
    current_round,
)

HAND_IN = ("agent", "round", "answer", "time", "ends_after_round", "snapshot")   # TeamSubmission's hand-in fields


def unsubmit_tools(config: SwarmConfig, role: str) -> list[str]:
    """The binding keys of the unsubmit tool an agent of this role gets: unsubmit for a worker under
    gives_unsubmit."""
    return [UNSUBMIT_TOOL] if config.holds_unsubmit(role) else []


def _seq(record: Unsubmits) -> int:
    """The next number of the one order of hand-ins and unsubmits."""
    return len(record.submits) + len(record.unsubmits)


def record_submit(hand_in: dict) -> None:
    """Record a hand-in of the team's work that went through (Unsubmits.submits), for the presence lines and run.json;
    submit calls it under gives_unsubmit."""
    record = store_as(Unsubmits)
    record.submits = [*record.submits, {**hand_in, "seq": _seq(record)}]


def standing_hand_in() -> dict | None:
    """The team's hand-in as it stands (TeamSubmission's hand-in fields), or None when none stands."""
    submission = store_as(TeamSubmission)
    return None if submission.agent is None else {field: getattr(submission, field) for field in HAND_IN}


def _take_back() -> None:
    """Empty the team's hand-in and lift the plan's cut; after_activation puts the plan's steps back."""
    submission = store_as(TeamSubmission)
    for field in HAND_IN:
        setattr(submission, field, TeamSubmission.model_fields[field].default)
    store_as(TurnState).halted_after_round = None
    store_as(Unsubmits).restore = True


def unsubmit(agent: str, names: list[str], told: bool) -> ToolDef:
    """unsubmit for one worker (see the module docstring). Its description names teammates only for an agent told of
    its team (prompt_texts.unsubmit_text)."""
    prompts = load_prompts()
    texts = prompts["unsubmit"]

    async def execute(reason: str) -> str:
        hand_in = standing_hand_in()
        if hand_in is None:
            raise Refused(texts["none"])
        record = store_as(Unsubmits)
        entry = {"agent": agent, "round": current_round(), "reason": reason,
                 "time": datetime.now(timezone.utc).isoformat(), "submit": hand_in, "seq": _seq(record)}
        record.unsubmits = [*record.unsubmits, entry]
        _take_back()
        transcript().info({UNSUBMIT_TAG: entry})
        system_post(texts["notice"].format(agent=agent, reason=reason), names)
        return texts["taken_back"]

    return ToolDef(execute, name=UNSUBMIT_TOOL, description=described(unsubmit_text(told)),
                   parameters={"reason": prompts["tool_args"][UNSUBMIT_TOOL]["reason"]}, parallel=False)


def unsubmit_bindings(config: SwarmConfig, told: bool) -> dict[str, ToolFactory]:
    """ORBIT tool_bindings for unsubmit under gives_unsubmit, else none. ORBIT builds a binding only for an agent whose
    AgentSpec.tools names it (unsubmit_tools). told: whether the agents' system prompts tell them of their team."""
    if not config.gives_unsubmit:
        return {}
    return {UNSUBMIT_TOOL: ToolFactory(lambda ctx: unsubmit(ctx.agent_name, config.names, told))}
