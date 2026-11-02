"""The paid check that Claude's prompt cache is read through OpenRouter. Run only with the user's yes.

    zsh -lic 'cd <repo> && uv run python -m scripts.check_anthropic_cache --model claude-haiku-5-5-openrouter --yes'

Two calls to the model with the same 5,000-token history, the second one message longer, through Inspect's real
request path with src/models/openrouter_anthropic_cache.py installed. The first call should write the cache, the
second should read it. Exit 0 and print `caching_verified: <date>` (the line to add to the entry in
src/models/model_registry.yaml) when the second call's cache read covers at least half its input; exit 1 otherwise.
Cost: about 10,000 input tokens and 20 output tokens; under one cent for Haiku 5.5 ($0.10 per million input tokens).
Without --yes it prints the plan and spends nothing.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys

from inspect_ai.model import ChatMessageSystem, ChatMessageUser, GenerateConfig, get_model

from src.models import model_registry as models
from src.models import openrouter_anthropic_cache as cache

FILLER = " ".join(f"Fact {i}: the board holds post number {i} about the redis cache client." for i in range(400))


async def two_calls(name: str) -> tuple[dict, dict]:
    inspect_name = models.resolve(name)
    model = get_model(inspect_name, memoize=False, config=GenerateConfig(max_tokens=10),
                      **models.model_args(inspect_name))
    history = [ChatMessageSystem(content="You oversee a team. Answer in one word."),
               ChatMessageUser(content=FILLER)]
    first = await model.generate(history)
    second = await model.generate([*history, ChatMessageUser(content="One word: ready?")])
    return first.usage.model_dump(), second.usage.model_dump()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="claude-haiku-5-5-openrouter")
    parser.add_argument("--yes", action="store_true", help="spend the two calls (the user said yes)")
    args = parser.parse_args(argv)
    if not cache.is_claude_on_openrouter(args.model):
        print(f"refused: {args.model} is not a Claude model through OpenRouter")
        return 2
    print(f"== plan: two calls to {args.model}, about 10,000 input tokens in all, under one cent")
    if not args.yes:
        print("nothing sent: pass --yes once the user has said yes")
        return 2
    cache.install()
    first, second = asyncio.run(two_calls(args.model))
    print(f"== first call:  input {first.get('input_tokens')}, cache write {first.get('input_tokens_cache_write')}, "
          f"cache read {first.get('input_tokens_cache_read')}")
    print(f"== second call: input {second.get('input_tokens')}, cache write "
          f"{second.get('input_tokens_cache_write')}, cache read {second.get('input_tokens_cache_read')}")
    read = second.get("input_tokens_cache_read") or 0
    total = read + (second.get("input_tokens") or 0) + (second.get("input_tokens_cache_write") or 0)
    if total and read >= total / 2:
        print(f"PASS: the second call read {read} of {total} input tokens from the cache")
        print(f"caching_verified: {dt.date.today().isoformat()}")
        return 0
    print(f"FAIL: the second call read {read} of {total} input tokens from the cache")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
