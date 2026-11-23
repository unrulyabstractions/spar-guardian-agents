"""The tools that end an agent's work, submit (it hands work in) and stop_working (it hands in nothing), and
alert_humans, an influencer's alert, which ends nothing.

Who holds which, beside the board's tools (ending_tools; src/tasks.py binds them through ORBIT tool_bindings):

    agent                        tools
    lone agent                   submit
    neutral, shared_task         submit, stop_working (shutdown)
    neutral, solo_task           submit, meaning its own work
    disregulator                 stop_working (shared_task, shutdown), alert_humans (on a board)
    regulator                    alert_humans (on a board); the run ends once every worker has ended
                                 (turn_activation.workers_gone)

No agent gets ORBIT's own submit (AgentSpec.submit=False). Ours is bound under the key team_submit, because ORBIT
reserves "submit", and the turn-rules defense shows it to the model as submit (TurnRulesDefense.get_tool_wrappers).
Both tools are built with parallel=False, so a call never runs beside the other calls of its batch.

submit first awaits the benchmark's guard (Deliverable.missing, Benchmark.gradable). With nothing to grade it refuses
the call (turn_rules.Refused), naming what is missing, and records the call in TeamSubmission.refused. A submit made
after the hand-in it would make is refused and recorded the same way; it is checked again after the guard's await,
so of two concurrent submits one goes through. Otherwise:
  * shared_task: the team's work. TeamSubmission records the hand-in, the board gets a notice from the system, the
    caller's own turns end at once (TurnState.ended "submit"), and turn_activation.after_activation cuts the plan
    after round r + submit_delay_rounds, or after the plan's last round when that comes first (turn_rules.last_round).
  * lone agent: the same record; the run ends after the round of the call.
  * solo_task: the agent's own work, recorded in TeamSubmission.own. Its turns end at once (TurnState.ended
    "submit"); its teammates continue.
stop_working ends its caller's turns at once (TurnState.ended "stop_working", StopWorking.rounds) and hands in
nothing; its rounds go to nobody. A submit or stop_working that goes through ends the caller's round after its
reply, and a later call of that reply is refused (turn_rules._turn_rounds). Each of the two records its caller's exit
(turn_rules presence), which every peer present hears before its next round. alert_humans records an alert (Alerts:
who, round, reason, the agents it names) as an info event tagged ALERT_TAG and ends nothing: the caller keeps working
and may alert again, and every peer present hears "<agent> has alerted humans." before its next round. An info event
tagged TEAM_SUBMIT_TAG records each submit that went through. A guard or snapshot that loses the sandbox raises, so
ORBIT ends the sample as for any tool. The texts come from src/configuration/prompts.yaml `submit`, `stop_working`,
`alert` and `tools`.

    bindings = submit_bindings(config, Deliverable(text, missing, snapshot, told))
    names = ending_tools(config, "worker")       # ["team_submit", "stop_working"]
    names = ending_tools(config, "regulator")    # ["alert_humans"]
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import NoReturn

from inspect_ai.log import transcript
from inspect_ai.tool import ToolDef
from inspect_ai.util import SandboxUnavailableError, store_as
from orbit.tools import ToolFactory

from src.configuration.prompt_texts import described, stop_text
from src.configuration.settings import load_prompts
from src.configuration.swarm_config import INFLUENCERS, SwarmConfig
from src.swarm.board import system_post
from src.swarm.turn_rules import (
    ALERT_TAG,
    ALERT_TOOL,
    STOP_WORKING,
    SUBMIT,
    TEAM_SUBMIT,
    TEAM_SUBMIT_TAG,
    Alerts,
    Refused,
    StopWorking,
    TeamSubmission,
    TurnState,
    current_round,
    last_round,
    record_exit,
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


def holds_alert(config: SwarmConfig, role: str) -> bool:
    """Whether an agent of this role gets alert_humans: a regulator or a disregulator on a board. Workers never do."""
    return config.board and role in INFLUENCERS


def ending_tools(config: SwarmConfig, role: str) -> list[str]:
    """The binding keys of the tools that end an agent's work, as an agent of this role holds them: submit
    (TEAM_SUBMIT) when it holds submit, then stop_working when it holds that, then alert_humans for an influencer."""
    return [*([TEAM_SUBMIT] if config.holds_submit(role) else []),
            *([STOP_WORKING] if config.holds_stop_working(role) else []),
            *([ALERT_TOOL] if holds_alert(config, role) else [])]


def end_turns(agent: str, reason: str) -> None:
    """End the agent's turns at once: TurnState.ended takes the reason, unless the agent already ended, and
    turn_activation.after_activation takes its steps out of the plan."""
    st = store_as(TurnState)
    if agent not in st.ended:
        st.ended = {**st.ended, agent: reason}


def _refuse(submission: TeamSubmission, agent: str, round_number: int | None, reason: str) -> NoReturn:
    """Record a submit that hands in nothing, then refuse it with the reason."""
    submission.refused = [*submission.refused, {"agent": agent, "round": round_number, "reason": reason}]
    raise Refused(reason)


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
            record_exit(agent, "submit", round_number, hand_in="solo")
            transcript().info({TEAM_SUBMIT_TAG: {"agent": agent, **record, "kind": kind}})
            return texts["handed_in_own" if deliverable.told else "handed_in_own_untold"]
        end = _ends_after(round_number, config.submit_delay_rounds if kind == "team" else 0)
        submission.agent, submission.round, submission.answer, submission.time = agent, round_number, answer, time
        submission.ends_after_round, submission.snapshot = end, snapshot
        transcript().info({TEAM_SUBMIT_TAG: {"agent": agent, **record, "kind": kind, "ends_after_round": end}})
        if kind == "lone":
            return texts["handed_in_lone"]
        end_turns(agent, SUBMIT)
        record_exit(agent, "submit", round_number, hand_in="team", ends_after_round=end)
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
        record_exit(agent, "stop", current_round())
        return texts["stopped"]

    return ToolDef(execute, name=STOP_WORKING, description=described(stop_text(told)), parallel=False)


def alert_humans(agent: str) -> ToolDef:
    """alert_humans for one influencer: the alert is recorded (Alerts, an info event tagged ALERT_TAG) and every peer
    hears of it before its next round (turn_rules presence); the caller's turns go on."""
    prompts = load_prompts()
    args = prompts["tool_args"][ALERT_TOOL]

    async def execute(reason: str, agents: list[str] | None = None) -> str:
        alert = {"agent": agent, "round": current_round(), "reason": reason, "agents": list(agents or []),
                 "time": datetime.now(timezone.utc).isoformat()}
        record = store_as(Alerts)
        record.alerts = [*record.alerts, alert]
        transcript().info({ALERT_TAG: alert})
        return prompts["alert"]["recorded"]

    return ToolDef(execute, name=ALERT_TOOL, description=prompts["tools"][ALERT_TOOL],
                   parameters={"reason": args["reason"], "agents": args["agents"]}, parallel=False)


def submit_bindings(config: SwarmConfig, deliverable: Deliverable) -> dict[str, ToolFactory]:
    """ORBIT tool_bindings for submit (under TEAM_SUBMIT), stop_working and alert_humans. ORBIT builds a binding only
    for an agent whose AgentSpec.tools names it (ending_tools)."""
    return {TEAM_SUBMIT: ToolFactory(lambda ctx: submit(ctx.agent_name, config, deliverable)),
            STOP_WORKING: ToolFactory(lambda ctx: stop_working(ctx.agent_name, deliverable.told)),
            ALERT_TOOL: ToolFactory(lambda ctx: alert_humans(ctx.agent_name))}
