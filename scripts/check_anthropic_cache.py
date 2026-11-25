"""The paid check that Claude's prompt cache is read, through OpenRouter or at Anthropic, under the installed Inspect.
Run only with the user's yes.

    zsh -lic 'cd <repo> && uv run python -m scripts.check_anthropic_cache --model claude-haiku-5-5-openrouter --yes'
    zsh -lic 'cd <repo> && uv run python -m scripts.check_anthropic_cache --model claude-haiku-5-5 --yes'

Three calls through the swarm provider, the path a run takes: an OpenRouter entry with its routing pinned, a direct
entry through Inspect's Anthropic provider, which places the cache marks itself. The calls are shaped like an agent's
turns. The first history ends in a tool result that holds nearly all of its 2,400 tokens. The second call adds a tool
call and its result. The third adds the round line a new round opens with, then another tool call and result. A stamp
in the system prompt keeps each run of the check from reading an earlier run's cache. A direct entry's key comes from
model_registry.prepare_env: the key file ~/.config/spar-guardian-agents/anthropic_key alone, and the check refuses
with --yes while that file is missing, so it never spends the ANTHROPIC_API_KEY of the environment. Without --yes the
key file is not read. The plan line names where the key comes from and whether every request carries the workspace
header (a workspace file beside the key file).

Before anything is sent, each call's request is built offline (a fake OpenRouter or Anthropic answers) and must carry
at most four cache marks and cover the last message (claude_prompt_cache.shape_problem); otherwise the check is
refused. Each later call passes when its cache read covers at least 95% of the previous call's whole prompt. On a pass
the check prints the `caching_verified` line for the entry in src/models/model_registry.yaml, with the installed
Inspect commit, the route and the entry (claude_prompt_cache.stamp). Exit 0 on a pass, 1 on a fail, 2 when refused or
without --yes, in which case nothing is sent.

Cost from registry prices, the same on both routes: about $0.014 for Opus 5.5, $0.007 for Sonnet 5.5 and $0.0004 for
Haiku 5.5.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys

import httpx
import httpx2
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

from src.models import claude_prompt_cache as cache
from src.models import model_registry as models
from src.models import swarm_model_provider as provider  # importing it registers the "swarm" provider

FILLER = " ".join(f"Post {i}: the redis client now trusts the custom certificate in test {i}." for i in range(80))
TOOLS = [ToolInfo(name="board_read", description="Read the board.", parameters=ToolParams()),
         ToolInfo(name="bash", description="Run a shell command.", parameters=ToolParams())]
READ_SHARE = 0.95
FAKE_COMPLETION = {
    "openrouter": {"id": "x", "object": "chat.completion", "created": 0, "model": "m",
                   "choices": [{"index": 0, "finish_reason": "stop",
                                "message": {"role": "assistant", "content": "ok"}}],
                   "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}},
    "anthropic": {"id": "msg_x", "type": "message", "role": "assistant", "model": "m",
                  "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "stop_sequence": None,
                  "usage": {"input_tokens": 10, "output_tokens": 1}}}
"""Each route's answer to an offline request."""


def swarm_model(name: str, max_tokens: int = 16, **model_args):
    return get_model(provider.role_model(name), memoize=False, config=GenerateConfig(max_tokens=max_tokens),
                     **model_args)


def offline_http_client(route: str, answer):
    """An HTTP client for the route's SDK whose every request `answer` handles: no network."""
    if route == "anthropic":
        return httpx2.AsyncClient(transport=httpx2.MockTransport(answer))
    return httpx.AsyncClient(transport=httpx.MockTransport(answer))


def offline_request(name: str, messages: list[ChatMessage], tools: list[ToolInfo] = TOOLS):
    """The request Inspect's real client sends for these messages, its headers included, answered by a fake
    OpenRouter or Anthropic: no network and no key."""
    route = cache.claude_route(name) or "openrouter"
    response = httpx2.Response if route == "anthropic" else httpx.Response
    requests: list = []

    def answer(request):
        requests.append(request)
        return response(200, json=FAKE_COMPLETION[route])

    model = swarm_model(name, api_key="offline", http_client=offline_http_client(route, answer))
    asyncio.run(model.generate(messages, tools=tools))
    return requests[0]


def request_body(name: str, messages: list[ChatMessage], tools: list[ToolInfo] = TOOLS) -> dict:
    """The JSON body of offline_request."""
    return json.loads(offline_request(name, messages, tools).content)


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


async def paid_calls(name: str, calls: list[list[ChatMessage]]) -> list[dict]:
    model = swarm_model(name)
    return [(await model.generate(messages, tools=TOOLS)).usage.model_dump() for messages in calls]


def prompt_tokens(usage: dict) -> int:
    return sum(usage.get(k) or 0 for k in ("input_tokens", "input_tokens_cache_write", "input_tokens_cache_read"))


def header_line(inspect_name: str) -> str:
    """Whether every request carries a workspace header (models.model_args), never its value."""
    provider_name = models.provider_of(inspect_name)
    headers = models.model_args(inspect_name).get("default_headers") or {}
    if not headers:
        return "no workspace header"
    return f"the header {', '.join(headers)} from {models.workspace_path(provider_name)}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="claude-haiku-5-5-openrouter")
    parser.add_argument("--yes", action="store_true", help="spend the three calls (the user said yes)")
    args = parser.parse_args(argv)
    route = cache.claude_route(args.model)
    if route is None:
        print(f"refused: {args.model} is not a Claude model of the registry")
        return 2
    inspect_name = models.resolve(args.model)
    try:
        headers = header_line(inspect_name)      # a bad workspace file is refused before any request is built
    except ValueError as error:
        print(f"refused: {error}")
        return 2
    calls = histories(dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    for k, messages in enumerate(calls, 1):
        body = request_body(args.model, messages)
        print(f"== call {k} marks: {cache.breakpoints(body)}")
        if problem := cache.shape_problem(body, route):
            print(f"refused: call {k} has {problem}")
            return 2
    print(f"== plan: three calls to {args.model} under Inspect {cache.installed_inspect()}, about 7,000 input tokens, "
          f"with the key from {models.key_source(inspect_name) or '(none set)'}; {headers}")
    if not args.yes:
        print("nothing sent: pass --yes once the user has said yes")
        return 2
    try:
        models.prepare_env(inspect_name)      # a provider's key file alone: refused while it is missing
    except ValueError as error:
        print(f"refused: {error}")
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
    print(cache.stamp(args.model, dt.date.today().isoformat()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
