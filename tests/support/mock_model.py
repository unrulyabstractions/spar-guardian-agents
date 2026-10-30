"""Scripted outputs for Inspect's mock provider, the mock judge's verdict and a reader of a sample's info events.

    script = {"a01": [call("shell", {"command": "ls"}, "c1"), call("stop_working", {}, "c2")]}
    model = get_model(MODEL, custom_outputs=scripted(script, recorder))
"""
from __future__ import annotations

import json
import typing
import uuid
from collections.abc import Callable

from inspect_ai.model import ModelOutput
from inspect_ai.tool import ToolCall
from orbit.communication.runtime import current_identity

MODEL = "mockllm/model"


def call(name: str, args: dict, call_id: str) -> ModelOutput:
    """One model turn that calls one tool."""
    return ModelOutput.for_tool_call(MODEL, name, args, tool_call_id=call_id)


def calls(*requests: tuple[str, dict], call_id: str | None = None) -> ModelOutput:
    """One model turn with several (tool, arguments) calls, executed as a batch; call_id starts their ids."""
    start = call_id or f"call-{uuid.uuid4()}"
    out = ModelOutput.for_tool_call(MODEL, *requests[0])
    out.choices[0].message.tool_calls = [ToolCall(id=f"{start}-{i}", function=name, arguments=args)
                                         for i, (name, args) in enumerate(requests)]
    return out


def text(content: str) -> ModelOutput:
    """One model turn of plain text."""
    return ModelOutput.from_content(MODEL, content)


def scripted(script: dict, recorder: list | None = None) -> Callable:
    """Each agent follows its own script by its count of model turns; an entry may be a function of the messages.
    The agent is named by its team system prompt ("You are a01.") or by ORBIT's identity; a lone agent is the one
    script. A recorder gets one entry per call: agent, step, the tools offered, the last message, the system texts and
    the messages."""
    def outputs(messages, tools, _tool_choice, _config) -> ModelOutput:
        system = next((m.text for m in messages if m.role == "system" and m.text.startswith("You are ")), "")
        who = system.split()[2].rstrip(".,") if system else "?"
        if who not in script and current_identity() is not None:
            who = current_identity().agent_id
        if who not in script and len(script) == 1:
            who = next(iter(script))
        assert who in script, f"cannot tell which agent is asking from {system[:60]!r}"
        step = sum(1 for m in messages if m.role == "assistant")
        if recorder is not None:
            recorder.append({"agent": who, "step": step, "tools": sorted(t.name for t in tools),
                             "last": messages[-1].text, "system": system,
                             "system_texts": [m.text for m in messages if m.role == "system"], "messages": messages})
        plan = script[who]
        out = plan[min(step, len(plan) - 1)]
        return out(messages) if callable(out) else out
    return outputs


def mock_value(annotation):
    """A Verdict field's value in the mock judge's answer: None for an optional field, "smoke" for a string, False for
    a flag, 0 for a count."""
    if type(None) in typing.get_args(annotation):
        return None
    return {str: "smoke", bool: False, int: 0}[annotation]


def mock_verdict(spec) -> dict:
    """The task's Verdict with every flag false and the summary "smoke: mock judge"."""
    verdict = {name: mock_value(f.annotation) for name, f in spec.verdict.model_fields.items()}
    verdict["summary"] = "smoke: mock judge"
    return verdict


def honest_judge(spec) -> Callable:
    """The mock judge's outputs: mock_verdict(spec) as JSON on every call."""
    verdict = json.dumps(mock_verdict(spec))

    def outputs(messages, tools, tool_choice, config) -> ModelOutput:
        return text(verdict)
    return outputs


def tagged_infos(sample, tag: str) -> list[dict]:
    """The data of the sample's info events under `tag`, in order."""
    return [e.data[tag] for e in sample.events if e.event == "info" and isinstance(e.data, dict) and tag in e.data]
