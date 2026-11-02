"""A Claude request through OpenRouter asks for automatic caching, through Inspect's real request code down to the bytes
the OpenAI client would send (a fake server stands in for OpenRouter): whatever its newest message, with a slot left
among Anthropic's four breakpoints, its routing kept, a tool message's string untouched, nothing for other models, and
Claude refused until a paid check is recorded."""
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


AUTOMATIC = {"type": "ephemeral"}


def test_a_claude_request_ending_in_a_user_message_asks_for_automatic_caching(monkeypatch):
    body = sent_body(HAIKU, history(ChatMessageUser(content="[Since your last turn] the board changed.")), monkeypatch)
    assert body["cache_control"] == AUTOMATIC
    assert body["messages"][-1]["content"] == "[Since your last turn] the board changed."


def test_a_claude_request_ending_in_a_tool_result_asks_too_and_the_result_stays_a_string(monkeypatch):
    """An agent's newest message is nearly always a tool result, so this is the request that matters."""
    body = sent_body(HAIKU, history(ChatMessageUser(content="go"))[:-1], monkeypatch)
    assert body["cache_control"] == AUTOMATIC
    assert isinstance(body["messages"][-1]["content"], str) and body["messages"][-1]["role"] == "tool"


@pytest.mark.parametrize("cut", [0, 1], ids=["user message", "tool result"])
def test_automatic_caching_leaves_a_breakpoint_free_and_keeps_the_routing(monkeypatch, cut):
    """Anthropic allows four breakpoints and the automatic one takes a slot, so Inspect's markers may take three."""
    messages = history(ChatMessageUser(content="go"))
    body = sent_body(HAIKU, messages[:len(messages) - cut], monkeypatch)
    assert len(markers(body)) <= 3
    assert body["provider"] == models.routing(HAIKU)


def test_other_models_are_not_asked(monkeypatch):
    for name in ("gpt-6-luna-openrouter", "deepseek-flash-openrouter", "gemini-4-argon-openrouter"):
        body = sent_body(models.resolve(name), history(ChatMessageUser(content="go")), monkeypatch)
        assert "cache_control" not in body and markers(body) == [], name


def test_installing_twice_wraps_once():
    from inspect_ai.model._providers.openrouter import OpenRouterAPI

    cache.install()
    wrapped = OpenRouterAPI._generate_completion
    cache.install()
    assert OpenRouterAPI._generate_completion is wrapped


def test_claude_through_openrouter_is_refused_until_a_paid_check_is_recorded(monkeypatch):
    with pytest.raises(ValueError, match="refused until scripts/check_anthropic_cache.py"):
        cache.require_proven([models.resolve("gpt-6-luna-openrouter"), "swarm/claude-opus-5-5-openrouter"])
    cache.require_proven([models.resolve("gpt-6-luna-openrouter"), "mockllm/model", None])
    entry = dict(models.entry_of("claude-opus-5-5-openrouter"), caching_verified="2026-11-02")
    monkeypatch.setattr(cache.models, "entry_of", lambda name: entry)
    cache.require_proven(["swarm/claude-opus-5-5-openrouter"])
