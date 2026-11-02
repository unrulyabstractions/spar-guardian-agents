"""Prompt caching for Claude models through OpenRouter, and a refusal until a paid check proves it works.

Anthropic caches a request's prefix only up to a cache_control marker (OpenRouter's prompt-caching page: "Place
cache_control directly on individual content blocks", at most four breakpoints; a read costs 0.1x the input price).
Inspect 0.3.263 marks the system message, the last tool, and the block before the last only when that message's
content is a list. Our swarm conversations end in plain strings, so no marker ever covered the growing history, and
every Claude call paid full price for all of it: on 2026-11-01 an Opus regulator sent 10.9M uncached input tokens
against 0.3M cached in 22 minutes.

`install()` adds one marker on the newest message's last block, so each call reads every earlier message from the
cache. A string user or system message becomes a one-block list. A tool message is never touched (OpenRouter turns its
string into Anthropic's tool_result), nor is an empty one. No request ends with more than four markers.

`require_proven(models)` refuses a run that puts a Claude model through OpenRouter until model_registry.yaml records
`caching_verified` for that entry. Only scripts/check_anthropic_cache.py, a paid two-call check (under one cent) that
needs the user's yes, can show the cache is read. A unit test cannot.
"""
from __future__ import annotations

from typing import Any

from src.models import model_registry as models

MAX_BREAKPOINTS = 4


def _ephemeral() -> dict[str, str]:
    return {"type": "ephemeral"}


def count_markers(request: dict[str, Any]) -> int:
    """The cache_control markers in a request's messages and tools."""
    blocks = [block for message in request.get("messages") or [] if isinstance(message.get("content"), list)
              for block in message["content"] if isinstance(block, dict)]
    return sum("cache_control" in item for item in [*blocks, *(request.get("tools") or [])])


def mark_newest_message(request: dict[str, Any]) -> bool:
    """Put a marker on the newest message's last block when that is allowed and fits; True when one was added."""
    messages = request.get("messages") or []
    if not messages or count_markers(request) >= MAX_BREAKPOINTS:
        return False
    newest = messages[-1]
    if newest.get("role") not in ("user", "system", "assistant"):
        return False
    content = newest.get("content")
    if isinstance(content, str):
        if not content.strip() or newest.get("role") == "assistant":
            return False
        newest["content"] = [{"type": "text", "text": content, "cache_control": _ephemeral()}]
        return True
    if isinstance(content, list) and content and isinstance(content[-1], dict) and "cache_control" not in content[-1]:
        if content[-1].get("type") == "text" and not str(content[-1].get("text") or "").strip():
            return False
        content[-1]["cache_control"] = _ephemeral()
        return True
    return False


def install() -> bool:
    """Wrap OpenRouterAPI._generate_completion once; the marker goes only on requests Inspect itself caches (Claude
    models, cache_prompt not False). True when the wrapper is in place."""
    from inspect_ai.model._providers.openrouter import OpenRouterAPI

    if getattr(OpenRouterAPI._generate_completion, "_anthropic_newest_marker", False):
        return True
    original = OpenRouterAPI._generate_completion

    async def _generate_completion(self, request, config):
        if self._cache_prompt_enabled(config):
            mark_newest_message(request)
        return await original(self, request, config)

    _generate_completion._anthropic_newest_marker = True  # type: ignore[attr-defined]
    OpenRouterAPI._generate_completion = _generate_completion  # type: ignore[method-assign]
    return True


def is_claude_on_openrouter(model: str) -> bool:
    name = models.resolve(model[len("swarm/"):] if model.startswith("swarm/") else model)
    return name.startswith("openrouter/anthropic/")


def require_proven(run_models: list[str | None]) -> None:
    """Raise ValueError when a Claude model goes through OpenRouter before a paid check proved its cache is read."""
    for model in filter(None, run_models):
        if not is_claude_on_openrouter(model):
            continue
        entry = models.entry_of(models.resolve(model[len("swarm/"):] if model.startswith("swarm/") else model))
        if not (entry or {}).get("caching_verified"):
            raise ValueError(f"{model}: Claude through OpenRouter is refused until scripts/check_anthropic_cache.py "
                             "(a paid check under one cent, run only with the user's yes) shows its cache is read and "
                             "model_registry.yaml records caching_verified")
