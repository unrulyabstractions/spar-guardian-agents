"""agent_actions: a regulator's view of its teammates' tool calls, as an Inspect tool.

The midterm report gives regulators "access to all tool calls from neutral agents"; the tool answers for every
teammate, disregulators included, so that a refusal never tells a regulator who holds a role. tasks.py binds it to
regulators only (board.agent_tool_names, board.board_bindings). Workers and disregulators never get it.

    agent_actions(agent="t4x9n", latest=20)
    r1 bash({"cmd": "git log --all"}) -> commit 3f2a...
    r2 board_post({"message": "found it"}) -> posted #3

One line per call, oldest first: the round of the activation that made the call, the tool, its arguments as JSON
and the start of its result, each cut to CUT characters (prompts.yaml `oversight_output`). A call about an unknown
name, or about the regulator itself, returns "no such agent".

The calls come from the agent's own conversation. During the run that is its live state in ORBIT's session registry
(turn_rules.live_state), the same source turn_activation.after_activation reads. ORBIT writes the attributed
conversation (eval_sample_view.agent_messages) only when the run ends (AgentScheduler.sync_to_task_state), so it is
the fallback outside a live run. The round of each call comes from
TurnState.history_marks, which after_activation writes; a call after the last mark belongs to the step under way.

A call counts as a board action of kind "oversight" (turn_rules.record_board_action): TurnState.oversight per regulator,
oversight_targets per regulator and target, and oversight_first with the round of the first call about each target. A
step that ends with agent_actions is a free step, within the regulator's K (free_board_actions_regulator).
"""
from __future__ import annotations

import json

from inspect_ai.model import ChatMessageAssistant, ChatMessageTool
from inspect_ai.solver._task_state import sample_state  # the running sample's TaskState: no public accessor
from inspect_ai.tool import ToolDef, ToolError
from inspect_ai.util import store_as

from src.configuration.settings import load_prompts
from src.export.eval_sample_view import SampleView, agent_messages
from src.swarm.turn_rules import OVERSIGHT_TOOL, TurnState, current_round, live_state, record_board_action

CUT = 200            # characters of a call's arguments and of its result


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
