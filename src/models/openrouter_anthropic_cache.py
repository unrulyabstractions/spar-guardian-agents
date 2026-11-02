"""Prompt caching for Claude models through OpenRouter, and a refusal until a paid check proves it works.

Anthropic caches a request's prefix only up to a breakpoint. Inspect 0.3.263 marks the system message, the last tool,
and the block before the last only when that message's content is a list. Our swarm conversations end in plain strings
and tool results, so no marker ever covered the growing history, and every Claude call paid full price for all of it: on
2026-11-01 an Opus regulator sent 10.9M uncached input tokens against 0.3M cached in 22 minutes.

`install()` asks each such request for OpenRouter's automatic caching: a top-level cache_control, which "automatically
applies the cache breakpoint to the last cacheable block and advances it forward as conversations grow" (OpenRouter's
prompt-caching page). It goes in the request's extra_body, since the OpenAI client takes no such argument. It uses one
of Anthropic's four breakpoints, and Inspect's own markers use at most three.

A marker of our own on the newest message came first. It left tool messages alone, and an agent's newest message is
nearly always a tool result, so on the first real run with it (2026-11-02) each call read only its system prompt from
the cache, while a two-call check ending on a user message passed.

`require_proven(models)` refuses a run that puts a Claude model through OpenRouter until model_registry.yaml records
`caching_verified` for that entry. Only scripts/check_anthropic_cache.py, a paid two-call check (under one cent) shaped
like an agent's turn, can show the cache is read. A unit test cannot.
"""
from __future__ import annotations

from typing import Any

from src.models import model_registry as models


def ask_for_automatic_caching(request: dict[str, Any]) -> None:
    """Add the top-level cache_control to the body this request sends, beside what extra_body already holds."""
    request["extra_body"] = {**(request.get("extra_body") or {}), "cache_control": {"type": "ephemeral"}}


def install() -> bool:
    """Wrap OpenRouterAPI._generate_completion once; automatic caching goes only on requests Inspect itself caches
    (Claude models, cache_prompt not False). True when the wrapper is in place."""
    from inspect_ai.model._providers.openrouter import OpenRouterAPI

    if getattr(OpenRouterAPI._generate_completion, "_anthropic_automatic_caching", False):
        return True
    original = OpenRouterAPI._generate_completion

    async def _generate_completion(self, request, config):
        if self._cache_prompt_enabled(config):
            ask_for_automatic_caching(request)
        return await original(self, request, config)

    _generate_completion._anthropic_automatic_caching = True  # type: ignore[attr-defined]
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
