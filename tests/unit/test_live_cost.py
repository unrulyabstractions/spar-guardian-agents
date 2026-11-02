"""The live pricer prices a run's summed usage at the registry's flat rates, under the plain and the swarm/ name, adds
a run's models together, leaves an unpriced model out for the caller to name, and prices the calls over a model's
long-prompt line at that line's rates, which the box's script tells apart call by call."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys

import pytest

from scripts.box import live_cost
from src.models import model_registry as models

OPUS = models.resolve("claude-opus-5-5-openrouter")
LUNA = models.resolve("gpt-6-luna-openrouter")
HAIKU = models.resolve("claude-haiku-5-5-openrouter")


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


def test_calls_past_the_line_pay_the_long_rates_and_a_model_without_a_tier_refuses_the_flag():
    assert live_cost.price(HAIKU, 1_000_000, 0, 0, 0) == pytest.approx(0.10)
    assert live_cost.price(HAIKU, 1_000_000, 0, 0, 0, long=True) == pytest.approx(0.50)
    assert live_cost.price(HAIKU, 0, 1_000_000, 1_000_000, 0, long=True) == pytest.approx(0.05 + 0.625)
    assert live_cost.price(HAIKU, 0, 0, 0, 1_000_000, long=True) == pytest.approx(2.50)
    with pytest.raises(KeyError):
        live_cost.price(OPUS, 1, 0, 0, 0, long=True)


def call(model: str, prompt: int, read: int = 0, write: int = 0, output: int = 100, pending: bool = False) -> str:
    usage = {"input_tokens": prompt - read - write, "input_tokens_cache_read": read,
             "input_tokens_cache_write": write, "output_tokens": output}
    return json.dumps({"event": "model", "model": model, "pending": pending, "output": {"usage": usage}})


def test_the_box_script_splits_a_model_s_calls_at_its_line_by_the_whole_prompt(tmp_path):
    folder = tmp_path / "buffers" / "x"
    folder.mkdir(parents=True)
    db = sqlite3.connect(folder / "2026-11-02T10-00-00-00-00_flipt-gate_ABCDEFGHIJKLMNOPQRSTUV.eval.db")
    db.execute("create table events (data text)")
    calls = [call(f"swarm/{HAIKU}", 99_999), call(f"swarm/{HAIKU}", 100_000), call(HAIKU, 100_001, read=100_000),
             call(f"swarm/{HAIKU}", 150_000, read=140_000, write=9_000), call(OPUS, 500_000),
             call(HAIKU, 500_000, pending=True)]
    db.executemany("insert into events values (?)", [(data,) for data in calls])
    db.commit()
    db.close()
    out = subprocess.run([sys.executable, "-"], input=live_cost.remote_script(str(tmp_path / "buffers")),
                         capture_output=True, text=True, check=True)
    rows = {(row["model"], row["long"]): row for row in map(json.loads, out.stdout.splitlines())}
    assert {key: row["calls"] for key, row in rows.items()} == {
        (f"swarm/{HAIKU}", False): 2, (HAIKU, True): 1, (f"swarm/{HAIKU}", True): 1, (OPUS, False): 1}
    assert rows[(f"swarm/{HAIKU}", True)]["cache_write"] == 9_000
    assert {row["run"] for row in rows.values()} == {"flipt-gate"}
