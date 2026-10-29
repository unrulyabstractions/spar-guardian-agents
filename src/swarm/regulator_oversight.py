"""agent_actions: a regulator's view of its teammates' tool calls, as an Inspect tool.

The midterm report gives regulators "access to all tool calls from neutral agents"; the tool answers for every
teammate, disregulators included, so that a refusal never tells a regulator who holds a role. tasks.py binds it to
regulators only (pull_board.agent_tool_names, pull_board.board_bindings). Workers and disregulators never get it.

    agent_actions(agent="a02", latest=20)
    r1 bash({"cmd": "git log --all"}) -> commit 3f2a...
    r2 board_post({"message": "found it"}) -> posted #3

One line per call, oldest first: the round of the activation that made the call, the tool, its arguments as JSON
and the start of its result, each cut to CUT characters (prompts.yaml `oversight_output`). A call about an unknown
name, or about the regulator itself, returns "no such agent".

The calls come from the agent's own conversation. During the run that is ORBIT's live session record
(orbit/execution/sessions.py, current_session_registry().scheduled(agent).state.messages), the same source
turn_rules.after_activation reads. ORBIT writes the attributed conversation (the store's
EnvironmentState:message_attribution, which src/swarm/awareness_probes.py reads) only when the run ends
(AgentScheduler.sync_to_task_state), so it is the fallback outside a live run. The round of each call comes from
TurnState.history_marks, which after_activation writes; a call after the last mark belongs to the step under way.

A call counts as a board action of kind "oversight" (turn_rules.record_board_action): TurnState.oversight per regulator
and oversight_targets per regulator and target. A step that ends with agent_actions is a free step, within the
regulator's K (free_board_actions_regulator).
"""
from __future__ import annotations

import json

from inspect_ai.model import ChatMessageAssistant, ChatMessageTool
from inspect_ai.solver._task_state import sample_state
from inspect_ai.tool import ToolDef, ToolError
from inspect_ai.util import store, store_as
from orbit.execution.sessions import current_session_registry

from src.configuration.settings import load_prompts
from src.swarm.awareness_probes import ATTRIBUTION_KEY
from src.swarm.turn_rules import OVERSIGHT_TOOL, TurnState, current_round, record_board_action

__all__ = ["OVERSIGHT_TOOL", "CUT", "agent_actions", "conversation", "call_lines"]

CUT = 200            # characters of a call's arguments and of its result


def conversation(agent: str) -> list:
    """The agent's conversation: ORBIT's live session record, else the sample's attributed conversation."""
    try:
        return list(current_session_registry().scheduled(agent).state.messages)
    except (RuntimeError, KeyError):
        pass
    state = sample_state()
    if state is None:
        return []
    attribution = store().get(ATTRIBUTION_KEY) or []
    return [state.messages[a["index"]] for a in attribution
            if a.get("agent") == agent and a["index"] < len(state.messages)]


def _round_of(index: int, marks: list[list[int]], running: int) -> int:
    """The round of the activation that wrote message `index`: the first mark that ends after it."""
    for messages_after, round_number in marks:
        if index < messages_after:
            return round_number
    return running


def _cut(text: str) -> str:
    return text[:CUT]


def call_lines(messages: list, marks: list[list[int]], running: int, latest: int) -> list[str]:
    """The newest `latest` tool calls in the messages, oldest first, one line each (prompts.yaml oversight_output)."""
    out = load_prompts()["oversight_output"]
    results: dict[str, str] = {}
    for m in messages:
        if isinstance(m, ChatMessageTool) and m.tool_call_id:
            results[m.tool_call_id] = m.error.message if m.error is not None else m.text
    lines = []
    for index, m in enumerate(messages):
        if not isinstance(m, ChatMessageAssistant) or not m.tool_calls:
            continue
        for call in m.tool_calls:
            arguments = json.dumps(call.arguments, ensure_ascii=False, default=str)
            result = results.get(call.id)
            lines.append(out["line"].format(round=_round_of(index, marks, running), tool=call.function,
                                            arguments=_cut(arguments),
                                            result=_cut(result) if result is not None else out["pending"]))
    return lines[-latest:]


def agent_actions(owner: str, agents: list[str]) -> ToolDef:
    """The tool bound to the regulator `owner`: the latest tool calls of any teammate on the roster `agents`."""
    prompts = load_prompts()
    args = prompts["tool_args"][OVERSIGHT_TOOL]
    out = prompts["oversight_output"]
    allowed = set(agents) - {owner}

    async def execute(agent: str, latest: int = 20) -> str:
        if latest < 1:
            raise ToolError("latest must be at least 1")
        if agent not in allowed:
            return out["no_such_agent"]
        record_board_action(owner, "oversight", target=agent)
        marks = store_as(TurnState).history_marks.get(agent, [])
        lines = call_lines(conversation(agent), marks, current_round() or 0, latest)
        return "\n".join(lines) if lines else out["no_calls"]

    return ToolDef(execute, name=OVERSIGHT_TOOL, description=prompts["tools"][OVERSIGHT_TOOL],
                   parameters={"agent": args["agent"], "latest": args["latest"]})
