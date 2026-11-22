"""A dollar cap on every paid run.

Inspect stops a sample once its cost passes the eval's cost_limit, and it prices each call from the model's cost.
Our OpenRouter models are not in Inspect's database, so without this module Inspect could price nothing and no cap
could trip. `register` gives Inspect each model's price from src/models/model_registry.yaml, under every name
Inspect looks it up by (the Inspect string and its `swarm/` form, the role models' provider). `cap_for` returns the
cap a run must pass to inspect_ai.eval: SwarmConfig.cost_limit_usd for a paid run, None for a run on mock models
alone. A paid model without a price is refused, since Inspect cannot cap what it cannot price.

Inspect's model info holds one rate per model, but GPT-6 Luna (over 272k prompt tokens) and Claude Haiku 5.5 (over
100k) bill a call at a long-prompt rate past a line and at the normal rate below it. `price_calls` makes Inspect price
each call at the rate the provider bills it: it gives the model's API a `served_model_usage` that returns the call's
usage with the registry's rates for that call's prompt (ModelAPI.served_model_usage, the hook Inspect asks for a
call's rates). run.run hooks the agents' model and the judge; the swarm provider hooks every role model itself. The
cap then counts what OpenRouter bills, call for call (tests/unit/test_cost_cap.py checks real calls of the seed
pilot). The model info `register` sets is only the fallback for a model nothing hooked, and it takes the higher
rates, so such a cap trips early, never late.

Until 2026-11-22 nothing hooked any model, and the long-prompt rates priced every call: Inspect counted 1.87 times
what OpenRouter billed on a Luna run below the line (every token class at twice its rate, output at 1.5 times) and 5
times on Haiku, so a Luna run's $5 cap tripped once about $2.70 was spent.

On 2026-11-01 eight regulator runs cost $88.28 in thirty minutes with no cap at all (VERIFICATION_LOG.md 21:45 reported
$176.56, a double count).
"""
from __future__ import annotations

from inspect_ai.model import Model, ModelCost, ModelInfo, ModelOutput, ModelUsage, ServedModelUsage, set_model_info

from src.models import model_registry as models

SWARM_PREFIX = "swarm/"
DEFAULT_CAP_USD = 5.0
"""Dollars per run unless the run sets cost_limit_usd. The median study-1 run cost $0.23; the dearest uncapped ones
cost $10 to $25."""


class UnpricedModel(ValueError):
    """A paid model with no price in the registry: Inspect could not cap its cost."""


def as_model_cost(rates: dict) -> ModelCost:
    """Registry rates (usd_per_million, one tier) as Inspect's ModelCost: cache reads and writes at the input rate
    where the entry has no rate of their own."""
    return ModelCost(input=rates["input"], output=rates["output"],
                     input_cache_read=rates.get("cached_input", rates["input"]),
                     input_cache_write=rates.get("cache_write", rates["input"]))


def prompt_tokens(usage: ModelUsage) -> int:
    """A call's prompt as the provider counts it for its long-prompt line: Inspect's input tokens exclude the cache
    reads and writes, so they are added back."""
    return (usage.input_tokens or 0) + (usage.input_tokens_cache_read or 0) + (usage.input_tokens_cache_write or 0)


def call_cost(name: str, usage: ModelUsage, batch: bool = False) -> ModelCost | None:
    """The rates the provider bills this one call at: the registry's normal or long-prompt tier for the call's
    prompt, at batch prices with batch. None for a model without a price."""
    prices = models.usd_per_million(name, batch=batch)
    return as_model_cost(models.call_rates(prices, prompt_tokens(usage))) if prices else None


def served_at_call_rates(name: str, served: list[ServedModelUsage] | None, output: ModelOutput,
                         batch: bool = False) -> list[ServedModelUsage] | None:
    """What a hooked served_model_usage returns: the provider's own answer where another model served the call (a
    fallback or a router, which the registry's routing pins forbid), else the call's usage with call_cost's rates."""
    if served or output.usage is None:
        return served
    cost = call_cost(name, output.usage, batch)
    return None if cost is None else [ServedModelUsage(name, output.usage, cost)]


def price_calls(model: Model, batch: bool = False) -> Model:
    """Make Inspect price each call of this model at the rate its provider bills it (served_at_call_rates), on this
    instance. A model the registry does not price, or one whose API prices its own calls (the swarm provider), is
    left as it is. Returns the model."""
    api = model.api
    if getattr(api, "prices_its_calls", False):
        return model
    name = models.resolve(_bare(str(model)))
    if models.usd_per_million(name) is None:
        return model
    served = api.served_model_usage
    api.served_model_usage = lambda output: served_at_call_rates(name, served(output), output, batch)
    api.prices_its_calls = True
    return model


def model_cost(name: str) -> ModelCost | None:
    """The model's fallback price for Inspect's model info, in USD per million tokens, from the registry: each
    token class at the higher of its tiers' rates, for a model price_calls did not hook. None without a price."""
    prices = models.usd_per_million(name)
    if not prices:
        return None
    long = prices.get("long_prompt") or {}

    def rate(key: str, fallback: str) -> float:
        return max(prices.get(key, prices[fallback]), long.get(key, long.get(fallback, 0.0)))

    return ModelCost(input=rate("input", "input"), output=rate("output", "output"),
                     input_cache_read=rate("cached_input", "input"),
                     input_cache_write=rate("cache_write", "input"))


def is_paid(model: str) -> bool:
    """Whether calls to this model cost money: any model a registry provider serves (mockllm and the like do not)."""
    return models.provider_of(models.resolve(_bare(model))) is not None


def _bare(model: str) -> str:
    return model[len(SWARM_PREFIX):] if model.startswith(SWARM_PREFIX) else model


def lookup_names(model: str) -> list[str]:
    """Every name Inspect may look this model up by: its Inspect string and the `swarm/` form ORBIT's agents use."""
    inspect_string = models.resolve(_bare(model))
    return [inspect_string, SWARM_PREFIX + inspect_string]


def register(run_models: list[str]) -> None:
    """Give Inspect the price of every paid model of a run. Raises UnpricedModel for a paid model without a price."""
    for model in run_models:
        if not is_paid(model):
            continue
        cost = model_cost(models.resolve(_bare(model)))
        if cost is None:
            raise UnpricedModel(f"{model} has no price in src/models/model_registry.yaml: Inspect could not cap its "
                                "cost, so the run is refused")
        for name in lookup_names(model):
            set_model_info(name, ModelInfo(model=name, cost=cost))


def cap_for(run_models: list[str], cost_limit_usd: float | None) -> float | None:
    """The cost_limit for inspect_ai.eval: the run's cap when any model is paid, None for mock models alone. Registers
    the prices first. Raises ValueError for a paid run without a positive cap."""
    run_models = [m for m in run_models if m]
    register(run_models)
    if not any(is_paid(m) for m in run_models):
        return None
    if cost_limit_usd is None or cost_limit_usd <= 0:
        raise ValueError(f"a paid run needs a positive cost_limit_usd, not {cost_limit_usd!r}: every paid run has a "
                         "dollar cap (src/models/cost_cap.py)")
    return float(cost_limit_usd)
