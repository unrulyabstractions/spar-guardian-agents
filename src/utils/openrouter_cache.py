"""Cache an Anthropic model's conversation through OpenRouter, not only its system prompt and tools.

Inspect's OpenRouter provider marks the last system block, the last tool and the penultimate block of the last message
(or the last block of the message before it) for Anthropic's prompt cache, but only when that content is a list of
blocks. In the requests it sends, tool results and the turn rules' provider-only messages are plain strings, so no
message is ever marked: every call is billed in full for the whole conversation, and only the system prompt and tools
are read from the cache (seen with claude-haiku-5.5, 2026-10-27: about 2k of 10k tokens a call). `install()` wraps the
marker function so it also marks the last tool result, written as one text block with the same text, while the
request holds fewer than Anthropic's four breakpoints. The turn rules' notices come after it and change every call,
so the prefix up to that result is what the next call reads back. A direct check through OpenRouter (Anthropic
endpoint) read 17,073 of 17,089 prompt tokens back on the second call, and the model quoted the tool result
correctly in both forms. `python -m src run` installs it before the eval.
"""
from __future__ import annotations

import sys
from typing import Any

MAX_BREAKPOINTS = 4


def _breakpoints(request: dict[str, Any]) -> int:
    def marked(block) -> bool:
        return isinstance(block, dict) and "cache_control" in block
    count = sum(marked(t) for t in request.get("tools") or [])
    for message in request.get("messages") or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            count += sum(marked(b) for b in content)
    return count


def mark_last_tool_result(request: dict[str, Any]) -> bool:
    """Mark the last tool message's text for the cache, as one text block. True when a mark was added."""
    if _breakpoints(request) >= MAX_BREAKPOINTS:
        return False
    for message in reversed(request.get("messages") or []):
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        content = message.get("content")
        if isinstance(content, str) and content:
            message["content"] = [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
            return True
        if isinstance(content, list) and content and isinstance(content[-1], dict):
            if "cache_control" in content[-1]:
                return False
            content[-1]["cache_control"] = {"type": "ephemeral"}
            return True
        return False
    return False


def install() -> bool:
    """Wrap Inspect's OpenRouter marker function once. True when the wrapper is in place."""
    try:
        from inspect_ai.model._providers import openrouter
    except ImportError as e:  # pragma: no cover - only when the provider is absent
        print(f"openrouter_cache: not installed ({e})", file=sys.stderr)
        return False
    original = openrouter._add_anthropic_cache_markers
    if getattr(original, "_marks_tool_results", False):
        return True

    def add_markers(request: dict[str, Any]) -> None:
        original(request)
        mark_last_tool_result(request)

    add_markers._marks_tool_results = True  # type: ignore[attr-defined]
    openrouter._add_anthropic_cache_markers = add_markers
    return True
