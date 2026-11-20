"""The transcript viewer's tool definitions: each agent's tools as its first model call sent them to the provider (the
event's own list only when no request was recorded), a defense's own calls left out, and a change of tools between
calls flagged. No server, no Docker."""
from __future__ import annotations

from inspect_ai.event import ModelEvent, SpanBeginEvent
from inspect_ai.log import EvalDataset, EvalLog, EvalSample, EvalSpec, write_eval_log
from inspect_ai.model import GenerateConfig, ModelCall, ModelOutput
from inspect_ai.tool import ToolInfo, ToolParam, ToolParams

from src.ui.transcript_viewer import agent_tools, sent_tool


def tool(name: str) -> ToolInfo:
    return ToolInfo(name=name, description=f"{name} it.",
                    parameters=ToolParams(properties={"arg": ToolParam(type="string", description="the arg")},
                                          required=["arg"]))


def call(span: str, *names: str) -> ModelEvent:
    return ModelEvent(span_id=span, model="mockllm/model", input=[], tools=[tool(n) for n in names],
                      tool_choice="auto", config=GenerateConfig(), output=ModelOutput())


def write_log(path, events) -> str:
    spec = EvalSpec(created="2026-11-20T00:00:00+00:00", task="swe", dataset=EvalDataset(), model="mockllm/model",
                    config={})
    write_eval_log(EvalLog(eval=spec, samples=[EvalSample(id="t", epoch=1, input="", target="", events=events)]),
                   str(path))
    return path


def test_each_agent_gets_its_first_calls_tools_and_a_defenses_calls_are_left_out(tmp_path):
    events = [SpanBeginEvent(id="a", name="k7x6z", type="agent"),
              SpanBeginEvent(id="b", name="n8n6n", type="agent"),
              SpanBeginEvent(id="d", name="monitor", type="orbit.defense", parent_id="a"),
              call("d", "judge_it"),
              call("a", "shell", "board_post"),
              call("b", "shell", "agent_actions"),
              call("a", "shell", "board_post")]
    got = agent_tools(write_log(tmp_path / "x.eval", events))
    assert set(got) == {"k7x6z", "n8n6n"}
    assert [t["name"] for t in got["k7x6z"]["tools"]] == ["shell", "board_post"]
    assert [t["name"] for t in got["n8n6n"]["tools"]] == ["shell", "agent_actions"]
    shell = got["k7x6z"]["tools"][0]
    assert shell["description"] == "shell it."
    assert shell["parameters"]["properties"]["arg"] == {"type": "string", "description": "the arg"}
    assert shell["parameters"]["required"] == ["arg"]
    assert got["k7x6z"]["tool_choice"] == "auto"
    assert got["k7x6z"]["same_in_every_call"] is True


def test_a_change_of_tools_between_calls_is_flagged(tmp_path):
    events = [SpanBeginEvent(id="a", name="k7x6z", type="agent"), call("a", "shell"), call("a", "shell", "submit")]
    got = agent_tools(write_log(tmp_path / "x.eval", events))["k7x6z"]
    assert [t["name"] for t in got["tools"]] == ["shell"]
    assert got["same_in_every_call"] is False


def test_the_tools_sent_to_the_provider_win_over_the_events_list(tmp_path):
    sent = {"tools": [{"type": "function", "function": {"name": "shell", "description": "Run it.",
                                                         "parameters": {"type": "object", "properties": {}}}}]}
    e = call("a", "shell", "channel_post")
    e.call = ModelCall(request=sent, response={})
    got = agent_tools(write_log(tmp_path / "x.eval", [SpanBeginEvent(id="a", name="k7x6z", type="agent"), e]))["k7x6z"]
    empty = {"type": "object", "properties": {}}
    assert got["tools"] == [{"name": "shell", "description": "Run it.", "parameters": empty}]
    assert got["from_request"] is True


def test_a_sent_tool_reads_the_chat_responses_and_anthropic_formats():
    schema = {"type": "object"}
    want = {"name": "x", "description": "d", "parameters": schema}
    assert sent_tool({"type": "function", "function": {"name": "x", "description": "d", "parameters": schema}}) == want
    assert sent_tool({"type": "function", "name": "x", "description": "d", "parameters": schema}) == want
    assert sent_tool({"name": "x", "description": "d", "input_schema": schema}) == want
