"""Prompt caching for Claude, on both routes a run can take to it: where the cache marks go, and a refusal until a paid
check proves the cache is read under the Inspect that is installed.

    claude_route("claude-opus-5-5-openrouter")   -> "openrouter"
    claude_route("claude-opus-5-5")              -> "anthropic"
    claude_route("gpt-6-luna-openrouter")        -> None
    require_proven(["anthropic/claude-opus-5-5"])  -> ValueError until its entry records caching_verified

Where the marks go. Anthropic caches a request's prefix up to a block marked with cache_control, and it refuses a
request with more than four marks (HTTP 400, "A maximum of 4 blocks with cache_control may be provided. Found 5.",
seen on 2026-11-22). Inspect places all four itself on every Claude request; we add none.

Through OpenRouter (`_add_anthropic_cache_markers` in inspect_ai/model/_providers/openrouter.py, Inspect c574b6b):
- the last block of the system message, so the system prompt is read back even when the history misses;
- the last tool definition, so the tool list is read back with it;
- the last text block of the history, so each call writes its whole prompt to the cache;
- the text block before that, a fallback for Anthropic's lookback when the last block changes between calls.
A plain-string message that gets a mark, a tool result included, goes out as one text part, since a string cannot
carry one. A top-level cache_control there would make a fifth mark, and every Claude request would be refused.

At Anthropic (`resolve_chat_input` and `add_lookback_cache_control` in inspect_ai/model/_providers/anthropic.py,
Inspect c574b6b):
- a top-level cache_control, Anthropic's automatic caching, which marks the last cacheable block and takes one of the
  four slots;
- the last block of the system prompt;
- the last tool definition;
- the cacheable block before the last one, the lookback's fallback.

An agent's history only grows, so each call's prompt starts with the whole previous prompt, and the mark on the last
block lets the next call read all of it. Other families get no marks; their hosts cache on their own.

Why a paid check. A unit test can count the marks in a request built offline, and tests/unit/test_claude_prompt_cache.py
does. Only a paid call shows that Anthropic reads the cache. `require_proven` refuses a run that puts a Claude model on
either route until model_registry.yaml records, for that entry, `caching_verified` with the Inspect commit the check
ran under. An Inspect bump therefore refuses Claude until scripts/check_anthropic_cache.py (about a cent per model,
only with the user's yes) passes again.
"""
from __future__ import annotations

import re
from typing import Any

import inspect_ai

from src.models import model_registry as models

MAX_BREAKPOINTS = 4
"""The most cache_control marks Anthropic accepts in one request."""

ROUTE_NAMES = {"openrouter": "Claude through OpenRouter", "anthropic": "Claude at Anthropic"}


def claude_route(model: str) -> str | None:
    """The route a Claude model takes: "openrouter", "anthropic" (Inspect's Anthropic provider), or None for any
    other model."""
    name = _inspect_name(model)
    if name.startswith("openrouter/anthropic/"):
        return "openrouter"
    return "anthropic" if models.provider_of(name) == "anthropic" else None


def breakpoints(body: dict[str, Any]) -> list[str]:
    """Where the cache_control marks sit in a request body, chat completions (OpenRouter) or messages (Anthropic):
    "top-level", "system" for a block of Anthropic's system prompt, "tool <name>", or "<role> <message index>" for a
    marked content block."""
    found = ["top-level"] if "cache_control" in body else []
    system = body.get("system")
    if isinstance(system, list):
        found += ["system" for block in system if isinstance(block, dict) and "cache_control" in block]
    for tool in body.get("tools") or []:
        if "cache_control" in tool:
            found.append(f"tool {tool.get('function', {}).get('name') or tool.get('name')}")
    for index, message in enumerate(body.get("messages") or []):
        content = message.get("content")
        if isinstance(content, list):
            found += [f"{message['role']} {index}" for block in content
                      if isinstance(block, dict) and "cache_control" in block]
    return found


def shape_problem(body: dict[str, Any], route: str) -> str | None:
    """Why a Claude request body would not cache each call's whole prompt, or None: more marks than Anthropic accepts,
    or no mark on the last message. At Anthropic the top-level mark covers the last message."""
    marks = breakpoints(body)
    if len(marks) > MAX_BREAKPOINTS:
        return f"{len(marks)} cache marks, Anthropic accepts {MAX_BREAKPOINTS}: {marks}"
    last = f"{body['messages'][-1]['role']} {len(body['messages']) - 1}"
    if last not in marks and not (route == "anthropic" and "top-level" in marks):
        return f"the last message carries no cache mark: {marks}"
    return None


def installed_inspect() -> str:
    """The installed Inspect's commit (the `+g<hash>` of a build from git), or its version for a release."""
    match = re.search(r"\+g([0-9a-f]{7,})", inspect_ai.__version__)
    return match.group(1) if match else inspect_ai.__version__


def same_commit(a: str, b: str) -> bool:
    """Two abbreviations of one commit: the shorter is a prefix of the longer (builds abbreviate hashes to different
    lengths). A release version must match exactly."""
    hexes = all(re.fullmatch(r"[0-9a-f]{7,}", x) for x in (a, b))
    return (a.startswith(b) or b.startswith(a)) if hexes else a == b


def require_proven(run_models: list[str | None]) -> None:
    """Raise ValueError when a Claude model, on either route, runs before a paid check proved, under the installed
    Inspect, that its cache is read."""
    installed = installed_inspect()
    for model in filter(None, run_models):
        route = claude_route(model)
        if route is None:
            continue
        refused = f"{model}: {ROUTE_NAMES[route]} is refused until scripts/check_anthropic_cache.py"
        verified = (models.entry_of(_inspect_name(model)) or {}).get("caching_verified") or {}
        recorded = str(verified.get("inspect", "")) if isinstance(verified, dict) else ""
        if not recorded:
            raise ValueError(f"{refused} (a paid check of about a cent) shows its cache is read and "
                             "model_registry.yaml records caching_verified")
        if not same_commit(recorded, installed):
            raise ValueError(f"{refused} passes again: caching_verified names Inspect {recorded}, and Inspect "
                             f"{installed} is installed (a paid check of about a cent)")


def _inspect_name(model: str) -> str:
    return models.resolve(model.removeprefix("swarm/"))
