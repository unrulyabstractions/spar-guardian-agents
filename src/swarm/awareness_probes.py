"""Asking an agent a question after the run, on a copy of its own conversation with tools off: the helpers the belief
questions use (src/swarm/belief_probes.py).

The procedure follows Lucy's scenario.py (`ask`, `flatten` in
submodules/lucy/experiments/orbit_cheatbench/orbitcb/scenario.py at c410183): when a provider refuses a tool history
without tools, the question is asked again on the history written out as text. provider_messages gives each system
message as the provider received it: the turn rules' filter takes the agent loop's assistant paragraph out of every
call (turn_rules.provider_system_text), so the replay does too. Each agent is asked by its own model (agent_model): its
role's model when the run gave the role one (metadata["cheatbench"]["role_models"]), else the eval's model.

Lucy's end-of-episode probes, the scorer end_of_episode_probes, were removed on 2026-11-23; the export still reads the
scores of the stored runs that asked them (src/export/_export_run_blocks.py PROBES_SCORER).
"""
from __future__ import annotations

import json

from inspect_ai.model import (
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageTool,
    ChatMessageUser,
    Model,
    get_model,
)
from inspect_ai.solver import TaskState

from src.models import swarm_model_provider as provider
from src.swarm.turn_rules import provider_system_text


def flatten(messages: list) -> list:
    """The conversation with tool calls and results written out as text."""
    out = []
    for m in messages:
        if isinstance(m, ChatMessageTool):
            out.append(ChatMessageUser(content=f"[result of {m.function}]\n{m.text}"))
        elif isinstance(m, ChatMessageAssistant) and m.tool_calls:
            calls = "\n".join(f"[call {c.function}] {json.dumps(c.arguments)}" for c in m.tool_calls)
            out.append(ChatMessageAssistant(content=f"{m.text}\n{calls}".strip()))
        else:
            out.append(m)
    return out


def provider_messages(messages: list) -> list:
    """The conversation as the provider received it: each system message without the agent loop's assistant
    paragraph."""
    return [m.model_copy(update={"content": provider_system_text(m.text)}) if isinstance(m, ChatMessageSystem) else m
            for m in messages]


def agent_model(state: TaskState, agent: str) -> Model:
    """The model the agent ran: its role's model through the swarm provider, else the eval's model."""
    cb = (state.metadata or {}).get("cheatbench", {})
    chosen = provider.agent_model(cb.get("role_models"), cb.get("roles"), agent)
    return get_model(chosen) if chosen else get_model()


async def ask(messages: list, question: str, model: Model) -> tuple[str, str]:
    """The model's answer to one question, and whether the history went as is ("native") or as text."""
    asked = [*messages, ChatMessageUser(content=question)]
    try:
        out = await model.generate(asked, tools=[])
        return out.completion, "native"
    except Exception:  # noqa: BLE001 - a provider may refuse tool history without tools; ask again as text
        out = await model.generate(flatten(asked), tools=[])
        return out.completion, "flattened"

