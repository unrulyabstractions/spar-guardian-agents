"""The live pricer prices a run's summed usage at the registry's flat rates, under the plain and the swarm/ name, adds
a run's models together, and leaves an unpriced model out for the caller to name."""
from __future__ import annotations

import pytest

from scripts.box import live_cost
from src.models import model_registry as models

OPUS = models.resolve("claude-opus-5-5-openrouter")
LUNA = models.resolve("gpt-6-luna-openrouter")


@pytest.mark.parametrize("name", [OPUS, f"swarm/{OPUS}"])
def test_uncached_input_cache_reads_writes_and_output_each_take_their_rate(name):
    assert live_cost.price(name, 1_000_000, 0, 0, 0) == pytest.approx(4.0)
    assert live_cost.price(name, 0, 1_000_000, 0, 0) == pytest.approx(0.20)
    assert live_cost.price(name, 0, 0, 1_000_000, 0) == pytest.approx(5.0)
    assert live_cost.price(name, 0, 0, 0, 1_000_000) == pytest.approx(20.0)


def test_summed_usage_stays_at_the_flat_rate_past_a_long_prompt_line():
    assert live_cost.price(LUNA, 0, 10_000_000, 0, 0) == pytest.approx(0.10)


def test_a_run_s_models_add_up_and_an_unpriced_model_counts_nothing():
    rows = [{"box": "7", "run": "r1", "model": f"swarm/{OPUS}", "input": 1e6, "cache_read": 0, "cache_write": 0,
             "output": 0},
            {"box": "7", "run": "r1", "model": LUNA, "input": 0, "cache_read": 1e6, "cache_write": 0, "output": 0},
            {"box": "7", "run": "r2", "model": "openrouter/some/unpriced", "input": 1e6, "cache_read": 0,
             "cache_write": 0, "output": 0},
            {"box": "7", "run": "r3", "error": "locked"}]
    assert live_cost.price("openrouter/some/unpriced", 1, 0, 0, 0) is None
    assert live_cost.costs(rows) == pytest.approx({("7", "r1"): 4.01, ("7", "r2"): 0.0})
