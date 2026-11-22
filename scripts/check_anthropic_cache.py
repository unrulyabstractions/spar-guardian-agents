"""The paid check that Claude's prompt cache is read through OpenRouter under the installed Inspect. Run only with the
user's yes.

    zsh -lic 'cd <repo> && uv run python -m scripts.check_anthropic_cache --model claude-haiku-5-5-openrouter --yes'

Three calls through the swarm provider, the path a run takes with its OpenRouter routing pinned, shaped like an
agent's turns. The first history ends in a tool result that holds nearly all of its 2,400 tokens. The second call adds
a tool call and its result. The third adds the round line a new round opens with, then another tool call and result.
A stamp in the system prompt keeps each run of the check from reading an earlier run's cache.

Before anything is sent, each call's request is built offline (a fake OpenRouter answers) and must carry at most four
cache marks, one of them on the last message; otherwise the check is refused. Each later call passes when its cache
read covers at least 95% of the previous call's whole prompt. On a pass the check prints the `caching_verified` line
for the entry in src/models/model_registry.yaml, with the installed Inspect commit. Exit 0 on a pass, 1 on a fail, 2
when refused or without --yes, in which case nothing is sent.

Cost from registry prices: about $0.007 for Sonnet 5.5, $0.0004 for Haiku 5.5 and $0.014 for Opus 5.5.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys

import httpx
import openai
from inspect_ai.model import (
    ChatMessage,
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageTool,
    ChatMessageUser,
    GenerateConfig,
    get_model,
)
from inspect_ai.tool import ToolCall, ToolInfo, ToolParams

from src.models import model_registry as models
from src.models import openrouter_anthropic_cache as cache
from src.models import swarm_model_provider as provider  # importing it registers the "swarm" provider

FILLER = " ".join(f"Post {i}: the redis client now trusts the custom certificate in test {i}." for i in range(80))
TOOLS = [ToolInfo(name="board_read", description="Read the board.", parameters=ToolParams()),
         ToolInfo(name="bash", description="Run a shell command.", parameters=ToolParams())]
READ_SHARE = 0.95
FAKE_COMPLETION = {
    "id": "x", "object": "chat.completion", "created": 0, "model": "m",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}}


def swarm_model(name: str, max_tokens: int = 16):
    return get_model(provider.role_model(name), memoize=False, config=GenerateConfig(max_tokens=max_tokens))


def request_body(name: str, messages: list[ChatMessage], tools: list[ToolInfo] = TOOLS) -> dict:
    """The JSON body Inspect's real request code sends for these messages, read by a fake OpenRouter: no network."""
    bodies: list[dict] = []

    def answer(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=FAKE_COMPLETION)

    model = swarm_model(name)
    model.api.inner.client = openai.AsyncOpenAI(
        api_key="offline", base_url="https://openrouter.ai/api/v1", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    asyncio.run(model.generate(messages, tools=tools))
    return bodies[0]


def tool_turn(call_id: str, function: str, result: str) -> list[ChatMessage]:
    call = ToolCall(id=call_id, function=function, arguments={})
    return [ChatMessageAssistant(content="", tool_calls=[call]),
            ChatMessageTool(content=result, tool_call_id=call_id, function=function)]


def histories(stamp: str) -> list[list[ChatMessage]]:
    """The three calls' inputs; each starts with the whole previous input."""
    first = [ChatMessageSystem(content=f"You oversee a team. Answer in one word. Check {stamp}."),
             ChatMessageUser(content="Read the board, then answer."), *tool_turn("call_1", "board_read", FILLER)]
    second = [*first, *tool_turn("call_2", "bash", "ok")]
    third = [*second, ChatMessageUser(content="[Round 2 of 10] The board has one new post."),
             *tool_turn("call_3", "board_read", "Post 80: the fix is merged.")]
    return [first, second, third]


def shape_problem(body: dict) -> str | None:
    marks = cache.breakpoints(body)
    if len(marks) > cache.MAX_BREAKPOINTS:
        return f"{len(marks)} cache marks, Anthropic accepts {cache.MAX_BREAKPOINTS}: {marks}"
    if f"{body['messages'][-1]['role']} {len(body['messages']) - 1}" not in marks:
        return f"the last message carries no cache mark: {marks}"
    return None


async def paid_calls(name: str, calls: list[list[ChatMessage]]) -> list[dict]:
    model = swarm_model(name)
    return [(await model.generate(messages, tools=TOOLS)).usage.model_dump() for messages in calls]


def prompt_tokens(usage: dict) -> int:
    return sum(usage.get(k) or 0 for k in ("input_tokens", "input_tokens_cache_write", "input_tokens_cache_read"))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="claude-haiku-5-5-openrouter")
    parser.add_argument("--yes", action="store_true", help="spend the three calls (the user said yes)")
    args = parser.parse_args(argv)
    if not cache.is_claude_on_openrouter(args.model):
        print(f"refused: {args.model} is not a Claude model through OpenRouter")
        return 2
    calls = histories(dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    for k, messages in enumerate(calls, 1):
        body = request_body(args.model, messages)
        print(f"== call {k} marks: {cache.breakpoints(body)}")
        if problem := shape_problem(body):
            print(f"refused: call {k} has {problem}")
            return 2
    print(f"== plan: three calls to {args.model} under Inspect {cache.installed_inspect()}, about 7,000 input tokens")
    if not args.yes:
        print("nothing sent: pass --yes once the user has said yes")
        return 2
    usages = asyncio.run(paid_calls(args.model, calls))
    prices = models.usd_per_million(models.resolve(args.model))
    ok = True
    for k, usage in enumerate(usages, 1):
        read, write = usage.get("input_tokens_cache_read") or 0, usage.get("input_tokens_cache_write") or 0
        cost = models.cost_usd(prices, prompt_tokens(usage), read, usage.get("output_tokens") or 0, write)
        line = (f"== call {k}: prompt {prompt_tokens(usage)}, uncached {usage.get('input_tokens')}, cache write "
                f"{write}, cache read {read}, output {usage.get('output_tokens')}, ${cost:.5f}")
        if k > 1:
            previous = prompt_tokens(usages[k - 2])
            share = read / previous if previous else 0.0
            ok = ok and share >= READ_SHARE
            line += f", read {share:.4f} of call {k - 1}'s prompt"
        print(line)
    if not ok:
        print(f"FAIL: a later call read under {READ_SHARE:.0%} of the previous call's prompt from the cache")
        return 1
    print("PASS: every later call read the previous call's prompt from the cache")
    print(f'caching_verified: {{date: "{dt.date.today().isoformat()}", inspect: "{cache.installed_inspect()}"}}')
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
