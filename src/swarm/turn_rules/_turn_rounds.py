"""Rounds of calls: each agent's round under way, the line that opens it, the gate on its calls and where it ends.

An agent's round is one ORBIT activation under the completion quantum: model calls one after another, each seeing the
results before it. A reply asking for several calls runs them as one batch. The round ends at the first of these, the
end reason its timeline entry keeps:

    end_round     a batch held end_round (or a domain tool that closes the round: chess's pass_turn, close_round)
    calls         the round made N calls (TurnState.calls_per_round); end_round never counts
    ending        a call ended the agent's turns (submit, stop_working or alert_humans went through), or its submit
                  handed in the run's work (a lone agent's)
    time          the agents' time limit has passed (TurnState.time_limit_s from the first round's start)
    no_tool_call  a reply with no tool call
    context       a reply cut at the context limit

ends decides at each boundary of the activation (ORBIT ScheduleConfig.end_activation, turn_activation.ends_round).
counted, a tool wrapper on every agent's tools, refuses with a ToolError (prompts.yaml `round`) a call past N, a call
after end_round in the same batch and a call after the agent's turns ended, and times every call.

The round under way lives in memory (TurnState's Live.rounds), never in the store: after_activation turns it into the
round's timeline entry. open_round starts it before the round's first model call and appends the durable line
"Round {n} of {R}." to the agent's conversation, n counting the rounds the agent has used up plus one.

    record = round_record("k7q2m")
    record["made"], record["closed"], record["end"]       # 3, False, None
"""
from __future__ import annotations

import time

from inspect_ai.agent import AgentState
from inspect_ai.log import transcript
from inspect_ai.model import ChatMessageAssistant, ChatMessageUser
from inspect_ai.tool import ToolDef, ToolError
from inspect_ai.util import store_as

from src.configuration.settings import load_prompts
from src.swarm.turn_rules._turn_state import END_ROUND, ROUND_TAG, TeamSubmission, TurnState, current_round, live_record

END_REASONS = ("end_round", "calls", "ending", "time", "no_tool_call", "context")


def now() -> float:
    """UTC seconds: the clock of rounds and calls."""
    return time.time()


def round_record(agent: str, round_number: int | None = None) -> dict:
    """The agent's round under way, made when missing or when the round of the step under way (current_round) is
    another: {round, n, t0, t1, made, closed, end, runs, times, seen}. made counts the calls toward N, runs the calls
    the wrapper started in the batch under way, times each settled call's (t0, t1, refused) by call id."""
    rounds = live_record().rounds
    round_number = round_number if round_number is not None else current_round() or 0
    record = rounds.get(agent)
    if record is None or record["round"] != round_number:
        st = store_as(TurnState)
        record = {"round": round_number, "n": st.rounds_used.get(agent, 0) + 1, "t0": now(), "t1": None,
                  "made": 0, "closed": False, "end": None, "runs": [], "times": {}, "seen": None}
        rounds[agent] = record
    return record


def take_round(agent: str) -> dict | None:
    """The agent's round record, removed from memory: after_activation's, once the round is over."""
    return live_record().rounds.pop(agent, None)


def close_round(agent: str) -> None:
    """End the agent's round after the batch under way: end_round, and a domain tool that ends the round (chess's
    pass_turn)."""
    round_record(agent)["closed"] = True


def round_line(n: int, of: int) -> str:
    return load_prompts()["round"]["line"].format(round=n, rounds=of)


def open_round(agent: str, state: AgentState) -> str | None:
    """Start the agent's round and append its line "Round {n} of {R}." to its conversation (ROUND_TAG metadata), once
    per round: a boundary edit ORBIT rejects is delivered again at the next. Notes the first round's start
    (TurnState.started_at). Returns the line appended, or None. Outside a planned step nothing happens."""
    round_number = current_round()
    if round_number is None:
        return None
    st = store_as(TurnState)
    if st.started_at is None:
        st.started_at = now()
    record = round_record(agent, round_number)
    if any(((m.metadata or {}).get(ROUND_TAG) or {}).get("round") == round_number for m in state.messages
           if isinstance(m, ChatMessageUser)):
        return None
    tag = {"round": round_number, "n": record["n"], "of": st.round_budget}
    text = round_line(record["n"], st.round_budget)
    state.messages.append(ChatMessageUser(content=text, metadata={ROUND_TAG: tag}))
    transcript().info({ROUND_TAG: {"agent": agent, **tag, "content": text}})
    return text


# ------------------------------------------------------------------------------------------------ the gate on calls

def turns_over(st: TurnState, agent: str) -> bool:
    """Whether the agent's turns have ended (TurnState.ended), or its submit handed in the run's work: a lone agent's
    hand-in ends the run after its round."""
    return agent in st.ended or store_as(TeamSubmission).agent == agent


def _begin(agent: str, tool: str) -> dict:
    """Start one call of the agent's round: its run record, or a ToolError when the call is refused (prompts.yaml
    `round`): past N, after end_round in the batch, or after the agent's turns ended."""
    record = round_record(agent)
    run = {"tool": tool, "t0": now(), "t1": None, "refused": None}
    record["runs"].append(run)
    if tool == END_ROUND:
        record["closed"] = True
        return run
    st, texts = store_as(TurnState), load_prompts()["round"]
    if turns_over(st, agent):
        refused = texts["ended"]
    elif record["closed"]:
        refused = texts["closed"]
    elif record["made"] >= st.calls_per_round > 0:
        refused = texts["spent"].format(calls=st.calls_per_round)
    else:
        record["made"] += 1
        return run
    run["refused"], run["t1"] = refused, run["t0"]
    raise ToolError(refused)


def counted(agent: str):
    """A tool wrapper for the agent's tools: each call goes through _begin, and its end time is kept."""

    def wrap(tool):
        definition = tool if isinstance(tool, ToolDef) else ToolDef(tool)

        async def execute(**kwargs):
            run = _begin(agent, definition.name)
            try:
                return await definition.tool(**kwargs)
            finally:
                run["t1"] = now()

        wrapped = ToolDef(execute, name=definition.name, description=definition.description,
                          parameters=definition.parameters, parallel=definition.parallel, viewer=definition.viewer,
                          model_input=definition.model_input, max_output=definition.max_output,
                          options=definition.options)
        return wrapped if isinstance(tool, ToolDef) else wrapped.as_tool()

    return wrap


# ------------------------------------------------------------------------------------------------ where a round ends

def _settle(record: dict, messages: list) -> None:
    """After a batch: pair each call of the last reply with the run the wrapper started for it (by tool, in the
    reply's order), keep its times by call id, and count the batch's calls toward N, end_round left out. A call that
    never reached the wrapper (its arguments did not parse) counts too, without times."""
    last = next((m for m in reversed(messages) if isinstance(m, ChatMessageAssistant)), None)
    if last is None or not last.tool_calls or last.id == record["seen"]:
        return
    record["seen"] = last.id
    runs, asked = list(record["runs"]), 0
    for call in last.tool_calls:
        at = next((i for i, run in enumerate(runs) if run["tool"] == call.function), None)
        run = runs.pop(at) if at is not None else {"t0": None, "t1": None, "refused": None}
        record["times"][call.id] = (run["t0"], run["t1"], run["refused"])
        if call.function == END_ROUND:
            record["closed"] = True
        else:
            asked += 1
    record["runs"] = []
    record["asked"] = record.get("asked", 0) + asked
    record["made"] = max(record["made"], record["asked"])


def past_deadline(st: TurnState) -> bool:
    return bool(st.time_limit_s) and st.started_at is not None and now() >= st.started_at + st.time_limit_s


def ends(agent: str, boundary) -> str | None:
    """Why the agent's round ends at this boundary (END_REASONS), or None to go on; the reason and the time are kept
    in the round record."""
    record = round_record(agent)
    st = store_as(TurnState)
    reason = None
    if boundary.after == "reply":
        reason = "context" if boundary.output.stop_reason == "model_length" else "no_tool_call"
    elif boundary.after == "tools":
        _settle(record, boundary.messages)
        if turns_over(st, agent):
            reason = "ending"
        elif record["closed"]:
            reason = "end_round"
        elif record["made"] >= st.calls_per_round > 0:
            reason = "calls"
        elif past_deadline(st):
            reason = "time"
    if reason is not None:
        record["end"], record["t1"] = reason, now()
    return reason
