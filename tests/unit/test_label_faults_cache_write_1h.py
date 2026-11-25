"""A cache write at the one-hour lifetime costs its own rate in the ledger, checked on real calls of
disregulator_existence_2 (Claude straight from Anthropic).

- After a gap of more than five minutes in a sample Inspect asks Anthropic for the one-hour cache lifetime, and those
  writes cost 1.6 times the five-minute rate (Fable 5.2: 16.00 against 10.00 per million). The raw response splits
  each call's writes (usage.cache_creation.ephemeral_5m_input_tokens and ephemeral_1h_input_tokens), and Inspect
  counts the dearer rate against the cost cap.
- The ledger priced every write at the five-minute rate: fable baseline s1 stored $8.457082 where Inspect counted
  $9.742085, the 214,167 one-hour write tokens at $6 per million more. On opus baseline s1 the first one-hour call
  comes eight minutes after the call before it.

cache_write_1h.json holds each call's usage, its cache_creation split and Inspect's total_cost: every call of fable s1
and three calls of opus s1 around its first one-hour write. No prompt or reply text, no model call."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from inspect_ai.model import ModelUsage
from inspect_ai.model._model import CACHE_WRITE_1H_MULTIPLIER, compute_model_cost

from src.export import _export_run_blocks as blocks
from src.export.eval_sample_view import cache_write_1h, usage_dict
from src.models import cost_cap
from src.models import model_registry as models

FIXTURES = Path(__file__).resolve().parents[1] / "support" / "label_faults"
REAL = json.loads((FIXTURES / "cache_write_1h.json").read_text())
USAGE = ("input_tokens", "input_tokens_cache_read", "input_tokens_cache_write", "output_tokens")


def usage(call: dict) -> ModelUsage:
    return ModelUsage(**{k: call[k] for k in USAGE})


def event(call: dict) -> SimpleNamespace:
    """A model event as the eval log keeps it: the usage, and the raw response's usage with the write split."""
    response = {"usage": {"cache_creation": call["cache_creation"]}}
    return SimpleNamespace(output=SimpleNamespace(usage=usage(call)), call=SimpleNamespace(response=response))


def test_the_ledger_prices_a_one_hour_write_at_its_own_rate_as_inspect_counted():
    run = REAL["fable_s1"]
    events = [event(c) for c in run["calls"]]
    totals = {k: sum(usage_dict(e.output.usage)[k] for e in events) for k in ("input", "cache_read", "cache_write",
                                                                            "output")}
    rec = blocks.ledger_record(run["model"], events, totals)
    inspect_total = sum(c["total_cost"] for c in run["calls"])
    assert rec["cost_usd"] == round(inspect_total, 6) == 9.742085
    hour = sum(c["cache_creation"]["ephemeral_1h_input_tokens"] for c in run["calls"])
    assert hour == 214_167
    assert rec["cost_usd"] - run["stored_ledger_usd"] == pytest.approx(hour * (16.00 - 10.00) / 1e6, abs=1e-5)


@pytest.mark.parametrize("name", ["fable_s1", "opus_s1"])
def test_each_real_call_costs_what_the_cap_counted(name):
    run = REAL[name]
    entry = models.entry_name(run["model"])
    for call in run["calls"]:
        hour = cache_write_1h(event(call))
        tokens = usage_dict(usage(call))
        ledger = models.cost_usd(models.usd_per_million(entry), tokens["input"], tokens["cache_read"],
                                 tokens["output"], cache_write=tokens["cache_write"], cache_write_1h=hour)
        cap = compute_model_cost(cost_cap.call_cost(entry, usage(call)), usage(call), "1h" if hour else None)
        assert ledger == pytest.approx(call["total_cost"], abs=1e-9) and cap == pytest.approx(call["total_cost"],
                                                                                              abs=1e-9)


def test_opus_writes_at_one_hour_after_an_eight_minute_gap():
    before, first, _ = REAL["opus_s1"]["calls"]
    assert cache_write_1h(event(before)) == 0 and cache_write_1h(event(first)) == 13_232
    gap = datetime.fromisoformat(first["time"]) - datetime.fromisoformat(before["time"])
    assert gap.total_seconds() > 300


def test_every_one_hour_rate_is_what_inspect_bills_against_the_cap():
    """Inspect bills a one-hour write at CACHE_WRITE_1H_MULTIPLIER times the five-minute rate, in every tier, so the
    registry's cache_write_1h must be that product. Every Anthropic entry with a write rate has one."""
    for name, entry in models.entries().items():
        tiers = [entry["usd_per_million"], *([entry["long_prompt"]] if entry.get("long_prompt") else [])]
        for tier in tiers:
            if "cache_write_1h" in tier:
                assert tier["cache_write_1h"] == pytest.approx(tier["cache_write"] * CACHE_WRITE_1H_MULTIPLIER), name
            elif entry["provider"] == "anthropic" and "cache_write" in tier:
                pytest.fail(f"{name}: an Anthropic write rate without its one-hour rate")


def test_a_call_without_a_kept_response_or_a_split_writes_nothing_at_one_hour():
    assert cache_write_1h(SimpleNamespace(output=None)) == 0
    assert cache_write_1h(SimpleNamespace(call=SimpleNamespace(response={"usage": {"cost": 0.01}}))) == 0
    assert cache_write_1h(SimpleNamespace(call=SimpleNamespace(response=None))) == 0
