"""The board: one shared channel as ORBIT configuration, and the pull board's tools over ORBIT's store.

A board is one shared channel that every agent on the roster can read and write. There are no invocation edges: no
agent calls, hands off to, or waits on another. Nothing is pushed: ORBIT's delivery is "poll", and posts are visible
as soon as they are sent ("live"); a concurrent round runs on a snapshot of the board as of its start
(turn_rules.schedule). Turns follow a written plan, so ORBIT's scheduling_mode does not apply.

    comm    = board_communication(names, max_message_chars=2000)
    execute = board_execution(plan=turn_rules.schedule(config, names))
    sched   = board_scheduler(max_turns=config.max_turns + 5, max_time_seconds=3600)

Agents check, read and post through board_status, board_read and board_post, which tasks.py binds to every agent of
a board (ORBIT tool_bindings, one ToolFactory per name, the way SWE binds shell and run_python). ORBIT attaches
channel_post, channel_read and channel_list to every agent as soon as a CommunicationConfig is set, and
turn_rules.TurnRulesFilter withholds them from the provider.

The tools keep no store of their own. They read and write ORBIT's communication store (orbit/communication/):
    board_post    send_message, as channel_post does, so exports, the transcript and the browser see a normal post
    board_read    read_messages for the selection, record_read for the read event and its receipt, which calls
                  delivery.acknowledge_read and advances the agent's read cursor to the newest post returned
    board_status  read_messages for the counts and delivery.unread_messages for "new": posts by others above the
                  agent's read cursor
read_messages honours a concurrent round's snapshot, so an agent sees the board as of its step's start. A post's
number (#17) is its position on the board, oldest first; it does not change when later posts arrive.

team_submit is bound to neutral agents only, and only when the mode has a team to submit for
(SwarmConfig.has_team_submit). The first call records turn_rules.TeamSubmission and an info event tagged
turn_rules.TEAM_SUBMIT_TAG; turn_rules.after_activation then lets the round finish and drops every later step.
agent_actions (src/swarm/regulator_oversight.py) is bound to regulators only, over every teammate.

Budgets per round (SwarmConfig.reads_per_turn, posts_per_turn; turn_rules.spend). board_read returns at most the posts
left in the agent's read budget for the round, the newest of its selection, and says how many it left out; with none
left it raises a ToolError and counts the refusal (TurnState.reads_refused). board_post past the post budget raises a
ToolError, sends nothing and counts the refusal (posts_refused). The counts reset at each round. Regulators read
without a budget and without the read_page cap: a board_read with no selection returns the whole board.

Each tool also counts the action in turn_rules.TurnState (checks, reads, posts, read_senders, discovery_round).
Descriptions come from src/configuration/prompts.yaml `tools`, argument descriptions from `tool_args`, the result
lines from `board_output` and the budget texts from `budget`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from inspect_ai.log import transcript
from inspect_ai.tool import ToolDef, ToolError
from inspect_ai.util import store_as
from orbit.communication.delivery import unread_messages
from orbit.communication.errors import ChannelCapacityError
from orbit.communication.runtime import read_messages, record_read, send_message
from orbit.communication.state import CommunicationState
from orbit.configs.communication import ChannelConfig, CommunicationConfig
from orbit.configs.execution import AgentGroup, ExecutionConfig
from orbit.configs.scheduler import SchedulerConfig
from orbit.configs.scheduling import ScheduleConfig
from orbit.tools import ToolFactory

from src.configuration.settings import load_prompts
from src.configuration.swarm_config import SwarmConfig, role_of
from src.swarm.regulator_oversight import agent_actions
from src.swarm.turn_rules import (
    BOARD_ID,
    BOARD_TOOLS,
    OVERSIGHT_TOOL,
    TEAM_SUBMIT,
    TEAM_SUBMIT_TAG,
    TeamSubmission,
    budget_used,
    current_round,
    record_board_action,
    refuse,
    spend,
)

BOARD_NAME = "team board"


# ------------------------------------------------------------------------------------------------ ORBIT configuration

def board_channel(agents: list[str], *, channel_id: str = BOARD_ID, name: str = BOARD_NAME) -> ChannelConfig:
    return ChannelConfig(id=channel_id, name=name, readers=tuple(agents), writers=tuple(agents), private=False)


def board_communication(agents: list[str], *, max_message_chars: int) -> CommunicationConfig:
    """The board as ORBIT's CommunicationConfig: one channel, nothing pushed, posts visible at once."""
    return CommunicationConfig(channels=(board_channel(agents),), delivery="poll", visibility="live",
                               max_message_chars=max_message_chars)


def board_execution(*, plan: ScheduleConfig | None = None, agent_groups: list[AgentGroup] | None = None,
                    initial_context: str = "auto") -> ExecutionConfig:
    """Scheduled execution under a written plan, one generation and every call in it per step (quantum
    model_step).

    history_projection="all" projects every agent's conversation into the sample with
    EnvironmentState:message_attribution, even after a hit limit.
    """
    return ExecutionConfig(executor="scheduled", quantum="model_step", plan=plan, history_projection="all",
                           agent_groups=list(agent_groups or []), initial_context=initial_context)


def board_scheduler(*, max_turns: int, max_time_seconds: int) -> SchedulerConfig:
    return SchedulerConfig(max_turns=max_turns, max_time_seconds=max_time_seconds, halt_on_convergence=False,
                           convergence_window=1, health_checks=False)


# ------------------------------------------------------------------------------------------------ the tools

def _board(agent: str, board: str) -> list:
    """Every post on the board the agent may see, oldest first (ORBIT read_messages, snapshot included)."""
    try:
        return read_messages(board, agent_id=agent)
    except PermissionError as error:
        raise ToolError(str(error)) from None


def _numbers(board: str) -> dict[str, int]:
    """Each post's position on the board, from ORBIT's canonical store."""
    posts = [m for m in store_as(CommunicationState).messages if m.channel_id == board]
    return {m.id: i for i, m in enumerate(posts, 1)}


def _render(message, number: int) -> str:
    return load_prompts()["board_output"]["post"].format(sender=message.sender, sequence=number,
                                                         text=message.content)


def board_status(agent: str, board: str = BOARD_ID) -> ToolDef:
    """Counts of posts by sender, and how many are new since the agent's last read."""
    prompts = load_prompts()
    out = prompts["board_output"]

    async def execute() -> str:
        posts = _board(agent, board)
        new = [m for m in unread_messages(agent) if m.channel_id == board]
        record_board_action(agent, "check")
        lines = []
        for sender in sorted({m.sender for m in posts}):
            total = sum(1 for m in posts if m.sender == sender)
            fresh = sum(1 for m in new if m.sender == sender)
            lines.append(out["sender"].format(sender=sender, total=total, new=fresh))
        lines.append(out["total"].format(total=len(posts), new=len(new)))
        return "\n".join(lines)

    return ToolDef(execute, name="board_status", description=prompts["tools"]["board_status"])


def _newest(posts: list, count: int | None) -> list:
    """The newest `count` posts, or every post when count is None."""
    return posts if count is None else posts[-count:]


def board_read(agent: str, read_page: int | None, board: str = BOARD_ID, reads_per_turn: int | None = None) -> ToolDef:
    """The selected posts, oldest first, at most read_page in all and at most what is left of the agent's read
    budget for the round; marks them read. read_page None (a regulator) selects without a cap, and a call with no
    selection then returns the whole board; reads_per_turn None reads without a budget."""
    prompts = load_prompts()
    args = prompts["tool_args"]["board_read"]
    budget = prompts["budget"]

    async def execute(latest: int | None = None, from_agent: str | None = None,
                      latest_from: int | None = None) -> str:
        for name, value in (("latest", latest), ("latest_from", latest_from)):
            if value is not None and value < 1:
                raise ToolError(f"{name} must be at least 1")
        if latest_from is not None and from_agent is None:
            raise ToolError("latest_from needs from_agent")
        left = None
        if reads_per_turn is not None:
            used = budget_used(agent, "read")
            left = reads_per_turn - used
            if left <= 0:
                refuse(agent, "read")
                raise ToolError(budget["read_refused"].format(used=used))
        posts = _board(agent, board)
        chosen: dict[str, object] = {}
        if latest is None and from_agent is None:
            chosen.update({m.id: m for m in _newest(posts, read_page)})
        if latest is not None:
            chosen.update({m.id: m for m in posts[-latest:]})
        if from_agent is not None:
            theirs = [m for m in posts if m.sender == from_agent]
            chosen.update({m.id: m for m in _newest(theirs, latest_from or read_page)})
        selected = _newest(sorted(chosen.values(), key=lambda m: m.sequence), read_page)
        cut = 0
        if left is not None and len(selected) > left:
            cut, selected = len(selected) - left, selected[-left:]
        numbers = _numbers(board)
        rendered = {m.id: _render(m, numbers[m.id]) for m in selected}
        if rendered:
            record_read(board, agent_id=agent, rendered_messages=rendered)
        if reads_per_turn is not None:
            spend(agent, "read", len(selected))
        record_board_action(agent, "read", [m.sender for m in selected])
        lines = list(rendered.values()) + ([budget["read_cut"].format(cut=cut)] if cut else [])
        return "\n".join(lines)

    return ToolDef(execute, name="board_read", description=prompts["tools"]["board_read"],
                   parameters={"latest": args["latest"], "from_agent": args["from_agent"],
                               "latest_from": args["latest_from"]})


def board_post(agent: str, board: str = BOARD_ID, posts_per_turn: int | None = None) -> ToolDef:
    """Post through ORBIT's send_message, as channel_post does, within the agent's post budget for the round."""
    prompts = load_prompts()

    async def execute(message: str) -> str:
        if not message:
            raise ToolError("A nonempty message is required")
        if posts_per_turn is not None:
            used = budget_used(agent, "post")
            if used >= posts_per_turn:
                refuse(agent, "post")
                raise ToolError(prompts["budget"]["post_refused"].format(used=used))
        try:
            sent = send_message(board, agent_id=agent, content=message)
        except (ChannelCapacityError, PermissionError) as error:
            raise ToolError(str(error)) from None
        if posts_per_turn is not None:
            spend(agent, "post", 1)
        record_board_action(agent, "post")
        return prompts["board_output"]["posted"].format(sequence=_numbers(board)[sent.id])

    return ToolDef(execute, name="board_post", description=prompts["tools"]["board_post"],
                   parameters={"message": prompts["tool_args"]["board_post"]["message"]})


def team_submit(agent: str) -> ToolDef:
    """Record the team's submission; the run ends after the round under way (turn_rules.after_activation)."""
    prompts = load_prompts()
    out = prompts["board_output"]

    async def execute(answer: str = "") -> str:
        submission = store_as(TeamSubmission)
        round_number = current_round()
        if submission.agent is not None:
            submission.refused = [*submission.refused, {"agent": agent, "round": round_number}]
            return out["already_submitted"].format(agent=submission.agent, round=submission.round)
        submission.agent, submission.round, submission.answer = agent, round_number, answer
        submission.time = datetime.now(timezone.utc).isoformat()
        transcript().info({TEAM_SUBMIT_TAG: {"agent": agent, "round": round_number, "answer": answer,
                                             "time": submission.time}})
        return out["submitted"]

    return ToolDef(execute, name=TEAM_SUBMIT, description=prompts["tools"]["team_submit"],
                   parameters={"answer": prompts["tool_args"]["team_submit"]["answer"]})


# ------------------------------------------------------------------------------------------------ binding

def board_bindings(config: SwarmConfig, board: str = BOARD_ID,
                   roles: dict[str, str] | None = None) -> dict[str, ToolFactory]:
    """ORBIT tool_bindings for the board tools, team_submit and agent_actions, built per agent. Empty without a
    board.

    A binding is built only for the agents whose AgentSpec.tools names it (agent_tool_names). A regulator (roles)
    reads without the read_page cap and without a read budget; agent_actions covers every teammate on the roster
    (config.names), so a refusal never tells roles apart."""
    if not config.board:
        return {}

    def regulator(ctx) -> bool:
        return role_of(roles, ctx.agent_name) == "regulator"

    return {"board_status": ToolFactory(lambda ctx: board_status(ctx.agent_name, board)),
            "board_read": ToolFactory(lambda ctx: board_read(
                ctx.agent_name, None if regulator(ctx) else config.read_page, board,
                None if regulator(ctx) else config.reads_per_turn)),
            "board_post": ToolFactory(lambda ctx: board_post(ctx.agent_name, board, config.posts_per_turn)),
            TEAM_SUBMIT: ToolFactory(lambda ctx: team_submit(ctx.agent_name)),
            OVERSIGHT_TOOL: ToolFactory(lambda ctx: agent_actions(ctx.agent_name, config.names))}


def agent_tool_names(config: SwarmConfig, roles: dict[str, str] | None = None) -> dict[str, list[str]]:
    """The board tool names each agent of the roster gets: the three board tools, team_submit for a neutral agent
    (no role, or the worker role) when the mode has a team to submit for, and agent_actions for a regulator."""
    if not config.board:
        return {n: [] for n in config.names}
    out = {}
    for n in config.names:
        role = role_of(roles, n)
        out[n] = [*BOARD_TOOLS, *([TEAM_SUBMIT] if config.has_team_submit and role == "worker" else []),
                  *([OVERSIGHT_TOOL] if role == "regulator" else [])]
    return out
