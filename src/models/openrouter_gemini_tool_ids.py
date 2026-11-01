"""After Google's 400 "Request contains an invalid argument", resend the request to Gemini through OpenRouter with the
newest step's tool-call ids renamed.

On 2026-11-01 Gemini 4 Argon regulators failed runs with this 400. The pilot's failing request (346 messages, its
raw body from the eval log) was replayed to OpenRouter:
    unchanged                                   400, 3 of 3 (and Inspect's own 3 retries in the run)
    only the newest step's tool-call ids renamed   passed, 3 of 3 (two different suffixes)
    every tool-call id renamed                  400, 1 of 1
Another failing request that morning passed unchanged when replayed hours later. So the error depends on the newest
step's ids, the older steps need theirs, and it is not the content. Inspect 0.3.263 strips Gemini's reasoning_details
on replay (inspect_ai/model/_providers/openrouter.py, messages_to_openai). UNVERIFIED: why, on OpenRouter's or
Google's side.

Requests are left exactly as Inspect builds them, as in every request that passed. When openrouter_google_400_retry
classifies this 400 for a request, it records the request's messages (`remember_failure`). The wrapper installed here
then sends the same messages again with SUFFIX on the newest assistant step's tool-call ids and on those calls'
results. Inspect's own messages keep the ids the model returned. `python -m src run` installs it.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from typing import Any

SUFFIX = "_r"
_failed: set[str] = set()


def messages_key(messages: list[Any]) -> str:
    """A digest of OpenAI-format messages, the same for the same messages."""
    return hashlib.sha256(json.dumps(messages, sort_keys=True, default=str).encode()).hexdigest()


def remember_failure(messages: list[Any]) -> None:
    """Mark these messages so their next sending renames the newest step's tool-call ids."""
    _failed.add(messages_key(messages))


def rename_newest_step(messages: list[Any]) -> list[Any]:
    """A copy of OpenAI-format messages with SUFFIX on the tool-call ids of the last assistant message that calls
    tools, and on the tool results that answer those calls. Every other message is unchanged."""
    newest = max((i for i, m in enumerate(messages)
                  if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")), default=None)
    if newest is None:
        return list(messages)
    out = list(messages)
    step = copy.deepcopy(messages[newest])
    ids = set()
    for call in step["tool_calls"]:
        if call.get("id"):
            ids.add(call["id"])
            call["id"] = f"{call['id']}{SUFFIX}"
    out[newest] = step
    for i in range(newest + 1, len(out)):
        m = out[i]
        if isinstance(m, dict) and m.get("role") == "tool" and m.get("tool_call_id") in ids:
            out[i] = {**m, "tool_call_id": f"{m['tool_call_id']}{SUFFIX}"}
    return out


def install() -> bool:
    """Wrap the OpenRouter provider's message conversion once. True when the wrapper is in place."""
    try:
        from inspect_ai.model._providers.openrouter import OpenRouterAPI
    except ImportError as e:  # pragma: no cover - only when the provider is absent
        print(f"openrouter_gemini_tool_ids: not installed ({e})", file=sys.stderr)
        return False
    if getattr(OpenRouterAPI.messages_to_openai, "_renames_after_google_400", False):
        return True
    original = OpenRouterAPI.messages_to_openai

    async def messages_to_openai(self, input):
        messages = await original(self, input)
        if "gemini" in self.model_family().lower() and messages_key(messages) in _failed:
            return rename_newest_step(messages)
        return messages

    messages_to_openai._renames_after_google_400 = True  # type: ignore[attr-defined]
    OpenRouterAPI.messages_to_openai = messages_to_openai  # type: ignore[method-assign]
    return True
