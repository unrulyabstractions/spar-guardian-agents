"""The belief questions asked from a study's setup, with no run: what each worker model believes about its task's
exploits before it acts.

    zsh -lic 'cd <repo> && uv run python -m scripts.study.belief_questions studies/plans/<study>.yaml --yes'
        [--only <target> ...] [--cost-limit 3]

For each worker model of the plan its target with the smallest team is taken, or the targets --only names. The
target's workers get their system prompt and first message as a run builds them, from the setup alone
(scripts/study/agent_texts.py: no run, no Docker). Each worker's model is then asked every belief question
(src/swarm/belief_probes.py, prompts.yaml beliefs) alone, on a copy of that opening, with tools off, through the route
a run takes. An influencer is not asked.

Paid: one short call per worker and question. Without --yes it prints the plan and sends nothing. The asking stops
before the team whose calls could pass --cost-limit (dollars at the registry's prices, 3 by default); a team's calls
are priced beforehand as its openings' characters at four per token plus MAX_REPLY_TOKENS a call.

The answers go to logs/<study>/belief_questions/<UTC stamp>.json, and the console gets each model's mean answer per
question, weakest model first.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

from inspect_ai.model import ChatMessageSystem, ChatMessageUser, get_model

from scripts.study import agent_texts, run_study
from src.configuration.settings import ROOT
from src.models import model_registry as models
from src.models import swarm_model_provider as provider
from src.models.cost_cap import prompt_tokens
from src.swarm import belief_probes
from src.swarm.awareness_probes import ask
from src.ui.transcript_index import MODELS, model_rank, short_model

MAX_REPLY_TOKENS = 4000
"""What one answer may cost in output tokens when a team's calls are priced beforehand, its thinking included."""


class Counted:
    """A model whose calls' token usage is kept, for the cost."""

    def __init__(self, model) -> None:
        self.model, self.usage = model, []

    async def generate(self, messages: list, tools: list):
        out = await self.model.generate(messages, tools=tools)
        self.usage.append(out.usage)
        return out


def model_key(entry: str) -> str:
    """A registry entry's model, the same for every route to it (transcript_index.short_model)."""
    return short_model(models.resolve(entry))


def chosen(plan: dict, only: list[str]) -> list[run_study.Cell]:
    """The cells to ask: the first seed of each target --only names, else of each worker model's smallest team."""
    first: dict[str, run_study.Cell] = {}
    for cell in run_study.cells(plan):
        first.setdefault(cell.target, cell)
    if only:
        missing = [target for target in only if target not in first]
        if missing:
            raise SystemExit(f"the plan has no target {missing[0]}")
        return [first[target] for target in only]
    smallest: dict[str, run_study.Cell] = {}
    for cell in first.values():
        key = model_key(cell.model)
        if key not in smallest or cell.n_agents < smallest[key].n_agents:
            smallest[key] = cell
    return sorted(smallest.values(), key=lambda cell: model_rank(model_key(cell.model)))


def openings(plan: dict, cell: run_study.Cell) -> tuple[dict[str, list], dict[str, str]]:
    """Each worker's opening, {agent: [system prompt, first message]}, and the questions' texts by key, from the
    cell's own config and benchmark."""
    config, benchmark, extra = agent_texts.cell_parts(cell.module, cell.argv(list(plan.get("settings", [])), False))
    texts = agent_texts.agent_texts(config, benchmark, extra)
    workers = {agent: [ChatMessageSystem(content=system), ChatMessageUser(content=first)]
               for agent, (role, system, first, _) in texts.items() if role == "worker"}
    return workers, belief_probes.questions(benchmark.belief_words())


def spent(entry: str, usage: list) -> float:
    """The calls' cost at the registry's prices."""
    prices = models.usd_per_million(entry)
    return sum(models.cost_usd(prices, prompt_tokens(u), u.input_tokens_cache_read or 0, u.output_tokens or 0,
                               u.input_tokens_cache_write or 0) for u in usage)


def at_most(entry: str, workers: dict[str, list], asked: dict[str, str]) -> float:
    """What the team's calls cost at most, priced beforehand: every prompt uncached at four characters a token, and
    MAX_REPLY_TOKENS of output a call."""
    prices = models.usd_per_million(entry)
    prompt = sum(len(m.text) for messages in workers.values() for m in messages) // 4 * len(asked)
    calls = len(workers) * len(asked)
    return models.cost_usd(prices, prompt + calls * 200, 0, calls * MAX_REPLY_TOKENS)


async def ask_team(entry: str, workers: dict[str, list], asked: dict[str, str]) -> tuple[dict, float]:
    """Every worker's answers, {agent: {question key: {answer, reason, raw, mode}}}, and the calls' cost."""
    models.prepare_env(entry)
    model = Counted(get_model(provider.role_model(entry), memoize=False, **models.model_args(entry)))

    async def one(context: list) -> dict:
        replies = await asyncio.gather(*(ask(context, text, model) for text in asked.values()))
        return {key: {**belief_probes.parse(raw), "raw": raw, "mode": mode}
                for key, (raw, mode) in zip(asked, replies, strict=True)}

    answers = await asyncio.gather(*(one(context) for context in workers.values()))
    return dict(zip(workers, answers, strict=True)), spent(entry, model.usage)


def table(rows: list[dict], asked: list[str]) -> str:
    """Each model's mean answer per question (the answers behind it in brackets), one line per model."""
    lines = [f"{'workers':22}" + "".join(f"{key:>22}" for key in asked)]
    for row in rows:
        cells = []
        for key in asked:
            values = [a[key]["answer"] for a in row["answers"].values() if a[key]["answer"] is not None]
            cells.append(f"{statistics.mean(values):.0f} {values}" if values else "no answer")
        lines.append(f"{MODELS.get(row['model'], row['model']):22}" + "".join(f"{cell:>22}" for cell in cells))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("plan", type=Path, help="a plan file, e.g. studies/plans/disregulator_existence_2.yaml")
    parser.add_argument("--only", nargs="+", default=[], metavar="TARGET", help="ask these targets' workers")
    parser.add_argument("--cost-limit", type=float, default=3.0, help="dollars the calls may cost in all")
    parser.add_argument("--yes", action="store_true", help="ask: the calls are paid")
    args = parser.parse_args(argv)
    plan = run_study.load_plan(args.plan)
    rows, total, asked = [], 0.0, {}
    for cell in chosen(plan, args.only):
        workers, asked = openings(plan, cell)
        most = at_most(cell.model, workers, asked)
        print(f"{cell.target}: {len(workers)} workers of {cell.model}, {len(asked)} questions each, "
              f"at most ${most:.2f}", flush=True)
        if not args.yes:
            continue
        if total + most > args.cost_limit:
            print(f"stopped before {cell.target}: ${total:.2f} spent and its calls could pass "
                  f"${args.cost_limit:.2f}", flush=True)
            break
        answers, cost = asyncio.run(ask_team(cell.model, workers, asked))
        total += cost
        rows.append({"target": cell.target, "entry": cell.model, "model": model_key(cell.model),
                     "cost_usd": round(cost, 4), "answers": answers})
    if not args.yes:
        print("nothing sent: --yes asks")
        return 2
    out = ROOT / "logs" / plan["study"] / "belief_questions" / time.strftime("%Y-%m-%dT%H%M%SZ.json", time.gmtime())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"plan": str(args.plan), "questions": asked, "teams": rows}, indent=1))
    print(table(rows, list(asked)))
    print(f"${total:.2f} at registry prices; answers in {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
