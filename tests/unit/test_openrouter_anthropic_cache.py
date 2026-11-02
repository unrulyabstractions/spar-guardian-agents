"""A Claude request through OpenRouter carries a cache marker on its newest message, through Inspect's real request
code down to the bytes the OpenAI client would send (a fake server stands in for OpenRouter): never more than four
markers, a tool message's string untouched, no marker for other models, and Claude refused until a paid check is
recorded."""
from __future__ import annotations

import asyncio
import json

import httpx
import openai
import pytest
from inspect_ai.model import ChatMessageAssistant, ChatMessageSystem, ChatMessageTool, ChatMessageUser, get_model
from inspect_ai.tool import ToolCall, ToolInfo, ToolParams

from src.models import model_registry as models
from src.models import openrouter_anthropic_cache as cache

COMPLETION = {"id": "x", "object": "chat.completion", "created": 0, "model": "m",
              "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
              "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}}
TOOL = ToolInfo(name="board_read", description="Read the board.", parameters=ToolParams())


def sent_body(model_name: str, messages: list, monkeypatch) -> dict:
    """The JSON body the real request code sends for these messages, read by a fake OpenRouter."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    cache.install()
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=COMPLETION)

    model = get_model(model_name, memoize=False, **models.model_args(model_name))
    model.api.client = openai.AsyncOpenAI(api_key="dummy", base_url="https://openrouter.ai/api/v1", max_retries=0,
                                          http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    asyncio.run(model.generate(messages, tools=[TOOL]))
    assert len(bodies) == 1
    return bodies[0]


def history(newest) -> list:
    call = ToolCall(id="call_1", function="board_read", arguments={})
    return [ChatMessageSystem(content="You oversee a team."), ChatMessageUser(content="Begin."),
            ChatMessageAssistant(content="", tool_calls=[call]),
            ChatMessageTool(content="posts", tool_call_id="call_1", function="board_read"), newest]


def markers(body: dict) -> list[tuple[int, str]]:
    found = [(i, m["role"]) for i, m in enumerate(body["messages"]) if isinstance(m.get("content"), list)
             for block in m["content"] if isinstance(block, dict) and "cache_control" in block]
    return found + [(-1, "tool") for tool in body.get("tools") or [] if "cache_control" in tool]


HAIKU = models.resolve("claude-haiku-5-5-openrouter")


def test_a_claude_request_marks_its_newest_message_and_stays_within_four(monkeypatch):
    body = sent_body(HAIKU, history(ChatMessageUser(content="[Since your last turn] the board changed.")), monkeypatch)
    newest = body["messages"][-1]
    assert newest["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert newest["content"][-1]["text"] == "[Since your last turn] the board changed."
    assert len(markers(body)) <= cache.MAX_BREAKPOINTS
    assert isinstance(body["messages"][3]["content"], str)          # the tool message keeps its string


def test_a_newest_tool_message_is_left_alone(monkeypatch):
    messages = history(ChatMessageUser(content="go"))[:-1]
    body = sent_body(HAIKU, messages, monkeypatch)
    assert isinstance(body["messages"][-1]["content"], str) and body["messages"][-1]["role"] == "tool"
    assert len(markers(body)) <= cache.MAX_BREAKPOINTS


def test_other_models_get_no_marker(monkeypatch):
    for name in ("gpt-6-luna-openrouter", "deepseek-flash-openrouter", "gemini-4-argon-openrouter"):
        body = sent_body(models.resolve(name), history(ChatMessageUser(content="go")), monkeypatch)
        assert markers(body) == [], name


def test_the_marker_never_takes_a_fifth_breakpoint():
    full = {"messages": [{"role": "user", "content": [{"type": "text", "text": f"m{i}", "cache_control": {}}
                                                     for i in range(3)]},
                         {"role": "user", "content": "newest"}],
            "tools": [{"type": "function", "cache_control": {}}]}
    assert cache.count_markers(full) == 4 and cache.mark_newest_message(full) is False
    assert full["messages"][-1]["content"] == "newest"


def test_claude_through_openrouter_is_refused_until_a_paid_check_is_recorded(monkeypatch):
    with pytest.raises(ValueError, match="refused until scripts/check_anthropic_cache.py"):
        cache.require_proven([models.resolve("gpt-6-luna-openrouter"), "swarm/claude-opus-5-5-openrouter"])
    cache.require_proven([models.resolve("gpt-6-luna-openrouter"), "mockllm/model", None])
    entry = dict(models.entry_of("claude-opus-5-5-openrouter"), caching_verified="2026-11-02")
    monkeypatch.setattr(cache.models, "entry_of", lambda name: entry)
    cache.require_proven(["swarm/claude-opus-5-5-openrouter"])
