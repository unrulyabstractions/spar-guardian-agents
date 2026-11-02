"""A dollar cap on every paid run.

Inspect stops a sample once its cost passes the eval's cost_limit, and it prices each call from the model's cost.
Our OpenRouter models are not in Inspect's database, so without this module Inspect could price nothing and no cap
could trip. `register` gives Inspect each model's price from src/models/model_registry.yaml, under every name
Inspect looks it up by (the Inspect string and its `swarm/` form, the role models' provider). `cap_for` returns the
cap a run must pass to inspect_ai.eval: SwarmConfig.cost_limit_usd for a paid run, None for a run on mock models
alone. A paid model without a price is refused, since Inspect cannot cap what it cannot price.

Inspect takes one rate per model, so where an entry has a long-prompt tier the higher rates price every call. The cap
then trips early, never late: up to 2x on GPT-6 Luna (over 272k tokens) and up to 5x on Claude Haiku 5.5 (over 100k).

On 2026-11-01 eight regulator runs cost $176.56 in thirty minutes with no cap at all (VERIFICATION_LOG.md 21:45).
"""
from __future__ import annotations

from inspect_ai.model import ModelCost, ModelInfo, set_model_info

from src.models import model_registry as models

SWARM_PREFIX = "swarm/"
DEFAULT_CAP_USD = 5.0
"""Dollars per run unless the run sets cost_limit_usd. The median study-1 run cost $0.23; the dearest uncapped ones
cost $10 to $25."""


class UnpricedModel(ValueError):
    """A paid model with no price in the registry: Inspect could not cap its cost."""


def model_cost(name: str) -> ModelCost | None:
    """The model's price for Inspect, in USD per million tokens, from the registry; None without one."""
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
