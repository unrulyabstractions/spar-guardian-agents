"""After Google's 400 through OpenRouter, the retry sends the same messages with the newest step's tool-call ids
renamed; every other request, every other step and Inspect's own messages are untouched."""
from __future__ import annotations

import asyncio
import json

import httpx
import openai

from src.models import openrouter_gemini_tool_ids as tool_ids
from src.models import openrouter_google_400_retry as retry

HISTORY = [
    {"role": "user", "content": "go"},
    {"role": "assistant", "content": "\n", "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "board_read", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "call_1", "content": "posts"},
    {"role": "user", "content": "Please continue with your next action."},
    {"role": "assistant", "content": "\n", "tool_calls": [
        {"id": "call_2", "type": "function", "function": {"name": "board_read", "arguments": "{}"}},
        {"id": "call_3", "type": "function", "function": {"name": "shell", "arguments": "{\"cmd\": \"ls\"}"}}]},
    {"role": "tool", "tool_call_id": "call_2", "content": "posts"},
    {"role": "tool", "tool_call_id": "call_3", "content": "files"},
    {"role": "user", "content": "[Since your last turn] the board changed."},
]
GOOGLE_400 = {"message": "Provider returned error", "code": 400,
              "metadata": {"raw": '{"error": {"code": 400, "message": "Request contains an invalid argument.", '
                                  '"status": "INVALID_ARGUMENT"}}', "provider_name": "Google"}}


class FakeProvider:
    def __init__(self, family: str):
        self.family = family

    def model_family(self) -> str:
        return self.family


def google_400(messages: list) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions",
                            content=json.dumps({"model": "google/gemini-4-argon", "messages": messages}).encode())
    response = httpx.Response(400, request=request, json={"error": GOOGLE_400})
    return openai.APIStatusError("Error code: 400", response=response, body=GOOGLE_400)


def test_only_the_newest_step_and_its_results_are_renamed_and_the_input_is_not_mutated():
    out = tool_ids.rename_newest_step(HISTORY)
    assert [c["id"] for c in out[4]["tool_calls"]] == ["call_2_r", "call_3_r"]
    assert [m["tool_call_id"] for m in out[5:7]] == ["call_2_r", "call_3_r"]
    assert out[:4] == HISTORY[:4] and out[7] == HISTORY[7]
    assert [c["id"] for c in HISTORY[4]["tool_calls"]] == ["call_2", "call_3"]
    assert HISTORY[5]["tool_call_id"] == "call_2"


def test_a_conversation_without_tool_calls_is_unchanged():
    plain = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    assert tool_ids.rename_newest_step(plain) == plain


def test_a_google_400_marks_its_messages_and_the_next_send_of_them_is_renamed(monkeypatch):
    from inspect_ai.model._providers.openrouter import OpenRouterAPI

    monkeypatch.setattr(tool_ids, "_failed", set())
    monkeypatch.setattr(retry, "_tries", {})

    async def provider_conversion(self, input):   # stands in for Inspect's own conversion
        return [dict(m) for m in input]

    monkeypatch.setattr(OpenRouterAPI, "messages_to_openai", provider_conversion)
    assert tool_ids.install() and tool_ids.install()
    send = OpenRouterAPI.messages_to_openai
    gemini, other = FakeProvider("google/gemini-4-argon"), FakeProvider("openai/gpt-6-luna")
    assert asyncio.run(send(gemini, HISTORY)) == HISTORY            # a first send is never touched
    assert retry.decide(google_400(HISTORY)) is True                # the 400 is retried and marks the messages
    resent = asyncio.run(send(gemini, HISTORY))
    assert resent == tool_ids.rename_newest_step(HISTORY) and resent != HISTORY
    assert asyncio.run(send(other, HISTORY)) == HISTORY             # never for another model
    assert asyncio.run(send(gemini, HISTORY[:4])) == HISTORY[:4]    # never for other messages
