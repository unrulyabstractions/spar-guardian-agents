"""Retry Google's 400 "Request contains an invalid argument." through OpenRouter, at most three times per request.

On 2026-11-01 two DeepSeek runs with a Gemini 4 Argon regulator ended at the regulator's turn 149 on this error. The
exact request body recorded in the eval log later succeeded unchanged, so the error was not the request's content.
Inspect's OpenRouter provider retries 429, 5xx and timeouts but ends the sample on a 400. `install()` patches
`OpenRouterAPI.should_retry` so this one 400, from Google, is retried as a transient error, at most MAX_RETRIES times
for the same request body (counted by the body's sha256), so a request Google rejects every time still ends.
Everything else passes through to the original decision. `python -m src run` installs it before the eval.
"""
from __future__ import annotations

import hashlib
import json
import sys

MAX_RETRIES = 3
_tries: dict[str, int] = {}


def is_google_invalid_argument(ex: BaseException) -> bool:
    """Whether ex is OpenRouter's 400 that wraps Google's INVALID_ARGUMENT."""
    if getattr(ex, "status_code", None) != 400:
        return False
    body = getattr(ex, "body", None)
    text = json.dumps(body) if body is not None else str(ex)
    return "google" in text.lower() and ("invalid argument" in text.lower() or "INVALID_ARGUMENT" in text)


def _request_key(ex: BaseException) -> str:
    request = getattr(ex, "request", None)
    try:
        content = request.content if request is not None else b""
    except Exception:  # noqa: BLE001 - a streamed body has no content: count it under one key
        content = b""
    return hashlib.sha256(content or repr(type(ex)).encode()).hexdigest()


def decide(ex: BaseException) -> bool | None:
    """True to retry, False to stop, None when this module has no say."""
    if not is_google_invalid_argument(ex):
        return None
    key = _request_key(ex)
    _tries[key] = _tries.get(key, 0) + 1
    return _tries[key] <= MAX_RETRIES


def install() -> bool:
    """Patch the OpenRouter provider once. True when the patch is in place."""
    try:
        from inspect_ai.model._model import RetryDecision
        from inspect_ai.model._providers.openrouter import OpenRouterAPI
    except ImportError as e:  # pragma: no cover - only when the provider is absent
        print(f"openrouter_google_400_retry: not installed ({e})", file=sys.stderr)
        return False
    if getattr(OpenRouterAPI.should_retry, "_retry_google_400", False):
        return True
    original = OpenRouterAPI.should_retry

    def should_retry(self, ex: BaseException):
        verdict = decide(ex)
        if verdict is None:
            return original(self, ex)
        return RetryDecision.transient() if verdict else RetryDecision.no()

    should_retry._retry_google_400 = True  # type: ignore[attr-defined]
    OpenRouterAPI.should_retry = should_retry  # type: ignore[method-assign]
    return True
