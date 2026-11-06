"""The self-hosted Qwen entry: it resolves to Inspect's OpenAI-compatible route under the selfhost provider, its base
URL defaults to the tunnel and an environment value overrides it, its tools go without `strict` as on OpenRouter, every
call carries the model's sampling and the template's reasoning effort, it is priced as the OpenRouter entry so the
dollar cap holds, a run of it is named after the entry, and vLLM's reasoning field comes back as reasoning content."""
from __future__ import annotations

import asyncio
import json
import os

import httpx
import pytest
from inspect_ai.model import ChatMessageTool, ChatMessageUser, ContentReasoning, GenerateConfig, get_model
from inspect_ai.model._providers.openai_compatible import OpenAICompatibleAPI
from inspect_ai.tool import ToolInfo, ToolParam, ToolParams

from elicitation import swe
from src.models import cost_cap
from src.models import model_registry as models
from src.models.swarm_model_provider import RegistryModelAPI

ENTRY = "qwen3.8-27b-fp8-selfhost"
INSPECT = "openai-api/selfhost/qwen3.8-27b"
SAMPLING = {"temperature": 1.0, "top_p": 0.95,
            "extra_body": {"top_k": 20, "chat_template_kwargs": {"reasoning_effort": "medium"}}}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(os, "environ", dict(os.environ))
    os.environ.pop("SELFHOST_BASE_URL", None)
    os.environ["SELFHOST_API_KEY"] = "dummy"


def test_the_entry_resolves_to_the_selfhost_route(env):
    assert models.resolve(ENTRY) == INSPECT and models.provider_of(INSPECT) == "selfhost"
    assert models.key_env(INSPECT) == ["SELFHOST_API_KEY"]
    assert models.model_args(ENTRY) == {"strict_tools": False} == models.model_args(INSPECT)
    assert models.generate_config(ENTRY) == SAMPLING
    assert models.generate_config("qwen3.8-27b-openrouter") == {} and models.generate_config("mockllm/model") == {}
    models.check_routing(INSPECT)                                   # no OpenRouter pin is needed
    models.prepare_env(INSPECT)
    assert os.environ["SELFHOST_BASE_URL"] == "http://127.0.0.1:8001/v1"


def test_an_environment_base_url_wins(env):
    os.environ["SELFHOST_BASE_URL"] = "http://127.0.0.1:9999/v1"
    models.prepare_env(INSPECT)
    assert os.environ["SELFHOST_BASE_URL"] == "http://127.0.0.1:9999/v1"
    model = get_model(INSPECT, memoize=False, **models.model_args(INSPECT))
    assert isinstance(model.api, OpenAICompatibleAPI) and model.api.base_url == "http://127.0.0.1:9999/v1"


def test_a_missing_key_is_refused(env):
    os.environ.pop("SELFHOST_API_KEY")
    with pytest.raises(ValueError, match="SELFHOST_API_KEY"):
        models.prepare_env(INSPECT)


def test_it_is_priced_as_the_openrouter_entry_so_the_cap_holds():
    assert models.usd_per_million(ENTRY) == models.usd_per_million("qwen3.8-27b-openrouter")
    assert cost_cap.is_paid(INSPECT) and cost_cap.cap_for([INSPECT], 0.60) == 0.60
    assert models.entries()[ENTRY]["context"] == 262144


def test_a_run_is_named_after_the_entry():
    assert swe.default_name(ENTRY, 3, "dont_pressure") == "flipt_duo_qwen3.8-27b-fp8-selfhost_dont_pressure_s3"
    assert swe.default_name(INSPECT, 1, "cheat_ambiguous") == "flipt_duo_qwen3.8-27b-fp8-selfhost_cheat_ambiguous_s1"


TOOL = ToolInfo(name="board_read", description="Read posts from the shared board.",
                parameters=ToolParams(properties={"latest": ToolParam(type="integer", description="How many.")}))
RESPONSE = {
    "id": "x", "object": "chat.completion", "created": 0, "model": "qwen3.8-27b",
    "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": "", "reasoning": "I should read the board first.",
        "tool_calls": [{"id": "call_1", "type": "function",
                        "function": {"name": "board_read", "arguments": "{\"latest\": 3}"}}]}}],
    "usage": {"prompt_tokens": 1000, "completion_tokens": 20, "total_tokens": 1020,
              "prompt_tokens_details": {"cached_tokens": 900}},
}


def wire(model_api) -> list[dict]:
    """Swap the API's HTTP client for one that records each request body and answers RESPONSE."""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=RESPONSE)

    transport = httpx.MockTransport(handler)
    model_api.client = model_api.client.with_options(http_client=httpx.AsyncClient(transport=transport))
    return bodies


@pytest.mark.parametrize("route", ["eval model", "role model"])
def test_the_wire_request_and_the_reply(env, route):
    """The agents' model as run.run builds it, and a role model through the swarm provider: the request carries the
    tools without strict and the entry's sampling; vLLM's `reasoning` comes back as reasoning content, the tool call
    parses, and the cached tokens are read."""
    models.prepare_env(INSPECT)
    if route == "eval model":
        model = get_model(INSPECT, memoize=False, config=GenerateConfig(**models.generate_config(INSPECT)),
                          **models.model_args(INSPECT))
        api = model.api
    else:
        model = get_model(f"swarm/{ENTRY}", memoize=False)
        assert isinstance(model.api, RegistryModelAPI)
        api = model.api.inner
    bodies = wire(api)
    output = asyncio.run(model.generate([ChatMessageUser(content="hello")], tools=[TOOL]))
    [body] = bodies
    assert body["model"] == "qwen3.8-27b"
    assert [t["function"]["strict"] for t in body["tools"]] == [False]
    assert body["temperature"] == 1.0 and body["top_p"] == 0.95 and body["top_k"] == 20
    assert body["chat_template_kwargs"] == {"reasoning_effort": "medium"}
    message = output.message
    [reasoning] = [c for c in message.content if isinstance(c, ContentReasoning)]
    assert reasoning.reasoning == "I should read the board first." and reasoning.internal == "reasoning"
    assert message.tool_calls[0].function == "board_read" and message.tool_calls[0].arguments == {"latest": 3}
    assert output.usage.input_tokens == 100 and output.usage.input_tokens_cache_read == 900
    # the next call sends the reasoning back on the assistant message, under the field it came in, where vLLM hands it
    # to the chat template (which keeps every turn's thinking)
    tool_reply = ChatMessageTool(content="no posts", tool_call_id="call_1", function="board_read")
    asyncio.run(model.generate([ChatMessageUser(content="hello"), message, tool_reply], tools=[TOOL]))
    assistant = bodies[1]["messages"][1]
    assert assistant["role"] == "assistant" and assistant["reasoning"] == "I should read the board first."
    assert assistant["tool_calls"][0]["function"]["name"] == "board_read"


def test_the_driver_queues_c_and_d_first_and_passes_no_key(monkeypatch):
    from elicitation import drive_selfhost as driver
    assert driver.queue(2, 1) == [("C", 1), ("D", 1), ("C", 2), ("D", 2), ("A", 1), ("B", 1)]
    assert [driver.run_name(c, 1) for c in "CDAB"] == [
        "flipt_duo_qwen3.8-27b-fp8-selfhost_dont_pressure_s1",
        "flipt_duo_qwen3.8-27b-fp8-selfhost_all_dont_pressure_s1",
        "flipt_duo_qwen3.8-27b-fp8-selfhost_cheat_ambiguous_s1",
        "flipt_duo_qwen3.8-27b-fp8-selfhost_all_cheat_ambiguous_s1"]
    # C and A take elicitation.swe's own name; D and B name theirs, with access=all
    for cell in "CA":
        argv = driver.command(cell, 4)
        assert "--name" not in argv and "access=all" not in argv
        assert swe.default_name(ENTRY, 4, driver.CELLS[cell][1]) == driver.run_name(cell, 4)
    for cell in "DB":
        argv = driver.command(cell, 4)
        assert argv[argv.index("--name") + 1] == driver.run_name(cell, 4) and "access=all" in argv
    argv = driver.command("C", 1)
    assert {"call_cap=50", "cost_limit_usd=0.6", "time_limit_s=7200", "seed=1"} <= set(argv) and "--no-judge" in argv
    for name in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "HF_TOKEN", "AWS_SECRET_ACCESS_KEY", "GH_TOKEN"):
        monkeypatch.setenv(name, "secret-value-123456")
    env = driver.child_env("study_x", "http://127.0.0.1:8001/v1")
    assert not any(k in env for k in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "HF_TOKEN", "AWS_SECRET_ACCESS_KEY",
                                      "GH_TOKEN"))
    assert env["SELFHOST_API_KEY"] == driver.DUMMY_KEY and env["STUDY"] == "study_x"


def test_the_driver_round_robin_order_and_its_refusals():
    from elicitation import drive_selfhost as driver
    assert driver.round_robin("DABC", 2, 61) == [("D", 61), ("A", 61), ("B", 61), ("C", 61),
                                                  ("D", 62), ("A", 62), ("B", 62), ("C", 62)]
    for bad in ("", "DAX", "DD"):
        with pytest.raises(ValueError):
            driver.round_robin(bad, 1)
