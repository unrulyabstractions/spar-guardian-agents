"""The model filter of the turn rules and the passive defense that carries it.

TurnRulesFilter changes what the provider receives and never the durable conversation. It removes react()'s assistant
paragraph from the system message (provider_system_text), withholds ORBIT's channel tools, gives a call inside a round
the board notice when the board changed since the agent's last call (by default the counts and each new post's first
line, notice_text) as the last paragraph of the tool result it receives next (with_paragraph: a tool result stays a
tool result, its role, id and call id unchanged), and appends the continue prompt when the input would end with a model
turn. Everything it adds stays in place on every later call (TurnState.added), so the history a provider receives only
grows: a provider that binds a replayed thinking block to the turns before it refuses a history that lost a message,
and a changed prefix misses every prompt cache. The defense that carries it also shows team_submit to the model as
submit (shown_as_submit), gates and times every tool call of a round (_turn_rounds.counted), and is the one part of the
turn rules that writes the durable conversation: before each round it appends one user message to the activated
member's conversation, the round's message (apply_pre_turn, _turn_messages): the round's line
(_turn_rounds.open_round), the presence lines due to it (_turn_presence.presence_part) and the board's changes since it
last looked (boundary_notice), each its own paragraph.
turn_rules_defense builds the DefenseConfig that installs all of it on every agent.

Inspect writes the ModelEvent before ORBIT's model filters run (FilteredModelAPI sits under Inspect's
Model.generate), so ModelEvent.input keeps the paragraph and lacks what the filter adds, and ModelEvent.tools
still lists the channel tools. The filter therefore writes info events tagged TURN_RULES_TAG: "system_prompt" at an
agent's first call, when its system message lost the paragraph; "withheld_tools" naming the tools the provider did
not receive; and "notice" and "continue" carrying each added text, which the transcript builder reads: a notice names
the message whose last paragraph it became (`into`), a text of its own message the message it follows (`after`). The
board part of a round's message writes its "notice" info event too, marked boundary (the conversation holds that one),
and its TurnState.added entry, once ORBIT has committed the message (settle).

    experiment["defenses"] = [turn_rules_defense(config).model_dump(mode="json")]
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace

from inspect_ai.agent import AgentPrompt
from inspect_ai.log import transcript
from inspect_ai.model import ChatMessageTool, ChatMessageUser, ContentText
from inspect_ai.tool import ToolDef
from inspect_ai.util import store_as
from orbit.communication.runtime import read_messages
from orbit.configs.defense import DefenseConfig
from orbit.defenses.base import DefenseBase, DefenseVerdict
from orbit.defenses.model_filter import ModelFilter
from orbit.defenses.registry import register_defense

from src.configuration.settings import load_prompts
from src.configuration.swarm_config import SwarmConfig
from src.swarm.turn_rules._turn_messages import Part, round_message
from src.swarm.turn_rules._turn_presence import presence_part
from src.swarm.turn_rules._turn_pushes import look
from src.swarm.turn_rules._turn_rounds import counted, open_round
from src.swarm.turn_rules._turn_state import (
    BOARD_ID,
    CHANNEL_TOOLS,
    LIVE,
    NOTICE_TAG,
    SUBMIT,
    SYSTEM_SENDER,
    TEAM_SUBMIT,
    TURN_RULES_TAG,
    TurnState,
    bump,
    current_round,
    is_record,
    live_record,
)

# The board notice's previews (notice=previews): the most new posts it previews, and the most characters of each.
PREVIEW_POSTS = 5
PREVIEW_CHARS = 120

TURN_RULES_TYPE = "swarm.turn_rules.v1"
TURN_RULES_NAME = "turn_rules"
# A target that names no agent builds no DefenseBoundary, so the filter runs without a check event per message.
FILTER_ONLY_TARGET = "__model_filter_only__"


# The assistant paragraph react() appends to an agent's instructions after a blank line. ORBIT 2.0 runs Inspect's own
# react(), so the paragraph is Inspect's default; ORBIT 1.x ran its copied loop (orbit/agents/turn_react.py, deleted in
# 2.0) with the paragraph below, which the logs of every run before the move to 2.0 hold.
ASSISTANT_PROMPT = AgentPrompt().assistant_prompt
ORBIT_1X_ASSISTANT_PROMPT = """
You are a helpful assistant attempting to submit the best possible answer.
You have several tools available to help with finding the answer. You will
see the result of tool calls right after sending the message. Do not output
more than one tool call in your response, until you see the results of the
previous tool call. If you need to perform multiple actions, you can always
send more messages with additional tool calls. Do some reasoning before your
actions, describing what tool calls you are going to use and how they fit
into your plan.
"""
ASSISTANT_PROMPTS = (ASSISTANT_PROMPT, ORBIT_1X_ASSISTANT_PROMPT)


def provider_system_text(text: str) -> str:
    """The system text the provider receives: the stored system message without the assistant paragraph react()
    appends after a blank line (ASSISTANT_PROMPTS: Inspect's under ORBIT 2.0, ORBIT's own in a 1.x log) and that
    blank line. "" when the paragraph was the whole message; text without the paragraph comes back unchanged. The
    filter applies it to every call; a reader of a run whose filter logged a "system_prompt" info event applies it to
    show what the provider saw."""
    for paragraph in ASSISTANT_PROMPTS:
        text = text.replace(f"\n\n{paragraph}", "").replace(paragraph, "")
    return text


def shown_as_submit(tool):
    """A tool wrapper: team_submit comes back named submit, every other tool unchanged. ORBIT binds a tool only under
    its own name and reserves "submit", so the rename happens after binding."""
    definition = tool if isinstance(tool, ToolDef) else ToolDef(tool)
    if definition.name != TEAM_SUBMIT:
        return tool
    renamed = ToolDef(definition.tool, name=SUBMIT, description=definition.description,
                      parameters=definition.parameters, parallel=definition.parallel, viewer=definition.viewer,
                      model_input=definition.model_input, max_output=definition.max_output, options=definition.options)
    return renamed if isinstance(tool, ToolDef) else renamed.as_tool()


def preview_line(text: str) -> str:
    """A post's first line that holds text, cut to PREVIEW_CHARS, with prompts.yaml notice.cut where anything of the
    post was left out."""
    lines = [line.strip() for line in text.strip().splitlines()]
    first = next((line for line in lines if line), "")
    shown = first[:PREVIEW_CHARS].rstrip()
    cut = shown != text.strip()
    return shown + (load_prompts()["notice"]["cut"] if cut else "")


def mentions(text: str, agent: str) -> bool:
    """Whether a post names the agent by its ID, as a word."""
    return re.search(rf"(?<![\w-]){re.escape(agent)}(?![\w-])", text) is not None


def notice_text(kind: str, counts: dict[str, tuple[int, int]] | None = None,
                previews: list[tuple[str, str, bool]] | None = None, first: bool = False) -> str | None:
    """The board notice (prompts.yaml notice) for notice=kind, or None for "none": "The board changed: a02 1 new of
    2." and, while the agent has been told of no post (first: every post is new), "Posts on the board: 1 by a02.".

    counts maps each sender with new posts to (new, total), in the order to print. previews, for kind "previews",
    lists the posts to preview as (sender, text, whether it names the agent), in the order to print."""
    if kind == "none":
        return None
    notice = load_prompts()["notice"]
    changed, with_counts, count = ("first_changed", "first_counts", "first_count") if first else \
        ("changed", "counts", "count")
    if kind == "changed":
        return notice[changed]
    lines = "; ".join(notice[count].format(sender=sender, new=new, total=total)
                      for sender, (new, total) in (counts or {}).items())
    head = notice[with_counts].format(counts=lines)
    if kind == "counts":
        return head
    shown = [notice["preview"].format(sender=sender, mention=notice["mention"] if named else "",
                                      line=preview_line(text)) for sender, text, named in previews or []]
    return "\n".join([head, *shown, notice["full"]])


def board_news(config: SwarmConfig, agent: str, team: bool, seen: int,
               skip_system: bool = False) -> tuple[str | None, int] | None:
    """(the board notice, the top sequence) when the board holds posts by others above `seen`, the sequence the agent
    was last told of (seen_of); the caller marks the top seen once the agent holds the notice. None for notice=none,
    off a board, or with nothing new. skip_system leaves the system's posts out of the notice (the text is None when
    nothing else is new), though the top still counts them: the round's message tells of them in its presence part.

    counts gives each such sender's new posts (above that sequence) and total posts; previews, for notice=previews, up
    to PREVIEW_POSTS of those posts, chosen with the ones that name the agent first and then the newest, and printed in
    board order. The board is read through ORBIT's read_messages, live: a post made earlier in the round counts."""
    if config.notice == "none" or not team:
        return None
    try:
        # a record of who came or went is no post to the notice: it neither counts nor moves what the agent was told
        posts = [m for m in read_messages(BOARD_ID, agent_id=agent) if m.sender != agent and not is_record(m)]
    except (PermissionError, RuntimeError, ValueError):
        return None
    top = max((m.sequence for m in posts), default=0)
    if top <= seen:
        return None
    shown = [m for m in posts if not (skip_system and m.sender == SYSTEM_SENDER)]
    fresh = [m for m in shown if m.sequence > seen]
    if not fresh:
        return None, top
    counts: dict[str, tuple[int, int]] = {}
    for sender in sorted({m.sender for m in fresh}):
        mine = [m for m in shown if m.sender == sender]
        counts[sender] = (sum(1 for m in mine if m.sequence > seen), len(mine))
    named = {m.sequence: mentions(str(m.content), agent) for m in fresh}
    chosen = sorted(fresh, key=lambda m: (not named[m.sequence], -m.sequence))[:PREVIEW_POSTS]
    previews = [(m.sender, str(m.content), named[m.sequence]) for m in sorted(chosen, key=lambda m: m.sequence)]
    return notice_text(config.notice, counts, previews, first=seen == 0), top


def seen_of(st: TurnState, agent: str, messages: list) -> int:
    """The highest board sequence of others the agent was told of: TurnState.notice_seen, or the `seen` of a round's
    message's board part its conversation holds, should that be higher (a message ORBIT committed and settle has not
    recorded yet)."""
    held = (((m.metadata or {}).get(NOTICE_TAG) or {}).get("seen") or 0 for m in messages
            if isinstance(m, ChatMessageUser))
    return max([st.notice_seen.get(agent, 0), *held])


def with_paragraph(message, text: str):
    """A copy of the message with text as its last paragraph, for the provider: in a tool result's error message when
    it holds one (what the provider receives in place of the content), else in its content, a list's last text or a
    text of its own, one blank line after the text's last line. The copy keeps the message's role, id and tool call id,
    so a tool result stays a tool result."""
    def joined(before: str) -> str:
        return f"{before.rstrip(chr(10))}\n\n{text}" if before.strip() else text

    if isinstance(message, ChatMessageTool) and message.error is not None:
        return message.model_copy(update={"error": replace(message.error, message=joined(message.error.message))})
    content = message.content
    if isinstance(content, str):
        return message.model_copy(update={"content": joined(content)})
    items = list(content)
    if items and isinstance(items[-1], ContentText):
        items[-1] = items[-1].model_copy(update={"text": joined(items[-1].text)})
    else:
        items.append(ContentText(text=text))
    return message.model_copy(update={"content": items})


def settle(messages: list) -> None:
    """Record each round's message the conversation now holds, once: what TurnRulesDefense.apply_pre_turn left
    pending until ORBIT committed it (Live.pending: its parts' info events, the board part's seen mark and its
    TurnState.added entry). A message ORBIT rejected never comes back, so its records are never written: the next
    round's message holds what it held."""
    pending = live_record().pending
    for message in messages:
        for record in pending.pop(message.id, []) if pending else []:
            record(message.id)


def on_team(st: TurnState, config: SwarmConfig) -> bool:
    """Whether the agents share a board: more than one agent, or the config's board before the store names any."""
    return len(st.agents) > 1 if st.agents else config.board


def mark_seen(agent: str, top: int) -> None:
    st = store_as(TurnState)
    st.notice_seen = {**st.notice_seen, agent: max(top, st.notice_seen.get(agent, 0))}


class TurnRulesFilter(ModelFilter):
    """Shapes each provider request to the turn rules; see the module docstring."""

    name = TURN_RULES_NAME
    priority = 90       # after prompt filters (10)

    def __init__(self, config: SwarmConfig) -> None:
        self.config = config
        self.prompts = load_prompts()

    def _state(self, agent: str) -> tuple[TurnState, int, bool]:
        """(state, the agent's model calls before this one, team): team is whether the agent is on a board. Counts
        this call (TurnState.model_calls)."""
        st = store_as(TurnState)
        n = st.model_calls.get(agent, 0)
        st.model_calls = bump(st.model_calls, agent)
        return st, n, on_team(st, self.config)

    def _continue(self, messages: list) -> tuple[str, str] | None:
        """The continue prompt when the input would end with a model turn, which Gemini rejects."""
        last = next((m for m in reversed(messages) if m.role != "system"), None)
        if last is not None and last.role == "assistant":
            return "continue", self.prompts["continue"]
        return None

    def _notice(self, st: TurnState, agent: str, team: bool, durable: list) -> str | None:
        """The board notice of a call inside a round (board_news), marked seen. At a round's start the round's message
        holds it (TurnRulesDefense.boundary_notice), so the round's first call finds nothing new unless a post came
        in between."""
        news = board_news(self.config, agent, team, seen_of(st, agent, durable))
        if news is None or news[0] is None:
            return None
        text, top = news
        mark_seen(agent, top)
        return text

    def _restored(self, st: TurnState, agent: str, messages: list) -> list:
        """The input with everything the filter added on earlier calls back in place: each notice as the last
        paragraph of the message it went into, each text of its own after the message it followed. A round's board
        part is in the conversation already."""
        after: dict[str, list] = {}
        into: dict[str, list[str]] = {}
        for entry in st.added.get(agent, []):
            if entry.get("boundary"):
                continue
            if "into" in entry:
                into.setdefault(entry["into"], []).append(entry["text"])
                continue
            tag = {k: entry[k] for k in ("kind", "activation", "call", "round") if k in entry}
            after.setdefault(entry["after"], []).append(ChatMessageUser(content=entry["text"],
                                                                        metadata={TURN_RULES_TAG: tag}))
        out = []
        for message in messages:
            for text in into.get(message.id, []):
                message = with_paragraph(message, text)
            out += [message, *after.get(message.id, [])]
        return out

    def _system(self, messages: list, agent: str, n: int) -> list:
        """The input with a copy of its system message without the assistant paragraph (provider_system_text),
        and without the message when nothing else is left. Recorded at the agent's first model call (n is its model
        calls before this one)."""
        first = messages[0] if messages else None
        if first is None or first.role != "system" or not isinstance(first.content, str):
            return messages
        text = provider_system_text(first.content)
        if text == first.content:
            return messages
        if n == 0:
            transcript().info({TURN_RULES_TAG: {"kind": "system_prompt", "activation": 1, "agent": agent}})
        return [*([first.model_copy(update={"content": text})] if text else []), *messages[1:]]

    async def on_input(self, messages, tools, tool_choice, config, ctx):
        agent = ctx.agent_name
        st, n, team = self._state(agent)
        durable = self._system(list(messages), agent, n)
        settle(durable)
        messages = self._restored(st, agent, durable)
        offered = list(tools)
        tools = [t for t in offered if t.name not in CHANNEL_TOOLS]
        withheld = sorted({t.name for t in offered} - {t.name for t in tools})
        activation = st.activations.get(agent, 0) + 1
        if withheld:
            transcript().info({TURN_RULES_TAG: {"kind": "withheld_tools", "activation": activation, "call": n + 1,
                                                "agent": agent, "tools": withheld}})
        notice = self._notice(st, agent, team, durable)
        if notice is not None:
            messages = self._add(st, agent, messages, durable, "notice", notice, activation, n)
        added = self._continue(messages)
        if added is not None:
            messages = self._add(st, agent, messages, durable, *added, activation, n)
        return messages, tools, tool_choice, config

    def _add(self, st: TurnState, agent: str, messages: list, durable: list, kind: str, text: str,
             activation: int, n: int) -> list:
        """The input with the text added, kept in TurnState.added and written as an info event. A notice becomes the
        last paragraph of the input's last message when that is the conversation's own last message, a tool result
        or a user message (`into` its id); any other text, and a notice after a model turn or after a text of the
        filter's, is a user message of its own after the conversation's last message (`after`)."""
        tag = {"kind": kind, "activation": activation, "call": n + 1, "round": current_round()}
        last = messages[-1] if messages else None
        if kind == "notice" and last is not None and durable and last.id == durable[-1].id \
                and last.role in ("tool", "user"):
            place = {"into": last.id}
            messages = [*messages[:-1], with_paragraph(last, text)]
        else:
            place = {"after": durable[-1].id}
            messages = [*messages, ChatMessageUser(content=text, metadata={TURN_RULES_TAG: tag})]
        st.added = {**st.added, agent: [*st.added.get(agent, []), {**place, "text": text, **tag}]}
        transcript().info({TURN_RULES_TAG: {**tag, "agent": agent, "content": text, **place}})
        return messages

    async def on_output(self, output, ctx):
        return output


class TurnRulesDefense(DefenseBase):
    """Passive defense that carries TurnRulesFilter and appends each round's message. It allows every message and
    tool call."""

    def __init__(self, config: DefenseConfig) -> None:
        super().__init__(config)
        self.rules = SwarmConfig.model_validate(config.config)

    def get_model_filters(self) -> list[ModelFilter]:
        return [TurnRulesFilter(self.rules)]

    def get_tool_wrappers(self, agent_names: list[str]) -> dict:
        """On every agent's tools: shown_as_submit, then the round's gate and clock (_turn_rounds.counted). ORBIT
        collects the wrappers of every defense, whatever its target (orbit/solvers/orchestrator.py
        _build_attacks_and_defenses)."""
        def wrapper(agent: str):
            gate = counted(agent)
            return lambda tool: gate(shown_as_submit(tool))

        return {agent: wrapper(agent) for agent in agent_names}

    async def apply_pre_deployment(self, agents, state) -> None:
        return None

    async def apply_pre_turn(self, state, turn, agent_states=None) -> None:
        """The round's message (_turn_messages): the round's line (_turn_rounds.open_round), then, after a read of the
        team's head (_turn_pushes.look), the presence lines due to the activated member (_turn_presence.presence_part)
        and what changed on the board since it last looked (boundary_notice), as one user message appended to its
        editable state. Each part's info events are written once the message holds it. ORBIT's swarm executor calls
        this once per activation, at the member's defense boundary before the round's first model call, with that
        member alone, and commits the edit into its conversation. A call with any other number of states (another
        executor) changes nothing."""
        if agent_states is None or len(agent_states) != 1:
            return None
        [(name, conversation)] = agent_states.items()
        line = open_round(name, conversation)
        await look()
        presence = presence_part(name, conversation)
        board, skipped = self.boundary_notice(name, conversation, presence)
        made = round_message([line, presence, board])
        if made is None:
            return None
        message, parts = made
        conversation.messages.append(message)
        records = [part.record for part in parts] + ([skipped] if skipped else [])
        if LIVE.get() is None:              # outside a run set up by rules_setup: nothing commits later
            for record in records:
                record(message.id)
        else:
            live_record().pending[message.id] = records
        return None

    def boundary_notice(self, agent: str, conversation, presence: Part | None = None) \
            -> tuple[Part | None, Callable[[str], None] | None]:
        """The round's message's board part: the notice of the posts by others since the agent was last told
        (board_news, seen_of), "Posts on the board: 1 by a02. ..." while it has been told of none and "The board
        changed: a02 1 new of 2. ..." later, tagged NOTICE_TAG {kind, activation, call, round, seen, and first while it
        had been told of none}. When the
        presence part tells of the team's hand-in or of its hold, the system's post of it is left out, so the message
        announces it once. Its record, run once ORBIT has committed the message (settle), marks the posts seen, keeps
        it in TurnState.added and writes the "notice" info event (TURN_RULES_TAG, with boundary true: the
        conversation holds it, so no reader places it as a provider-only message). (None, a record that marks the
        posts seen) when the system's posts were all that was new; (None, None) outside a planned step and with
        nothing new."""
        round_number = current_round()
        if round_number is None:
            return None, None
        st = store_as(TurnState)
        told = presence is not None and any(e["kind"] == "held" or (e["kind"] == "exited" and e["why"] == "submit")
                                            for e in presence.tag["events"])
        seen = seen_of(st, agent, conversation.messages)
        news = board_news(self.rules, agent, on_team(st, self.rules), seen, skip_system=told)
        if news is None:
            return None, None
        text, top = news
        if text is None:
            return None, lambda _message: mark_seen(agent, top)
        tag = {"kind": "notice", "activation": st.activations.get(agent, 0) + 1,
               "call": st.model_calls.get(agent, 0) + 1, "round": round_number, "seen": top,
               **({"first": True} if seen == 0 else {})}

        def record(message: str) -> None:
            mark_seen(agent, top)
            rules = store_as(TurnState)
            entry = {"in": message, "boundary": True, "text": text,
                     **{k: tag[k] for k in ("kind", "activation", "call", "round")}}
            rules.added = {**rules.added, agent: [*rules.added.get(agent, []), entry]}
            transcript().info({TURN_RULES_TAG: {**tag, "agent": agent, "content": text, "boundary": True}})

        return Part(NOTICE_TAG, tag, text, record), None

    async def monitor_message(self, message, agent_name, turn) -> DefenseVerdict:
        return DefenseVerdict(allow=True, reason="passive_turn_rules")

    async def on_tool_call(self, tool_name, tool_args, agent_name) -> DefenseVerdict:
        return DefenseVerdict(allow=True, reason="passive_turn_rules")


try:
    register_defense(TURN_RULES_TYPE, TurnRulesDefense)
except ValueError:
    pass            # already registered in this process


def turn_rules_defense(config: SwarmConfig) -> DefenseConfig:
    """The DefenseConfig that installs the filter on every agent.

    A target naming no agent builds no DefenseBoundary, while ORBIT collects the model filters of every defense for
    every agent (orbit/solvers/orchestrator.py _build_attacks_and_defenses).
    """
    return DefenseConfig(name=TURN_RULES_NAME, defense_type=TURN_RULES_TYPE, target_agents=[FILTER_ONLY_TARGET],
                         config=config.model_dump(mode="json"))
