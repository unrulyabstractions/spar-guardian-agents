"""Google's 400 "invalid argument" through OpenRouter is retried at most three times per request body; every other
error keeps Inspect's own decision."""
from __future__ import annotations

import httpx
import openai
import pytest

from src.models import openrouter_google_400_retry as retry

GOOGLE_400 = {"message": "Provider returned error", "code": 400,
              "metadata": {"raw": '{"error": {"code": 400, "message": "Request contains an invalid argument.", '
                                  '"status": "INVALID_ARGUMENT"}}', "provider_name": "Google"}}


def error(status: int, body: dict, content: bytes = b'{"messages": []}') -> openai.APIStatusError:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions", content=content)
    response = httpx.Response(status, request=request, json={"error": body})
    return openai.APIStatusError("Error code: %d" % status, response=response, body=body)


@pytest.fixture(autouse=True)
def fresh_counts(monkeypatch):
    monkeypatch.setattr(retry, "_tries", {})


def test_googles_400_is_retried_three_times_per_request_then_stops():
    ex = error(400, GOOGLE_400)
    assert [retry.decide(ex) for _ in range(4)] == [True, True, True, False]
    other = error(400, GOOGLE_400, content=b'{"messages": ["another request"]}')
    assert retry.decide(other) is True


def test_other_errors_keep_inspects_own_decision():
    assert retry.decide(error(400, {"message": "bad tool schema", "code": 400,
                                    "metadata": {"provider_name": "OpenAI"}})) is None
    assert retry.decide(error(403, {"message": "Gemini blocked the request: SAFETY", "code": 403,
                                    "metadata": {"provider_name": "Google"}})) is None
    assert retry.decide(error(402, {"message": "key limit", "code": 402})) is None
    assert retry.decide(ValueError("x")) is None


def test_the_patch_classifies_through_the_provider():
    from inspect_ai.model._providers.openrouter import OpenRouterAPI

    assert retry.install() and retry.install()
    should = OpenRouterAPI.should_retry
    assert getattr(should, "_retry_google_400", False)
    decision = should(object.__new__(OpenRouterAPI), error(400, GOOGLE_400))
    assert decision and decision.kind == "transient"
    assert not should(object.__new__(OpenRouterAPI), error(400, {"message": "bad", "code": 400,
                                                                 "metadata": {"provider_name": "OpenAI"}}))
