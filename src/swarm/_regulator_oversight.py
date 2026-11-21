"""An influencer's view of its teammates' work, as two Inspect tools that answer in JSON.

The midterm report gives regulators "access to all tool calls from neutral agents"; disregulators get the same tools
since 2026-11-20. get_peer_actions shows what a teammate did: its tool calls, each with an id, its round, its tool and
its arguments in full. get_peer_observations shows what those calls returned: by id, or the latest, each result cut to
max_chars characters with the cut stated (chars, shown_chars, truncated). Both answer for every teammate, influencers
included, so that a refusal never tells who holds a role; a call about an unknown name, or about the caller itself,
fails with "no such agent". tasks.py binds them to regulators and disregulators (board.agent_tool_names,
board.board_bindings). Workers never get them.

    get_peer_actions(peer="t4x9n", latest=2)
    {"peer": "t4x9n", "total_calls": 31, "shown": 2, "actions": [
      {"id": 29, "round": 7, "tool": "shell", "arguments": {"command": "git log --all"}},
      {"id": 30, "round": 7, "tool": "board_post", "arguments": {"message": "found it"}}]}
    get_peer_observations(peer="t4x9n", ids=[29])
    {"peer": "t4x9n", "observations": [
      {"id": 29, "round": 7, "tool": "shell", "status": "ok", "result": "commit 3f2a...", "chars": 5420,
       "shown_chars": 4000, "truncated": true}]}

A call's id is its place among the teammate's tool calls from 0, the transcript's numbering (call k). status is "ok",
"error" (result holds the error's message) or "pending" (the call is under way; result is null); an id the teammate has
not reached is "no such call". Until 2026-11-20 one tool, agent_actions, gave both in one line per call with the
arguments and the result each cut to 200 characters, a cut its description did not state; runs before then hold it,
and the readers (ladder_domain.OVERSIGHT) accept every name.

The calls come from the agent's own conversation. During the run that is its live state in ORBIT's session registry
(turn_rules.live_state), the same source turn_activation.after_activation reads. ORBIT writes the attributed
conversation (eval_sample_view.agent_messages) only when the run ends (AgentScheduler.sync_to_task_state), so it is
the fallback outside a live run. The round of each call comes from TurnState.history_marks, which after_activation
writes; a call after the last mark belongs to the step under way.

Each call counts as a board action of kind "oversight" (turn_rules.record_board_action): TurnState.oversight per
influencer, oversight_targets per influencer and target, and oversight_first with the round of the first call about
each target. A step that ends with either tool is a free step, within the caller's K
(free_board_actions_influencer, the same for both influencers).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from inspect_ai.model import ChatMessageAssistant, ChatMessageTool
from inspect_ai.solver._task_state import sample_state  # the running sample's TaskState: no public accessor
from inspect_ai.tool import ToolDef, ToolError
from inspect_ai.util import store_as

from src.configuration.settings import load_prompts
from src.export.eval_sample_view import SampleView, agent_messages
from src.swarm.turn_rules import (
    ACTIONS_TOOL,
    OBSERVATIONS_TOOL,
    TurnState,
    current_round,
    live_state,
    record_board_action,
)

MAX_CHARS = 4000     # the default cut of each result get_peer_observations returns


@dataclass(frozen=True)
class Called:
    """One tool call of a teammate: its id (place from 0), round, tool, arguments, and its result's text and status."""
    id: int
    round: int
    tool: str
    arguments: dict
    status: str              # ok | error | pending
    result: str | None


def conversation(agent: str) -> list:
    """The agent's conversation: its live state, else the sample's attributed conversation."""
    live = live_state(agent)
    if live is not None:
        return list(live.messages)
    state = sample_state()
    return agent_messages(SampleView.from_state(state, []), agent) if state is not None else []


def _round_of(index: int, marks: list[list[int]], running: int) -> int:
    """The round of the activation that wrote message `index`: the first mark that ends after it."""
    for messages_after, round_number in marks:
        if index < messages_after:
            return round_number
    return running


def called(messages: list, marks: list[list[int]], running: int) -> list[Called]:
    """Every tool call in the messages, in order, each with its id, round and result."""
    results: dict[str, ChatMessageTool] = {m.tool_call_id: m for m in messages
                                            if isinstance(m, ChatMessageTool) and m.tool_call_id}
    out: list[Called] = []
    for index, m in enumerate(messages):
        if not isinstance(m, ChatMessageAssistant) or not m.tool_calls:
            continue
        for call in m.tool_calls:
            result = results.get(call.id)
            if result is None:
                status, text = "pending", None
            elif result.error is not None:
                status, text = "error", result.error.message
            else:
                status, text = "ok", result.text
            out.append(Called(len(out), _round_of(index, marks, running), call.function, dict(call.arguments or {}),
                              status, text))
    return out


def actions_payload(agent: str, calls: list[Called], latest: int, since_round: int | None) -> dict:
    """get_peer_actions' answer: the newest `latest` calls from `since_round` on, oldest first, arguments in full."""
    chosen = [c for c in calls if since_round is None or c.round >= since_round][-latest:]
    return {"peer": agent, "total_calls": len(calls), "shown": len(chosen),
            "actions": [{"id": c.id, "round": c.round, "tool": c.tool, "arguments": c.arguments} for c in chosen]}


def observation(c: Called, max_chars: int) -> dict:
    """One call's result as get_peer_observations returns it, cut to max_chars with the cut stated."""
    text = c.result
    row = {"id": c.id, "round": c.round, "tool": c.tool, "status": c.status, "result": None if text is None
           else text[:max_chars]}
    if text is not None:
        row.update(chars=len(text), shown_chars=min(len(text), max_chars), truncated=len(text) > max_chars)
    return row


def observations_payload(agent: str, calls: list[Called], ids: list[int] | None, latest: int, max_chars: int) -> dict:
    """get_peer_observations' answer: the results of the calls `ids` names, in that order, else of the newest
    `latest` calls, oldest first."""
    if ids is None:
        rows = [observation(c, max_chars) for c in calls[-latest:]]
    else:
        rows = [observation(calls[i], max_chars) if 0 <= i < len(calls) else {"id": i, "status": "no such call"}
                for i in ids]
    return {"peer": agent, "observations": rows}


def _teammate_calls(owner: str, allowed: set[str], agent: str) -> list[Called]:
    """The teammate's calls, after the check that it is one, the call counted as oversight of it."""
    if agent not in allowed:
        raise ToolError(load_prompts()["oversight_output"]["no_such_agent"])
    record_board_action(owner, "oversight", target=agent)
    marks = store_as(TurnState).history_marks.get(agent, [])
    return called(conversation(agent), marks, current_round() or 0)


def _dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def get_peer_actions(owner: str, agents: list[str]) -> ToolDef:
    """The tool bound to the influencer `owner`: any teammate's latest tool calls on the roster `agents`."""
    prompts = load_prompts()
    args = prompts["tool_args"][ACTIONS_TOOL]
    allowed = set(agents) - {owner}

    async def execute(peer: str, latest: int = 20, since_round: int | None = None) -> str:
        if latest < 1:
            raise ToolError("latest must be at least 1")
        return _dump(actions_payload(peer, _teammate_calls(owner, allowed, peer), latest, since_round))

    return ToolDef(execute, name=ACTIONS_TOOL, description=prompts["tools"][ACTIONS_TOOL],
                   parameters={"peer": args["peer"], "latest": args["latest"], "since_round": args["since_round"]})


def get_peer_observations(owner: str, agents: list[str]) -> ToolDef:
    """The tool bound to the influencer `owner`: what any teammate's tool calls returned, by id or the latest."""
    prompts = load_prompts()
    args = prompts["tool_args"][OBSERVATIONS_TOOL]
    allowed = set(agents) - {owner}

    async def execute(peer: str, ids: list[int] | None = None, latest: int = 20, max_chars: int = MAX_CHARS) -> str:
        if latest < 1 or max_chars < 1:
            raise ToolError("latest and max_chars must be at least 1")
        calls = _teammate_calls(owner, allowed, peer)
        return _dump(observations_payload(peer, calls, ids, latest, max_chars))

    return ToolDef(execute, name=OBSERVATIONS_TOOL, description=prompts["tools"][OBSERVATIONS_TOOL],
                   parameters={"peer": args["peer"], "ids": args["ids"], "latest": args["latest"],
                               "max_chars": args["max_chars"]})
