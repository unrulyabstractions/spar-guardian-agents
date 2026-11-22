"""Prompt caching for Claude models through OpenRouter: where the cache markers go, and a refusal until a paid check
proves the cache is read under the Inspect that is installed.

Where the markers go. Anthropic caches a request's prefix up to a block marked with cache_control, and it refuses a
request with more than four marks (HTTP 400, "A maximum of 4 blocks with cache_control may be provided. Found 5.",
seen on 2026-11-22). Inspect's OpenRouter provider places all four itself on every request to an anthropic/ model
(`_add_anthropic_cache_markers` in inspect_ai/model/_providers/openrouter.py, Inspect c574b6b):

- the last block of the system message, so the system prompt is read back even when the history misses;
- the last tool definition, so the tool list is read back with it;
- the last text block of the history, so each call writes its whole prompt to the cache;
- the text block before that, a fallback for Anthropic's lookback when the last block changes between calls.

We add none. An agent's history only grows, so each call's prompt starts with the whole previous prompt, and the mark
on the last block lets the next call read all of it. A plain-string message that gets a mark, a tool result included,
goes out as one text part, since a string cannot carry one. Other families get no marks; their hosts cache on their
own.

Inspect before c574b6b marked only the system message and the last tool, so we sent OpenRouter's top-level
cache_control as well. Under c574b6b that field made a fifth mark, and every Claude request was refused.

Why a paid check. A unit test can count the marks in a request built offline, and
tests/unit/test_openrouter_anthropic_cache.py does. Only a paid call shows that Anthropic reads the cache.
`require_proven` refuses a run that puts a Claude model through OpenRouter until model_registry.yaml records, for that
entry, `caching_verified` with the Inspect commit the check ran under. An Inspect bump therefore refuses Claude until
scripts/check_anthropic_cache.py (about a cent per model, only with the user's yes) passes again.
"""
from __future__ import annotations

import re
from typing import Any

import inspect_ai

from src.models import model_registry as models

MAX_BREAKPOINTS = 4
"""The most cache_control marks Anthropic accepts in one request."""


def is_claude_on_openrouter(model: str) -> bool:
    return _inspect_name(model).startswith("openrouter/anthropic/")


def breakpoints(body: dict[str, Any]) -> list[str]:
    """Where the cache_control marks sit in a chat-completions request body: "top-level", "tool <name>", or
    "<role> <message index>" for a marked content block."""
    found = ["top-level"] if "cache_control" in body else []
    for tool in body.get("tools") or []:
        if "cache_control" in tool:
            found.append(f"tool {tool.get('function', {}).get('name')}")
    for index, message in enumerate(body.get("messages") or []):
        content = message.get("content")
        if isinstance(content, list):
            found += [f"{message['role']} {index}" for block in content
                      if isinstance(block, dict) and "cache_control" in block]
    return found


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
    """Raise ValueError when a Claude model goes through OpenRouter before a paid check proved, under the installed
    Inspect, that its cache is read."""
    installed = installed_inspect()
    for model in filter(None, run_models):
        if not is_claude_on_openrouter(model):
            continue
        verified = (models.entry_of(_inspect_name(model)) or {}).get("caching_verified") or {}
        recorded = str(verified.get("inspect", "")) if isinstance(verified, dict) else ""
        if not recorded:
            raise ValueError(f"{model}: Claude through OpenRouter is refused until scripts/check_anthropic_cache.py "
                             "(a paid check of about a cent) shows its cache is read and "
                             "model_registry.yaml records caching_verified")
        if not same_commit(recorded, installed):
            raise ValueError(f"{model}: Claude through OpenRouter is refused until scripts/check_anthropic_cache.py "
                             f"passes again: caching_verified names Inspect {recorded}, and Inspect {installed} is "
                             "installed (a paid check of about a cent)")


def _inspect_name(model: str) -> str:
    return models.resolve(model.removeprefix("swarm/"))
