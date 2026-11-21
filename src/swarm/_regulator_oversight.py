"""An influencer's view of its teammates' work, as two Inspect tools that answer in JSON.

The midterm report gives regulators "access to all tool calls from neutral agents"; disregulators get the same tools
since 2026-11-20. get_peer_actions shows what a teammate did: its tool calls, each with an id, its round, its tool and
its arguments in full. get_peer_observations shows what those calls returned: by id, or the latest, each result cut to
max_chars characters (16000 by default) with the cut stated (chars, shown_chars, truncated). Both answer for every
teammate, influencers included, so that a refusal never tells who holds a role; a call about an unknown name, or about
the caller itself, fails with "no such agent", and one about a teammate whose entry round has not come yet with
"<agent> has not entered yet." (turn_rules presence). A teammate that has exited still answers, with "status": "exited"
after "peer". tasks.py binds them to regulators and disregulators
(board.agent_tool_names, board.board_bindings). Workers never get them.

    get_peer_actions(peer="t4x9n", latest=2)
    {"peer": "t4x9n", "total_calls": 31, "shown": 2, "actions": [
      {"id": 29, "round": 7, "tool": "shell", "arguments": {"command": "git log --all"}},
      {"id": 30, "round": 7, "tool": "board_post", "arguments": {"message": "found it"}}],
     "not_shown": 0, "not_shown_ids": []}
    get_peer_observations(peer="t4x9n", ids=[29])
    {"peer": "t4x9n", "observations": [
      {"id": 29, "round": 7, "tool": "shell", "status": "ok", "result": "commit 3f2a...", "chars": 25420,
       "shown_chars": 16000, "truncated": true}], "not_shown": 0, "not_shown_ids": []}

The cap. A tool result past the run's cap (src/swarm/tool_output_caps.py, 64 KiB by default) is cut in the middle, which
would leave the JSON invalid. So each answer packs whole rows, the newest first for the latest calls and in the asked
order for ids, until the next would pass the cap less the wrapper, counts the rows it left out in not_shown and names as
many as fit in a quarter of the cap in not_shown_ids (oldest first), which a second call with ids returns. An id the
teammate has not reached is a row "no such call". A first row that alone passes the cap is cut to fit, the cut stated:
an observation's result further (shown_chars, truncated), an action's arguments as a JSON text (arguments_json,
arguments_chars, shown_chars, truncated).

A call's id is its place among the teammate's tool calls from 0, the transcript's numbering (call k). status is "ok",
"error" (result holds the error's message) or "pending" (the call is under way; result is null); an id the teammate has
not reached is "no such call". Until 2026-11-20 one tool, agent_actions, gave both in one line per call with the
arguments and the result each cut to 200 characters, a cut its description did not state; runs before then hold it,
and the readers (ladder_domain.OVERSIGHT) accept every name.

The calls come from the agent's own conversation. During the run that is the longer of its live state in ORBIT's
session registry (turn_rules.live_state), which ORBIT updates when the agent's round ends, and its conversation at its
last boundary (turn_rules.live_record().messages, kept by turn_activation.ends_round), so a teammate's calls of the
round under way show too. ORBIT writes the attributed conversation (eval_sample_view.agent_messages) only when the run
ends (AgentScheduler.sync_to_task_state), so it is the fallback outside a live run. The round of each call comes from
TurnState.history_marks, which after_activation writes; a call after the last mark belongs to the round under way.

Each call counts as a board action of kind "oversight" (turn_rules.record_board_action): TurnState.oversight per
influencer, oversight_targets per influencer and target, and oversight_first with the round of the first call about
each target. Both tools are board calls (turn_rules.FREE_TOOLS): they count toward a round's calls and use up none of
the caller's rounds.
"""
from __future__ import annotations

import json
from collections.abc import Callable
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
    Refused,
    TurnState,
    current_round,
    entered,
    exited,
    live_record,
    live_state,
    record_board_action,
)

MAX_CHARS = 16000    # the default cut of each result get_peer_observations returns
WRAP_MARGIN = 256    # bytes kept free under the cap beyond the answer's own wrapper


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
    """The agent's conversation: the longer of its live state and its conversation at its last boundary, else the
    sample's attributed conversation."""
    live, boundary = live_state(agent), live_record().messages.get(agent)
    if live is not None or boundary is not None:
        held = list(live.messages) if live is not None else []
        return list(boundary) if boundary is not None and len(boundary) > len(held) else held
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


def size(value) -> int:
    """The UTF-8 bytes of a value as the tools dump it."""
    return len(_dump(value).encode())


def fit(rows: list[dict], budget: int, shrink) -> tuple[list[dict], list[int]]:
    """The rows that fit whole in `budget` bytes, taken in order until the first that does not fit, and the ids of
    those left out. A first row that alone is over the budget is cut to fit (shrink(row, room))."""
    kept, used = [], 0
    for i, row in enumerate(rows):
        need = size(row) + 2                    # its comma and space in the list
        if used + need <= budget:
            kept.append(row)
            used += need
            continue
        if not kept:
            kept.append(shrink(row, budget - 2))
            rows = rows[i + 1:]
        else:
            rows = rows[i:]
        return kept, [r["id"] for r in rows]
    return kept, []


def cut_to(row: dict, key: str, text: str, room: int, extra) -> dict:
    """The row with `key` holding the longest head of `text` that keeps the row within `room` bytes; extra(shown)
    adds the fields that state the cut."""
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if size({**row, key: text[:mid], **extra(mid)}) <= room:
            low = mid
        else:
            high = mid - 1
    return {**row, key: text[:low], **extra(low)}


def shrink_action(row: dict, room: int) -> dict:
    """An action whose arguments alone pass the budget: its arguments as a JSON text cut to fit, the cut stated."""
    if "arguments" not in row:            # a "no such call" row has nothing to cut
        return row
    text = _dump(row["arguments"])
    base = {k: v for k, v in row.items() if k != "arguments"}
    return cut_to(base, "arguments_json", text, room,
                  lambda shown: {"arguments_chars": len(text), "shown_chars": shown, "truncated": True})


def shrink_observation(row: dict, room: int) -> dict:
    """An observation whose result alone passes the budget: the result cut further to fit, the cut stated."""
    text = row.get("result")
    if text is None:
        return row
    return cut_to(row, "result", text, room, lambda shown: {"shown_chars": shown, "truncated": True})


def ids_room(budget: int) -> int:
    """The bytes an answer keeps for the ids it left out: a quarter of the budget."""
    return budget // 4


def listed(ids: list[int], room: int) -> list[int]:
    """The first of `ids` that fit in `room` bytes as a JSON list."""
    out, used = [], 2
    for i in ids:
        used += len(str(i)) + 2
        if used > room:
            break
        out.append(i)
    return out


def packed(head: dict, rows_key: str, rows: list[dict], budget: int, shrink, newest_first: bool) -> dict:
    """The answer within `budget` bytes: head, the rows that fit whole (a first row alone over the room cut to fit),
    `not_shown` (how many were left out) and not_shown_ids (as many of their ids as fit in ids_room, oldest first for
    the newest calls). The rows give up only the room the left-out ids could take."""
    reserve = min(ids_room(budget), size([r["id"] for r in rows]))
    room = budget - size({**head, rows_key: [], "not_shown": len(rows), "not_shown_ids": []}) - reserve
    kept, left = fit(rows, room - WRAP_MARGIN, shrink)
    if newest_first:
        kept, left = kept[::-1], left[::-1]
    return {**head, rows_key: kept, "not_shown": len(left), "not_shown_ids": listed(left, ids_room(budget))}


def actions_payload(agent: str, calls: list[Called], latest: int, since_round: int | None,
                    ids: list[int] | None = None, budget: int = 16 * 1024, gone: bool = False) -> dict:
    """get_peer_actions' answer within `budget` bytes: the calls `ids` names, in that order, else the newest `latest`
    calls from `since_round` on, oldest first, arguments in full. The calls that did not fit are named in not_shown_ids,
    the oldest first for the newest calls, so a second call with ids gets them."""
    if ids is None:
        chosen = [c for c in calls if since_round is None or c.round >= since_round][-latest:]
        rows = [{"id": c.id, "round": c.round, "tool": c.tool, "arguments": c.arguments} for c in reversed(chosen)]
    else:
        rows = [{"id": c.id, "round": c.round, "tool": c.tool, "arguments": c.arguments} if (c := _at(calls, i))
                else {"id": i, "status": "no such call"} for i in ids]
    answer = packed({"peer": agent, **presence_head(gone), "total_calls": len(calls), "shown": 0}, "actions", rows,
                    budget, shrink_action, ids is None)
    return {**answer, "shown": len(answer["actions"])}


def _at(calls: list[Called], i: int) -> Called | None:
    return calls[i] if 0 <= i < len(calls) else None


def observation(c: Called, max_chars: int) -> dict:
    """One call's result as get_peer_observations returns it, cut to max_chars with the cut stated."""
    text = c.result
    row = {"id": c.id, "round": c.round, "tool": c.tool, "status": c.status, "result": None if text is None
           else text[:max_chars]}
    if text is not None:
        row.update(chars=len(text), shown_chars=min(len(text), max_chars), truncated=len(text) > max_chars)
    return row


def observations_payload(agent: str, calls: list[Called], ids: list[int] | None, latest: int, max_chars: int,
                         budget: int = 16 * 1024, gone: bool = False) -> dict:
    """get_peer_observations' answer within `budget` bytes: the results of the calls `ids` names, in that order, else
    of the newest `latest` calls, oldest first. The rows that did not fit are named in not_shown_ids."""
    if ids is None:
        rows = [observation(c, max_chars) for c in reversed(calls[-latest:])]
    else:
        rows = [observation(c, max_chars) if (c := _at(calls, i)) else {"id": i, "status": "no such call"}
                for i in ids]
    return packed({"peer": agent, **presence_head(gone)}, "observations", rows, budget, shrink_observation,
                  ids is None)


def presence_head(gone: bool) -> dict:
    """What a peer tool's answer says after "peer" of a teammate that has exited: {"status": "exited"}, else nothing."""
    return {"status": "exited"} if gone else {}


def checked_peer(allowed: set[str], agent: str) -> bool:
    """The peer tools' check of a name: a ToolError "no such agent" for a name not in `allowed`, "<agent> has not
    entered yet." for a teammate whose entry round has not come (turn_rules.entered). Returns whether it has exited."""
    texts = load_prompts()["oversight_output"]
    if agent not in allowed:
        raise ToolError(texts["no_such_agent"])
    st = store_as(TurnState)
    if st.entry_rounds and not entered(st, agent, current_round()):
        raise Refused(texts["not_entered"].format(agent=agent))
    return exited(st, agent)


def _teammate_calls(owner: str, allowed: set[str], agent: str) -> tuple[list[Called], bool]:
    """The teammate's calls and whether it has exited, after the check that it is one that has entered
    (checked_peer), the call counted as oversight of it."""
    gone = checked_peer(allowed, agent)
    record_board_action(owner, "oversight", target=agent)
    marks = store_as(TurnState).history_marks.get(agent, [])
    return called(conversation(agent), marks, current_round() or 0), gone


def _dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def get_peer_actions(owner: str, agents: list[str], cap: Callable[[], int] = lambda: 16 * 1024) -> ToolDef:
    """The tool bound to the influencer `owner`: any teammate's latest tool calls on the roster `agents`, its answer
    within cap() bytes (tool_output_caps.cap_of)."""
    prompts = load_prompts()
    args = prompts["tool_args"][ACTIONS_TOOL]
    allowed = set(agents) - {owner}

    async def execute(peer: str, latest: int = 20, since_round: int | None = None, ids: list[int] | None = None) -> str:
        if latest < 1:
            raise ToolError("latest must be at least 1")
        calls, gone = _teammate_calls(owner, allowed, peer)
        return _dump(actions_payload(peer, calls, latest, since_round, ids, cap(), gone))

    return ToolDef(execute, name=ACTIONS_TOOL, description=prompts["tools"][ACTIONS_TOOL],
                   parameters={"peer": args["peer"], "latest": args["latest"], "since_round": args["since_round"],
                               "ids": args["ids"]})


def get_peer_observations(owner: str, agents: list[str], cap: Callable[[], int] = lambda: 16 * 1024) -> ToolDef:
    """The tool bound to the influencer `owner`: what any teammate's tool calls returned, by id or the latest, its
    answer within cap() bytes (tool_output_caps.cap_of)."""
    prompts = load_prompts()
    args = prompts["tool_args"][OBSERVATIONS_TOOL]
    allowed = set(agents) - {owner}

    async def execute(peer: str, ids: list[int] | None = None, latest: int = 20, max_chars: int = MAX_CHARS) -> str:
        if latest < 1 or max_chars < 1:
            raise ToolError("latest and max_chars must be at least 1")
        calls, gone = _teammate_calls(owner, allowed, peer)
        return _dump(observations_payload(peer, calls, ids, latest, max_chars, cap(), gone))

    return ToolDef(execute, name=OBSERVATIONS_TOOL, description=prompts["tools"][OBSERVATIONS_TOOL],
                   parameters={"peer": args["peer"], "ids": args["ids"], "latest": args["latest"],
                               "max_chars": args["max_chars"]})
