"""After Google's 400 through OpenRouter, the retry sends the same request with the newest step's tool-call ids renamed.
Proven through Inspect's real request code: a fake OpenRouter answers the first POST with Google's exact 400 and the
second with a completion, and the two bodies it read differ only in the newest step's ids. Other models, other steps
and Inspect's own messages are untouched."""
from __future__ import annotations

import asyncio
import json

import httpx
import openai
import pytest
from inspect_ai.model import ChatMessageAssistant, ChatMessageTool, ChatMessageUser, get_model
from inspect_ai.tool import ToolCall, ToolInfo, ToolParams

from src.models import model_registry as models
from src.models import openrouter_gemini_tool_ids as tool_ids
from src.models import openrouter_google_400_retry as retry

GOOGLE_RAW = json.dumps({"error": {"code": 400, "message": "Request contains an invalid argument.",
                                   "status": "INVALID_ARGUMENT"}})
GOOGLE_400 = {"error": {"message": "Provider returned error", "code": 400,
                        "metadata": {"raw": GOOGLE_RAW, "provider_name": "Google"}}}
COMPLETION = {"id": "x", "object": "chat.completion", "created": 0, "model": "m",
              "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
              "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}}
TOOL = ToolInfo(name="board_read", description="Read the board.", parameters=ToolParams())


def history() -> list:
    old = ToolCall(id="call_1", function="board_read", arguments={})
    new = [ToolCall(id="call_2", function="board_read", arguments={}),
           ToolCall(id="call_3", function="board_read", arguments={})]
    return [ChatMessageUser(content="go"), ChatMessageAssistant(content="", tool_calls=[old]),
            ChatMessageTool(content="posts", tool_call_id="call_1", function="board_read"),
            ChatMessageAssistant(content="", tool_calls=new),
            ChatMessageTool(content="posts", tool_call_id="call_2", function="board_read"),
            ChatMessageTool(content="files", tool_call_id="call_3", function="board_read"),
            ChatMessageUser(content="[Since your last turn] the board changed.")]


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(tool_ids, "_failed", set())
    monkeypatch.setattr(retry, "_tries", {})
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    retry.install()
    tool_ids.install()


def bodies_for(model_name: str, answers: list[tuple[int, dict]], messages: list) -> list[dict]:
    """The JSON bodies the real request code sent, answered in turn by a fake OpenRouter."""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        status, payload = answers[min(len(bodies), len(answers)) - 1]
        return httpx.Response(status, json=payload)

    model = get_model(model_name, memoize=False, **models.model_args(model_name))
    model.api.client = openai.AsyncOpenAI(api_key="dummy", base_url="https://openrouter.ai/api/v1", max_retries=0,
                                          http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    output = asyncio.run(model.generate(messages, tools=[TOOL]))
    assert output.completion == "ok"
    return bodies


def ids(body: dict) -> list[list[str]]:
    return [[c["id"] for c in m["tool_calls"]] for m in body["messages"] if m.get("tool_calls")]


def results(body: dict) -> list[str]:
    return [m["tool_call_id"] for m in body["messages"] if m["role"] == "tool"]


def test_the_retry_after_google_400_renames_the_newest_step_and_nothing_else():
    argon = models.resolve("gemini-4-argon-openrouter")
    first, second = bodies_for(argon, [(400, GOOGLE_400), (200, COMPLETION)], history())
    assert ids(first) == [["call_1"], ["call_2", "call_3"]] and results(first) == ["call_1", "call_2", "call_3"]
    assert ids(second) == [["call_1"], ["call_2_r", "call_3_r"]] and results(second) == ["call_1", "call_2_r",
                                                                                         "call_3_r"]
    same = json.dumps(second).replace("call_2_r", "call_2").replace("call_3_r", "call_3")
    assert json.loads(same) == first


def test_a_first_send_and_other_models_are_never_renamed():
    luna = models.resolve("gpt-6-luna-openrouter")
    tool_ids._failed.add(frozenset({"call_2", "call_3"}))
    [body] = bodies_for(luna, [(200, COMPLETION)], history())
    assert ids(body) == [["call_1"], ["call_2", "call_3"]]
    tool_ids._failed.clear()
    [body] = bodies_for(models.resolve("gemini-4-argon-openrouter"), [(200, COMPLETION)], history())
    assert ids(body) == [["call_1"], ["call_2", "call_3"]]


def test_a_renamed_request_keeps_its_key_and_inspect_s_messages_keep_their_ids():
    renamed = tool_ids.rename_newest_step([{"role": "assistant", "tool_calls": [{"id": "a"}]},
                                           {"role": "tool", "tool_call_id": "a"}])
    assert tool_ids.newest_step_ids(renamed) == frozenset({"a"})
    messages = history()
    bodies_for(models.resolve("gemini-4-argon-openrouter"), [(400, GOOGLE_400), (200, COMPLETION)], messages)
    assert [c.id for c in messages[3].tool_calls] == ["call_2", "call_3"]
