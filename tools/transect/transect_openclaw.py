"""One agent's ATIF transcript as OpenClaw telemetry-hal records, the per-agent input Transect can scan.

Transect numbers the turns of one orchestrator lane and refuses an Inspect sample with several top-level agents,
which is how ORBIT logs a team. Each agent's transcript therefore becomes one OpenClaw file of its own: an
`agent.start` with the opening messages and an `agent.end` with the whole thread, under the session key
`agent:<agent>:main:<run>-<agent>`. The agent's k-th step is the k-th model event of its agent span in the eval
log, which gives the turn its timestamps and its token usage. Without the log, or when the counts disagree, the
timestamps are synthetic (one second per activation) and the usage is zero.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from inspect_ai.log import read_eval_log

from tools.run_reader import Run, Step, observation_for

SYNTHETIC_START_MS = 1_700_000_000_000
SYNTHETIC_STEP_MS = 1_000
SYSTEM_PREFIX = "[system prompt]\n"


@dataclass(frozen=True)
class ModelCall:
    """One model event of an agent span: when it ran and what it used."""

    started_ms: int
    completed_ms: int
    model: str
    usage: dict  # OpenClaw usage keys: input, output, cacheRead, cacheWrite, totalTokens
    first_call_id: str | None


def model_calls_by_agent(eval_log: Path) -> dict[str, list[ModelCall]]:
    """Every model event of the log's first sample, grouped by the agent span that holds it, in event order."""
    log = read_eval_log(str(eval_log))
    calls: dict[str, list[ModelCall]] = {}
    for sample in (log.samples or [])[:1]:
        spans = {event.id: event for event in sample.events if event.event == "span_begin"}
        for event in sample.events:
            if event.event != "model":
                continue
            agent = agent_span_name(spans, event.span_id)
            if agent is None:
                continue
            calls.setdefault(agent, []).append(model_call_of(event))
    return calls


def agent_span_name(spans: dict, span_id: str | None) -> str | None:
    """The name of the nearest enclosing span of type agent, or None outside every agent span."""
    while span_id in spans:
        span = spans[span_id]
        if span.type == "agent":
            return span.name
        span_id = span.parent_id
    return None


def model_call_of(event) -> ModelCall:
    """A model event's timing, model and usage in OpenClaw's exclusive-cache keys."""
    usage = event.output.usage if event.output is not None else None
    parts = {
        "input": (usage.input_tokens if usage else 0) or 0,
        "output": (usage.output_tokens if usage else 0) or 0,
        "cacheRead": (usage.input_tokens_cache_read if usage else 0) or 0,
        "cacheWrite": (usage.input_tokens_cache_write if usage else 0) or 0,
    }
    message = event.output.choices[0].message if event.output is not None and event.output.choices else None
    tool_calls = (message.tool_calls or []) if message is not None else []
    started = epoch_ms(event.timestamp)
    return ModelCall(
        started_ms=started,
        completed_ms=epoch_ms(event.completed) if event.completed else started,
        model=str(event.model),
        usage={**parts, "totalTokens": sum(parts.values())},
        first_call_id=tool_calls[0].id if tool_calls else None,
    )


def epoch_ms(moment: datetime) -> int:
    """A datetime as integer epoch milliseconds."""
    return int(moment.timestamp() * 1000)


def aligned_calls(steps: list[Step], calls: list[ModelCall]) -> tuple[list[ModelCall] | None, str]:
    """The model calls to pair one-to-one with the agent steps, or None, and why."""
    if not calls:
        return None, "no model events for this agent in the eval log"
    if len(calls) != len(steps):
        return None, f"{len(steps)} agent steps but {len(calls)} model events"
    for step, call in zip(steps, calls, strict=True):
        if step.call_ids and call.first_call_id and step.call_ids[0] != call.first_call_id:
            return None, f"activation {step.activation}: call id {step.call_ids[0]} != {call.first_call_id}"
    return calls, f"{len(calls)} model events paired with {len(steps)} agent steps"


def synthetic_calls(steps: list[Step], model: str) -> list[ModelCall]:
    """One call per agent step, a second apart, with zero usage."""
    zero = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 0}
    calls = []
    for position, step in enumerate(steps):
        started = SYNTHETIC_START_MS + (position + 1) * SYNTHETIC_STEP_MS
        calls.append(ModelCall(started_ms=started, completed_ms=started + SYNTHETIC_STEP_MS // 2, model=model,
                               usage=dict(zero), first_call_id=step.call_ids[0] if step.call_ids else None))
    return calls


def session_key(run: Run, agent: str) -> str:
    """The OpenClaw session key of one agent: an orchestrator (main) surface named after the run and the agent."""
    slug = run.name.replace("/", "-").replace(":", "-")
    return f"agent:{agent}:main:{slug}-{agent}"


def telemetry_records(run: Run, agent: str, calls: list[ModelCall]) -> list[dict]:
    """The agent's transcript as two telemetry-hal events: agent.start (opening messages) and agent.end (all).

    `calls` holds one ModelCall per agent step, in order. A tool call id that repeats in the transcript (mock models
    reuse "c1") gets the suffix "#<activation>", since the importer keys tool results by call id.
    """
    steps = run.steps(agent)
    starts = [call.started_ms for call in calls]
    seen_ids: set[str] = set()
    messages, opening = [], []
    activation = 0
    for step in steps:
        if step.source == "agent":
            call = calls[activation]
            activation += 1
            ids = [unique_call_id(call_id, step.activation, seen_ids) for call_id in step.call_ids]
            messages.append(assistant_message(agent, step, call, ids))
            messages.extend(tool_results(step, ids, call.completed_ms))
            continue
        upcoming = starts[activation] if activation < len(starts) else (calls[-1].completed_ms + 1 if calls else 0)
        message = user_message(agent, step, upcoming)
        messages.append(message)
        if activation == 0:
            opening.append(message)
    key = session_key(run, agent)
    return [
        {"type": "agent.start", "sessionKey": key, "agentId": agent, "messages": opening},
        {"type": "agent.end", "sessionKey": key, "agentId": agent, "messages": messages},
    ]


def unique_call_id(call_id: str, activation: int | None, seen: set[str]) -> str:
    """The call id, suffixed with its activation and a counter when an earlier call already used it."""
    unique, count = call_id, 0
    while not unique or unique in seen:
        count += 1
        unique = f"{call_id or 'call'}#{activation}-{count}"
    seen.add(unique)
    return unique


def user_message(agent: str, step: Step, timestamp_ms: int) -> dict:
    """A system or user step as an OpenClaw user turn; the system prompt carries a "[system prompt]" prefix."""
    text = SYSTEM_PREFIX + step.text if step.source == "system" else step.text
    return {"role": "user", "idempotencyKey": f"{agent}-{step.source}-{step.index}", "timestamp": timestamp_ms,
            "content": text}


def assistant_message(agent: str, step: Step, call: ModelCall, call_ids: list[str]) -> dict:
    """An agent step as an OpenClaw assistant turn: reasoning, text and tool calls, with the model call's usage."""
    content = []
    if step.reasoning:
        content.append({"type": "thinking", "thinking": step.reasoning})
    if step.text:
        content.append({"type": "text", "text": step.text})
    for call_id, (name, arguments) in zip(call_ids, step.tool_calls, strict=True):
        content.append({"type": "toolCall", "id": call_id, "name": name, "arguments": arguments})
    return {
        "role": "assistant",
        "responseId": f"{agent}-activation-{step.activation}",
        "timestamp": call.started_ms,
        "model": call.model,
        "responseModel": call.model,
        "stopReason": "toolUse" if step.tool_calls else "stop",
        "usage": call.usage,
        "content": content,
    }


def tool_results(step: Step, call_ids: list[str], completed_ms: int) -> list[dict]:
    """The step's tool results as OpenClaw toolResult turns, one per call: the run reader's observation_for matches
    each to its call by source_call_id, else by position, and a call without one gets an empty result."""
    return [{"role": "toolResult", "toolCallId": call_id, "toolName": name, "timestamp": completed_ms,
             "content": observation_for(step, step.call_ids[position], position)}
            for position, ((name, _arguments), call_id) in enumerate(zip(step.tool_calls, call_ids, strict=True))]


def write_telemetry(records: list[dict], path: Path) -> Path:
    """Write the records as JSON lines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records))
    return path
