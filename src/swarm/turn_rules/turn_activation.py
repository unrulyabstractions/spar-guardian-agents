"""The two trusted callbacks ORBIT runs for the plan: ends_round at every boundary of a round, after_activation after
every round.

ends_round (ScheduleConfig.end_activation) ends an agent's round: after a batch that held end_round, after N calls,
after a call that ended its turns, once the time limit has passed, or at a reply with no tool call
(_turn_rounds.ends). It also keeps the agent's conversation at each boundary in memory (Live.messages), for the peer
tools.

after_activation (ScheduleConfig.after_activation) counts the round (a record: rounds_used), writes its timeline entry
and marks why an agent stopped. An agent that has made all C of its tool calls (TurnState.call_budget, counted by the
gate in _turn_rounds) ends "rounds"; no count of rounds ends an agent. It then amends the pending plan: the ended
agents' steps removed, every step after the team's hand-in dropped, and every step after the round under way dropped
once no worker is left (workers_gone), so no regulator holds a run open. An agent that stops simply exits: its calls go
to nobody. Last it records the exits the plan now shows (_turn_presence): an ending a rule wrote (rounds, context) and
an agent out of steps (out_of_steps), so its peers hear of it before their next round.

    schedule = ScheduleConfig(steps=steps, after_activation=f"{turn_activation.__name__}.after_activation",
                              end_activation=f"{turn_activation.__name__}.ends_round")
"""
from __future__ import annotations

from collections.abc import Awaitable

from inspect_ai.log import transcript
from inspect_ai.model import ChatMessageAssistant, ChatMessageTool
from inspect_ai.util import store_as
from orbit.configs.scheduling import ScheduleStep

from src.configuration.swarm_config import Window
from src.swarm.turn_rules._turn_presence import ENDED_WHY, record_exit
from src.swarm.turn_rules._turn_pushes import TEAM_HEAD, look
from src.swarm.turn_rules._turn_rounds import charge, ends, name_job_call, take_round
from src.swarm.turn_rules._turn_state import (
    ACTOR,
    HALT_TAG,
    ROUND_END,
    ROUND_END_TAG,
    STOP_WORKING,
    SUBMIT,
    Alerts,
    TeamSubmission,
    TurnState,
    holdable,
    live_record,
    live_state,
    out_of_calls,
    running_record,
    running_step,
    submit_holds,
)
from src.swarm.turn_rules._turn_steps import step_round


def ends_round(member: str, boundary) -> bool:
    """ORBIT's end_activation callback: whether the member's round ends at this boundary (_turn_rounds.ends). Keeps
    the member's conversation as of the boundary for the peer tools (Live.messages)."""
    live_record().messages[member] = boundary.messages
    return ends(member, boundary) is not None


def _window(st: TurnState, agent: str) -> Window:
    start, end = st.windows.get(agent) or [1, None]
    return Window(start=start, end=end)


def _out_of_context(agent: str) -> bool:
    """Whether the agent's last generation stopped at the context limit."""
    state = live_state(agent)
    return state is not None and state.output is not None and state.output.stop_reason == "model_length"


def _activation_messages(agent: str, result) -> list:
    """The messages the activation added to the agent's conversation, from its live state."""
    state = live_state(agent)
    return list(state.messages[result.messages_before:result.messages_after]) if state is not None else []


def _ms(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def round_calls(messages: list, times: dict | None = None, jobs: dict | None = None) -> list[dict]:
    """The round's tool calls in order, from the messages its activation added: each {id, tool, k (its place in the
    round from 1), error (its result is an error), refused (our rules refused it: Refused),
    t0, t1 (UTC seconds, from the round record's times; None without one)}, and `job` for a command call that was
    still running when the round's time ran out: the id of the job it became (the round record's jobs)."""
    results = {m.tool_call_id: m for m in messages if isinstance(m, ChatMessageTool) and m.tool_call_id}
    out = []
    for m in messages:
        if not isinstance(m, ChatMessageAssistant):
            continue
        for call in m.tool_calls or []:
            t0, t1, refused = (times or {}).get(call.id, (None, None, None))
            result = results.get(call.id)
            out.append({"id": call.id, "tool": call.function, "k": len(out) + 1,
                        "error": result is not None and result.error is not None, "refused": refused is not None,
                        "t0": _ms(t0), "t1": _ms(t1), **({"job": jobs[call.id]} if call.id in (jobs or {}) else {})})
    return out


def over_time(record: dict | None, limit: int | None) -> float | None:
    """How far past T (TurnState.round_time_s) the round ran, in seconds: its end less its start and T. None for a
    round within T, without times or with no round clock."""
    if not record or not limit or record.get("t1") is None:
        return None
    over = record["t1"] - record["t0"] - limit
    return _ms(over) if over > 0 else None


def ending_of(st: TurnState, submission: TeamSubmission, agent: str, round_number: int) -> str | None:
    """"stop" or "submit" when the agent's stop_working or submit went through in the round just completed, read from
    the records: its turns ended by its own call (TurnState.ended), or the team's hand-in names it in this round. None
    otherwise. An alert ends nothing (the entry's `alert`)."""
    ended = st.ended.get(agent)
    if ended == STOP_WORKING:
        return "stop"
    if ended == SUBMIT or (submission.agent == agent and submission.round == round_number):
        return "submit"
    if any(d["decision"] == "hold" and d["submit"]["agent"] == agent and d["submit"]["round"] == round_number
           for d in getattr(submit_holds(), "decisions", [])):
        return "submit"                   # a regulator held it in the same round, before this callback
    return None


def _end_reason(agent: str, record: dict | None, messages: list) -> str | None:
    """Why the round ended: the round record's (ends_round), else "context" when the agent's last generation stopped at
    the context limit (its loop ends there, with no boundary), else "no_tool_call" when its last reply called nothing.
    None when ORBIT ended the member's loop otherwise."""
    if record is not None and record.get("end"):
        return record["end"]
    if _out_of_context(agent):
        return "context"
    last = next((m for m in reversed(messages) if isinstance(m, ChatMessageAssistant)), None)
    return "no_tool_call" if last is not None and not last.tool_calls else None


def _stop_reason(st: TurnState, agent: str) -> str | None:
    """Why after_activation stops an agent: its last generation hit the context limit, or it has made all C of its
    tool calls (TurnState.call_budget), the ending "rounds" (its name from when rounds were the budget). No count of
    rounds ends an agent. submit and stop_working end their caller themselves (submit_and_stop.end_turns)."""
    if _out_of_context(agent):
        return "context"
    if out_of_calls(st, agent):
        return "rounds"
    return None


def _halt(st: TurnState, submission: TeamSubmission, keep: list[dict]) -> list[dict]:
    """After the team's hand-in: keep the steps up to its last round (ends_after_round), drop every later one."""
    last = submission.ends_after_round
    if last is None:
        return []
    if st.halted_after_round is None:
        st.halted_after_round = last
        transcript().info({HALT_TAG: {"round": submission.round, "ends_after_round": last, "agent": submission.agent,
                                      "dropped": [s["id"] for s in keep if step_round(s["id"]) > last]}})
    return [s for s in keep if step_round(s["id"]) <= last]


def workers_gone(st: TurnState) -> bool:
    """Whether every worker's turns have ended, by a stop, its own hand-in or a limit, while another agent's have not:
    the run is over then. A regulator cannot stop and a disregulator cannot hand in, so neither keeps it going. False
    with no worker, where only the plan's end stops the run, and once every agent has ended, which empties the plan.
    False too while a regulator may hold the hand-in (SwarmConfig.submit_hold, holdable): the hand-in's own last
    round ends the run then, and a hold gives the worker its turns back."""
    return bool(st.workers) and set(st.workers) <= st.ended.keys() and len(st.ended) < len(st.agents) \
        and not holdable()


def restored(step: ScheduleStep | None, pending: list) -> list:
    """The pending plan, or after a regulator's hold (SubmitHolds.restore) the written plan's steps after the step
    under way (Live.plan), which the hand-in's cut and the submitter's ending had taken out; after_activation then
    keeps only the active agents' part of them, as after any round."""
    holds = submit_holds()
    if holds is None or not holds.restore:
        return pending
    holds.restore = False
    plan = live_record().plan
    ids = [s["id"] for s in plan]
    at = ids.index(step.id) + 1 if step is not None and step.id in ids else 0
    return [ScheduleStep.model_validate(s) for s in plan[at:]]


def _without_workers(st: TurnState, keep: list[dict], step: ScheduleStep | None) -> list[dict]:
    """The amended plan, last of all: once no worker is left (workers_gone) the steps up to the round under way, which
    finishes, and no later one."""
    if not workers_gone(st):
        return keep
    last = step_round(step.id) if step is not None else 0
    if st.halted_after_round != last:
        st.halted_after_round = last
        transcript().info({HALT_TAG: {"round": last, "ends_after_round": last, "agent": None,
                                      "dropped": [s["id"] for s in keep if step_round(s["id"]) > last]}})
    return [s for s in keep if step_round(s["id"]) <= last]


def after_activation(runtime, result, global_turn: int) -> Awaitable[None] | None:
    """ORBIT calls this after every round of the plan (ScheduleConfig.after_activation): the round's record
    (_after_round), then the benchmark's round-end callback when it has one (ROUND_END) and a read of the team's head
    when the benchmark has a reader (_turn_pushes.look), awaited after the record."""
    hook, reader = ROUND_END.get(), TEAM_HEAD.get()
    if hook is None and reader is None:
        _after_round(runtime, result, global_turn)
        return None
    agent, step = result.agent_name, running_step(runtime)
    round_number = step_round(step.id) if step is not None else 0
    calls = [{"id": c.id, "tool": c.function, "arguments": c.arguments}
             for m in _activation_messages(agent, result) if isinstance(m, ChatMessageAssistant)
             for c in m.tool_calls or []]
    _after_round(runtime, result, global_turn)

    async def then() -> None:
        if hook is not None:
            try:
                await hook(agent, round_number, calls)
            except Exception as error:  # noqa: BLE001 - a failed callback is recorded; the run goes on
                transcript().info({ROUND_END_TAG: {"agent": agent, "round": round_number,
                                                   "error": f"{type(error).__name__}: {error}"[:500]}})
        await look()
    return then()


def _after_round(runtime, result, global_turn: int) -> None:
    """The round's record, after every round of the plan.

    Counts the round (every round uses up one of the agent's rounds), writes its timeline entry (its calls with their
    times, why it ended, its ending: submit or stop when that tool went through, and its last alert), records why the
    agent stopped, and amends the pending plan: an ended agent's steps (TurnState.ended,
    which submit and stop_working also write) are removed; the team's hand-in (TeamSubmission) cuts every step after
    its last round; once no worker is left (workers_gone) every step after the round under way goes, whatever a
    hand-in left running. The timeline marks an agent's first round ("entered") and the round after which its window
    holds no more steps ("left").
    """
    st = store_as(TurnState)
    agent = result.agent_name
    step = running_step(runtime)
    activation = st.activations.get(agent, 0) + 1
    tools = [call["tool"] for call in result.tool_calls]
    round_number = step_round(step.id) if step is not None else 0
    messages = _activation_messages(agent, result)
    record = take_round(agent)
    end_reason = _end_reason(agent, record, messages)
    if end_reason in ("no_tool_call", "context") and not (record or {}).get("end"):
        charge(agent)                     # a reply with no tool call, with no boundary to count it (ends)
    calls = round_calls(messages, (record or {}).get("times"), (record or {}).get("jobs")) if messages else \
        [{"tool": tool, "k": k} for k, tool in enumerate(tools, 1)]
    st.history_marks = {**st.history_marks,
                        agent: [*st.history_marks.get(agent, []), [result.messages_after, round_number]]}
    submission = store_as(TeamSubmission)
    ending = ending_of(st, submission, agent, round_number)
    used = st.rounds_used.get(agent, 0) + 1
    st.activations = {**st.activations, agent: activation}
    st.rounds_used = {**st.rounds_used, agent: used}
    actual = list(runtime.pending_steps)
    pending = restored(step, actual)
    reason = None if ending is not None else _stop_reason(st, agent)   # its own ending comes first
    if reason and agent not in st.ended:
        st.ended = {**st.ended, agent: reason}
        st.ended_round = {**st.ended_round, agent: round_number}
    left = _window(st, agent).end is not None and not any(agent in s.agents for s in pending)
    entry = {"turn": global_turn, "agent": agent, "activation": activation, "round": round_number,
             "step": step.id if step is not None else None, "ending": ending, "used": used,
             "tools": tools, "calls": calls, "end": end_reason, "calls_used": st.calls_used.get(agent, 0),
             "t0": _ms((record or {}).get("t0")), "t1": _ms((record or {}).get("t1")),
             "ended": st.ended.get(agent), "entered": activation == 1, "left": left}
    over = over_time(record, st.round_time_s)
    if over is not None:
        entry["over_s"] = over
    for call in calls:
        if call.get("job"):
            name_job_call(call["job"], call["id"])
    alerts = [a for a in store_as(Alerts).alerts if a["agent"] == agent and a.get("round") == round_number]
    if alerts:
        entry["alert"] = alerts[-1]
    st.timeline = [*st.timeline, entry]

    active = [name for name in st.agents if name not in st.ended]
    keep = []
    for s in pending:
        agents = [name for name in s.agents if name in active]
        if len(agents) == len(s.agents):
            keep.append(s.model_dump(mode="json"))
        elif agents:
            keep.append(s.model_copy(update={"agents": tuple(agents)}).model_dump(mode="json"))
    before = [s.model_dump(mode="json") for s in actual]
    halted = submission.agent is not None
    if halted:
        keep = _halt(st, submission, keep)
    keep = _without_workers(st, keep, step)
    _presence(st, runtime, step, agent, keep, halted)
    _amend(st, runtime, agent, keep, before, halted)


def _called_back_later(runtime, step: ScheduleStep | None, agent: str) -> set[str]:
    """The members of the step under way whose callback comes after this one: ORBIT calls back in activation order
    once a concurrent step has finished (StepRecord.activations). Their own callbacks judge them."""
    current = running_record(runtime)
    if step is None or current is None:
        return set()
    order = [a.agent for a in current.activations]
    return set(order[order.index(agent) + 1:]) if agent in order else set(step.agents) - {agent}


def out_of_steps(st: TurnState, keep: list[dict], skip: set[str] = frozenset()) -> list[str]:
    """The active agents with no exit yet that the amended plan `keep` holds in no step: its window closed, or the
    plan's rounds are over. An agent in `skip` is judged later."""
    holders = {a for s in keep for a in s["agents"]}
    return [a for a in st.agents if a not in st.ended and a not in st.exits and a not in holders and a not in skip]


def _presence(st: TurnState, runtime, step: ScheduleStep | None, agent: str, keep: list[dict], halted: bool) -> None:
    """Record the exits the amended plan shows (_turn_presence.record_exit): every ending in TurnState.ended without
    one (rounds, context; submit and stop_working record their own), then, unless the run is
    ending (halted: a hand-in, or no worker left), every agent out of steps as "rounds"."""
    round_number = step_round(step.id) if step is not None else st.rounds
    for name, reason in st.ended.items():
        if name not in st.exits and reason in ENDED_WHY:
            record_exit(name, ENDED_WHY[reason], round_number)
    if halted or workers_gone(st) or len(st.agents) < 2:
        return
    for name in out_of_steps(st, keep, _called_back_later(runtime, step, agent)):
        record_exit(name, "rounds", round_number)


def _amend(st: TurnState, runtime, agent: str, keep: list[dict], before: list[dict], halted: bool) -> None:
    if keep == before:
        return
    why = SUBMIT if halted else st.ended.get(agent) or "stopped"
    runtime.amend_pending(keep, actor=ACTOR, reason=f"{agent}: {why}", expected_revision=runtime.revision)

