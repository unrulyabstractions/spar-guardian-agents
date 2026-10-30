"""The model filter of the turn rules and the passive defense that carries it.

TurnRulesFilter changes what the provider receives and never the durable conversation. It withholds ORBIT's
channel tools, opens a call with the board notice when the board changed, and appends the continue prompt when the
input would end with a model turn. turn_rules_defense builds the DefenseConfig that installs it on every agent.

Inspect writes the ModelEvent before ORBIT's model filters run (FilteredModelAPI sits under Inspect's
Model.generate), so what the filter adds is absent from ModelEvent.input, and ModelEvent.tools still lists the channel
tools. Each call therefore writes info events tagged TURN_RULES_TAG: "withheld_tools" names the tools the provider did
not receive, and "notice" and "continue" carry each added message, which the transcript builder reads.

    experiment["defenses"] = [turn_rules_defense(config).model_dump(mode="json")]
"""
from __future__ import annotations

from inspect_ai.log import transcript
from inspect_ai.model import ChatMessageUser
from inspect_ai.util import store_as
from orbit.agents.turn_react import DEFAULT_ASSISTANT_PROMPT_TURN_REACT as ORBIT_ASSISTANT_PROMPT
from orbit.communication.runtime import read_messages
from orbit.configs.defense import DefenseConfig
from orbit.defenses.base import DefenseBase, DefenseVerdict
from orbit.defenses.model_filter import ModelFilter
from orbit.defenses.registry import register_defense

from src.configuration.settings import load_prompts
from src.configuration.swarm_config import SwarmConfig
from src.swarm.turn_rules.turn_state import BOARD_ID, CHANNEL_TOOLS, TURN_RULES_TAG, TurnState

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

    async def on_input(self, messages, tools, tool_choice, config, ctx):
        agent = ctx.agent_name
        st, n, team = self._state(agent)
        offered = list(tools)
        tools = [t for t in offered if t.name not in CHANNEL_TOOLS]
        withheld = sorted({t.name for t in offered} - {t.name for t in tools})
        if withheld:
            transcript().info({TURN_RULES_TAG: {"kind": "withheld_tools", "activation": n + 1, "agent": agent,
                                                "tools": withheld}})
        messages = list(messages)
        for added in (self._notice(st, agent, team), self._continue(messages)):
            if added is None:
                continue
            kind, text = added
            tag = {"kind": kind, "activation": n + 1}
            messages = [*messages, ChatMessageUser(content=text, metadata={TURN_RULES_TAG: tag})]
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
