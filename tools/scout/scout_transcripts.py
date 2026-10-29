"""A Scout transcripts database of one run: one inspect_scout Transcript per agent, built from its ATIF transcript.

Each ATIF step becomes chat messages: a system step a ChatMessageSystem, a user step a ChatMessageUser, and an agent
step a ChatMessageAssistant with its tool calls followed by one ChatMessageTool per tool result. Every message carries
the id "<agent>-<step index>-<part>" and the metadata {step, activation, schedule_step, round}, so a finding that
names a message can be traced back to the ATIF step and the round it happened in.
"""
from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path

from inspect_ai._util.content import ContentReasoning, ContentText
from inspect_ai.model import (
    ChatMessage,
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageTool,
    ChatMessageUser,
)
from inspect_ai.tool import ToolCall
from inspect_scout import Transcript, message_as_str, message_numbering, transcripts_db

from tools.run_reader import Run, Step

SOURCE_TYPE = "spar_run"
REDACTED_REASONING = "[redacted reasoning]"
JUDGE_SCALARS = (bool, int, float, str)


@dataclass(frozen=True)
class MessageOrigin:
    """Where one chat message came from: its ATIF step and that step's round."""

    message_id: str
    step: int
    round: int | None
    source: str
    text: str  # the message as llm_scanner renders it, for locating a finding's quote


def episode_of(run: Run, agent: str) -> str:
    """The agent's episode id, such as "riemann#1@a02"; the run name and agent when episodes.jsonl lacks it."""
    return (run.episodes.get(agent) or {}).get("episode") or f"{run.name}@{agent}"


def step_metadata(step: Step) -> dict:
    """The metadata every message of a step carries."""
    return {"step": step.index, "activation": step.activation, "schedule_step": step.schedule_step,
            "round": step.round}


def assistant_content(step: Step) -> str | list:
    """The assistant message's content: its text, with the reasoning first when the transcript kept it."""
    if step.reasoning and step.reasoning.strip() != REDACTED_REASONING:
        return [ContentReasoning(reasoning=step.reasoning), ContentText(text=step.text)]
    return step.text


def step_messages(agent: str, step: Step, model: str | None) -> list[ChatMessage]:
    """The chat messages of one ATIF step."""
    prefix, metadata = f"{agent}-{step.index}", step_metadata(step)
    if step.source == "system":
        return [ChatMessageSystem(id=f"{prefix}-0", content=step.text, metadata=metadata)]
    if step.source == "user":
        return [ChatMessageUser(id=f"{prefix}-0", content=step.text, metadata=metadata)]
    calls = [ToolCall(id=call_id or f"{prefix}-call{position}", function=name, arguments=arguments)
             for position, (call_id, (name, arguments)) in enumerate(zip(step.call_ids, step.tool_calls))]
    messages: list[ChatMessage] = [ChatMessageAssistant(id=f"{prefix}-0", content=assistant_content(step),
                                                        tool_calls=calls or None, model=model, metadata=metadata)]
    functions = {call.id: call.function for call in calls}
    for position, (result_id, content) in enumerate(zip(step.result_ids, step.observations), 1):
        messages.append(ChatMessageTool(id=f"{prefix}-{position}", content=content, tool_call_id=result_id or None,
                                        function=functions.get(result_id), metadata=metadata))
    return messages


def agent_messages(run: Run, agent: str) -> list[ChatMessage]:
    """Every chat message of the agent's transcript, in ATIF order."""
    model = (run.transcripts.get(agent) or {}).get("model") or run.model
    model = model if isinstance(model, str) else run.model
    return [message for step in run.steps(agent) for message in step_messages(agent, step, model)]


def judge_metadata(row: dict) -> dict:
    """The judge row's scalar fields (its verdict flags, summary and evidence) as judge_<field>, without nested blocks.

    The database turns metadata keys into columns, so the fields are flat and prefixed.
    """
    return {f"judge_{key}": value for key, value in row.items() if value is None or isinstance(value, JUDGE_SCALARS)}


def agent_transcript(run: Run, agent: str) -> Transcript:
    """The agent's Scout transcript: its messages plus the run, role, domain and judge verdict as metadata.

    The agent's name is the transcript's agent field; the database reserves "agent" as a column, so the metadata
    does not repeat it.
    """
    messages = agent_messages(run, agent)
    judge = run.judge.get(agent) or {}
    metadata = {
        "run": run.name,
        "role": run.roles.get(agent, "worker"),
        "domain": run.domain,
        "task": run.task,
        "cheated": run.cheated.get(agent),
        "holds": ", ".join(str(item) for item in run.exploit.get("holds", {}).get(agent, [])),
        **judge_metadata(judge),
    }
    return Transcript(
        transcript_id=episode_of(run, agent),
        source_type=SOURCE_TYPE,
        source_id=run.name,
        source_uri=str(run.directory),
        task_set=run.domain,
        task_id=str(judge.get("id") or run.task),
        agent=agent,
        model=run.model,
        message_count=len(messages),
        metadata=metadata,
        messages=messages,
    )


def run_transcripts(run: Run) -> list[Transcript]:
    """One transcript per agent that has an ATIF transcript, in the run's agent order."""
    agents = [agent for agent in run.agents if agent in run.transcripts]
    agents += [agent for agent in run.transcripts if agent not in agents]
    return [agent_transcript(run, agent) for agent in agents]


async def insert_transcripts(location: Path, transcripts: list[Transcript]) -> None:
    """Insert the transcripts into the parquet database at location."""
    async with transcripts_db(str(location)) as database:
        await database.insert(transcripts)


def build_database(run: Run, location: Path) -> list[Transcript]:
    """Rebuild the run's transcripts database at location (it is derived data, so an old one is replaced)."""
    if location.exists():
        shutil.rmtree(location)
    location.mkdir(parents=True)
    transcripts = run_transcripts(run)
    asyncio.run(insert_transcripts(location, transcripts))
    return transcripts


def origin_of_message(message: ChatMessage) -> MessageOrigin:
    """The origin of one message, from the metadata its step gave it."""
    metadata = message.metadata or {}
    return MessageOrigin(message_id=message.id, step=metadata.get("step"), round=metadata.get("round"),
                         source=message.role, text=message_as_str(message) or "")


def message_origins(messages: list[ChatMessage]) -> dict[str, MessageOrigin]:
    """Each message's origin by the label an LLM scanner gives it ("M1", "M2", ...), in transcript order.

    The labels come from inspect_scout's own message_numbering with the default preprocessor (system messages
    excluded), the same numbering llm_scanner shows the model.
    """
    render, _references, label_for_id = message_numbering(label_for_id=True)
    asyncio.run(render(messages))
    labelled = [(label_for_id(message.id), message) for message in messages]
    return {label: origin_of_message(message) for label, message in labelled if label is not None}


def origins_by_id(messages: list[ChatMessage]) -> dict[str, MessageOrigin]:
    """Each message's origin by its message id."""
    return {message.id: origin_of_message(message) for message in messages}
