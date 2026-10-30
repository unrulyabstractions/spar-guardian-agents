"""The model filter of the turn rules and the passive defense that carries it.

TurnRulesFilter changes what the provider receives and never the durable conversation. It removes ORBIT's assistant
paragraph from the system message (provider_system_text), withholds ORBIT's channel tools, opens a call with the board
notice when the board changed, and appends the continue prompt when the input would end with a model turn. Everything
it adds stays in place on every later call (TurnState.added), so the history a provider receives only grows: a provider
that binds a replayed thinking block to the turns before it refuses a history that lost a message, and a changed prefix
misses every prompt cache. The defense that carries it also shows team_submit to the model as submit (shown_as_submit).
turn_rules_defense builds the DefenseConfig that installs both on every agent.

Inspect writes the ModelEvent before ORBIT's model filters run (FilteredModelAPI sits under Inspect's
Model.generate), so ModelEvent.input keeps ORBIT's paragraph and lacks what the filter adds, and ModelEvent.tools
still lists the channel tools. The filter therefore writes info events tagged TURN_RULES_TAG: "system_prompt" at an
agent's first call, when its system message lost ORBIT's paragraph; "withheld_tools" naming the tools the provider did
not receive; and "notice" and "continue" carrying each added message, which the transcript builder reads.

    experiment["defenses"] = [turn_rules_defense(config).model_dump(mode="json")]
"""
from __future__ import annotations

from inspect_ai.log import transcript
from inspect_ai.model import ChatMessageUser
from inspect_ai.tool import ToolDef
from inspect_ai.util import store_as
from orbit.agents.turn_react import DEFAULT_ASSISTANT_PROMPT_TURN_REACT as ORBIT_ASSISTANT_PROMPT
from orbit.communication.runtime import read_messages
from orbit.configs.defense import DefenseConfig
from orbit.defenses.base import DefenseBase, DefenseVerdict
from orbit.defenses.model_filter import ModelFilter
from orbit.defenses.registry import register_defense

from src.configuration.settings import load_prompts
from src.configuration.swarm_config import SwarmConfig
from src.swarm.turn_rules.turn_state import BOARD_ID, CHANNEL_TOOLS, SUBMIT, TEAM_SUBMIT, TURN_RULES_TAG, TurnState

TURN_RULES_TYPE = "swarm.turn_rules.v1"
TURN_RULES_NAME = "turn_rules"
# A target that names no agent builds no DefenseBoundary, so the filter runs without a check event per message.
FILTER_ONLY_TARGET = "__model_filter_only__"


def provider_system_text(text: str) -> str:
    """The system text the provider receives: the stored system message without ORBIT's assistant paragraph
    (DEFAULT_ASSISTANT_PROMPT_TURN_REACT, which turn_react appends after a blank line) and that blank line. "" when
    the paragraph was the whole message; text without the paragraph comes back unchanged. The filter applies it to
    every call; a reader of a run whose filter logged a "system_prompt" info event applies it to show what the
    provider saw."""
    return text.replace(f"\n\n{ORBIT_ASSISTANT_PROMPT}", "").replace(ORBIT_ASSISTANT_PROMPT, "")


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


def notice_text(kind: str, counts: dict[str, tuple[int, int]] | None = None) -> str | None:
    """The board notice (prompts.yaml notice) for notice=kind, or None for "none".

    counts maps each sender with new posts to (new, total), in the order to print."""
    if kind == "none":
        return None
    notice = load_prompts()["notice"]
    if kind == "changed":
        return notice["changed"]
    lines = "; ".join(notice["count"].format(sender=sender, new=new, total=total)
                      for sender, (new, total) in (counts or {}).items())
    return notice["counts"].format(counts=lines)


class TurnRulesFilter(ModelFilter):
    """Shapes each provider request to the turn rules; see the module docstring."""

    name = TURN_RULES_NAME
    priority = 90       # after prompt filters (10)

    def __init__(self, config: SwarmConfig) -> None:
        self.config = config
        self.prompts = load_prompts()

    def _state(self, agent: str) -> tuple[TurnState, int, bool]:
        """(state, completed activations, team): team is whether the agent is on a board."""
        st = store_as(TurnState)
        n = st.activations.get(agent, 0)
        team = len(st.agents) > 1 if st.agents else self.config.board
        return st, n, team

    def _continue(self, messages: list) -> tuple[str, str] | None:
        """The continue prompt when the input would end with a model turn, which Gemini rejects."""
        last = next((m for m in reversed(messages) if m.role != "system"), None)
        if last is not None and last.role == "assistant":
            return "continue", self.prompts["continue"]
        return None

    def _notice(self, st: TurnState, agent: str, team: bool) -> tuple[str, str] | None:
        """The board notice when the board holds posts by others above the sequence the agent saw at its last call.

        counts gives each such sender's new posts (above that sequence) and total posts. The board is read through
        ORBIT's read_messages, so a concurrent round sees its snapshot."""
        if self.config.notice == "none" or not team:
            return None
        try:
            posts = [m for m in read_messages(BOARD_ID, agent_id=agent) if m.sender != agent]
        except (PermissionError, RuntimeError, ValueError):
            return None
        seen = st.notice_seen.get(agent, 0)
        top = max((m.sequence for m in posts), default=0)
        if top <= seen:
            return None
        st.notice_seen = {**st.notice_seen, agent: top}
        counts: dict[str, tuple[int, int]] = {}
        for sender in sorted({m.sender for m in posts if m.sequence > seen}):
            mine = [m for m in posts if m.sender == sender]
            counts[sender] = (sum(1 for m in mine if m.sequence > seen), len(mine))
        text = notice_text(self.config.notice, counts)
        return ("notice", text) if text else None

    def _restored(self, st: TurnState, agent: str, messages: list) -> list:
        """The input with everything the filter added on earlier calls back after the message it followed."""
        added: dict[str, list] = {}
        for entry in st.added.get(agent, []):
            tag = {"kind": entry["kind"], "activation": entry["activation"]}
            added.setdefault(entry["after"], []).append(ChatMessageUser(content=entry["text"],
                                                                        metadata={TURN_RULES_TAG: tag}))
        return [m for message in messages for m in (message, *added.get(message.id, []))]

    def _system(self, messages: list, agent: str, n: int) -> list:
        """The input with a copy of its system message without ORBIT's assistant paragraph (provider_system_text),
        and without the message when nothing else is left. Recorded at the agent's first call."""
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
        messages = self._restored(st, agent, durable)
        offered = list(tools)
        tools = [t for t in offered if t.name not in CHANNEL_TOOLS]
        withheld = sorted({t.name for t in offered} - {t.name for t in tools})
        if withheld:
            transcript().info({TURN_RULES_TAG: {"kind": "withheld_tools", "activation": n + 1, "agent": agent,
                                                "tools": withheld}})
        for added in (self._notice(st, agent, team), self._continue(messages)):
            if added is None:
                continue
            kind, text = added
            tag = {"kind": kind, "activation": n + 1}
            messages = [*messages, ChatMessageUser(content=text, metadata={TURN_RULES_TAG: tag})]
            entry = {"after": durable[-1].id, "text": text, **tag}
            st.added = {**st.added, agent: [*st.added.get(agent, []), entry]}
            transcript().info({TURN_RULES_TAG: {**tag, "agent": agent, "content": text}})
        return messages, tools, tool_choice, config

    async def on_output(self, output, ctx):
        return output


class TurnRulesDefense(DefenseBase):
    """Passive defense that carries TurnRulesFilter. It allows every message and tool call."""

    def __init__(self, config: DefenseConfig) -> None:
        super().__init__(config)
        self.rules = SwarmConfig.model_validate(config.config)

    def get_model_filters(self) -> list[ModelFilter]:
        return [TurnRulesFilter(self.rules)]

    def get_tool_wrappers(self, agent_names: list[str]) -> dict:
        """shown_as_submit on every agent's tools. ORBIT collects the wrappers of every defense, whatever its target
        (orbit/solvers/orchestrator.py _build_attacks_and_defenses)."""
        return dict.fromkeys(agent_names, shown_as_submit)

    async def apply_pre_deployment(self, agents, state) -> None:
        return None

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
