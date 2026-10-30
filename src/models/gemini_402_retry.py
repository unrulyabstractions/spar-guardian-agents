"""Treat the Gemini API's 402 "prepayment credits are depleted" as a retryable error.

With prepaid billing and auto-reload, the Gemini API answers 402 RESOURCE_EXHAUSTED once the balance is depleted, and
the reload lands minutes later. Inspect retries 429 but ends the sample on 402. `install()` patches the Google
provider's `should_retry` so a 402 is classified as a rate limit (unlimited retries by default; the run's --timeout
bounds them). Everything else passes through to the original decision. `python -m src run` installs it before the
eval; `inspect eval src/tasks.py@...` does not.
"""
from __future__ import annotations

import sys

RETRY_STATUS = 402


def install() -> bool:
    """Patch the Google provider once. True when the patch is in place."""
    try:
        from google.genai.errors import APIError
        from inspect_ai.model._model import RetryDecision
        from inspect_ai.model._providers.google import GoogleGenAIAPI
    except ImportError as e:  # pragma: no cover - only when the provider is absent
        print(f"gemini_402_retry: not installed ({e})", file=sys.stderr)
        return False
    if getattr(GoogleGenAIAPI.should_retry, "_retry_402", False):
        return True
    original = GoogleGenAIAPI.should_retry

    def should_retry(self, ex: BaseException):
        if isinstance(ex, APIError) and ex.code == RETRY_STATUS:
            return RetryDecision.rate_limit()
        return original(self, ex)

    should_retry._retry_402 = True  # type: ignore[attr-defined]
    GoogleGenAIAPI.should_retry = should_retry  # type: ignore[method-assign]
    return True
