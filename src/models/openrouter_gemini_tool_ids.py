"""After Google's 400 "Request contains an invalid argument", resend the request to Gemini through OpenRouter with the
newest step's tool-call ids renamed.

On 2026-11-01 Gemini 4 Argon regulators failed runs with this 400. The pilot's failing request (346 messages, its
raw body from the eval log) was replayed to OpenRouter:
    unchanged                                   400, 3 of 3 (and Inspect's own 3 retries in the run)
    only the newest step's tool-call ids renamed   passed, 3 of 3 (two different suffixes)
    every tool-call id renamed                  400, 1 of 1
So the error depends on the newest step's ids. UNVERIFIED: why, on OpenRouter's or Google's side.

The first version (c45c36c) keyed a failure by a hash of the request's messages and renamed inside
messages_to_openai. In a real run the hash never matched what was sent next, so the retries went out unchanged and
the run failed at the regulator's turn 175 (VERIFICATION_LOG.md, 2026-11-01 21:45). This version works on the final
request: openrouter_google_400_retry records the failed body's newest-step ids (`remember_failure`), and the wrapper on
OpenRouterAPI._generate_completion renames them in the next request whose newest step holds the same ids, Gemini only.
Inspect's own messages keep the ids the model returned. `python -m src run` installs it.
"""
from __future__ import annotations

import copy
from typing import Any

SUFFIX = "_r"
_failed: set[frozenset[str]] = set()


def newest_step(messages: list[Any]) -> int | None:
    """The index of the last assistant message that calls tools, or None."""
    return max((i for i, m in enumerate(messages)
                if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")), default=None)


def newest_step_ids(messages: list[Any]) -> frozenset[str]:
    """The newest step's tool-call ids without SUFFIX, so a renamed request that fails again keeps its key."""
    index = newest_step(messages)
    if index is None:
        return frozenset()
    return frozenset(str(call.get("id")).removesuffix(SUFFIX) for call in messages[index]["tool_calls"]
                     if call.get("id"))


def remember_failure(messages: list[Any]) -> None:
    """Mark the newest step of a request Google refused, so its next sending renames that step's ids."""
    ids = newest_step_ids(messages)
    if ids:
        _failed.add(ids)


def rename_newest_step(messages: list[Any]) -> list[Any]:
    """A copy of OpenAI-format messages with SUFFIX on the tool-call ids of the last assistant message that calls
    tools, and on the tool results that answer those calls. Every other message is unchanged."""
    index = newest_step(messages)
    if index is None:
        return list(messages)
    out = list(messages)
    step = copy.deepcopy(messages[index])
    ids = set()
    for call in step["tool_calls"]:
        if call.get("id") and not str(call["id"]).endswith(SUFFIX):
            ids.add(call["id"])
            call["id"] = f"{call['id']}{SUFFIX}"
    out[index] = step
    for i in range(index + 1, len(out)):
        m = out[i]
        if isinstance(m, dict) and m.get("role") == "tool" and m.get("tool_call_id") in ids:
            out[i] = {**m, "tool_call_id": f"{m['tool_call_id']}{SUFFIX}"}
    return out


def install() -> bool:
    """Wrap OpenRouterAPI._generate_completion once. True when the wrapper is in place."""
    from inspect_ai.model._providers.openrouter import OpenRouterAPI

    if getattr(OpenRouterAPI._generate_completion, "_renames_after_google_400", False):
        return True
    original = OpenRouterAPI._generate_completion

    async def _generate_completion(self, request, config):
        messages = request.get("messages") or []
        if "gemini" in self.model_family().lower() and newest_step_ids(messages) in _failed:
            request = {**request, "messages": rename_newest_step(messages)}
        return await original(self, request, config)

    _generate_completion._renames_after_google_400 = True  # type: ignore[attr-defined]
    OpenRouterAPI._generate_completion = _generate_completion  # type: ignore[method-assign]
    return True
