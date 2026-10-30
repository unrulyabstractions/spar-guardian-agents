"""The export's usage totals: prompt tokens with cache reads, reasoning inside output, and what the provider billed
for the calls whose raw response the log kept. The stop-after-delivery facts: the run's delivery block, an episode's
first sighting and its ended_by."""
from __future__ import annotations

from types import SimpleNamespace

from src.export import export_episodes
from src.export import export_run_blocks as blocks
from src.export.eval_sample_view import billed_cost, usage_from_events


def event(input_tokens, cached, output, reasoning=0, cost=None):
    usage = SimpleNamespace(input_tokens=input_tokens, input_tokens_cache_read=cached, output_tokens=output,
                            reasoning_tokens=reasoning)
    response = {"usage": {"prompt_tokens": input_tokens + cached, "completion_tokens": output,
                          **({"cost": cost} if cost is not None else {})}}
    call = SimpleNamespace(request={}, response=response) if cost is not None else None
    return SimpleNamespace(output=SimpleNamespace(usage=usage), call=call)


def test_usage_totals_add_cache_reads_and_bill_only_the_calls_the_log_kept():
    events = [event(800, 0, 70, 0, 0.0005), event(300, 1024, 120, 40, 0.001), event(200, 2048, 90, 30)]
    assert usage_from_events(events) == {"input": 4372, "output": 280, "cache_read": 3072, "reasoning": 70,
                                         "billed_usd": 0.0015, "billed_calls": 2}
    assert usage_from_events([event(100, 0, 10)]) == {"input": 100, "output": 10, "cache_read": 0}
    assert usage_from_events([]) == {}
    assert billed_cost(SimpleNamespace(call=SimpleNamespace(request={}, response={"usage": {}}))) is None
    assert billed_cost(SimpleNamespace(call=None)) is None


def view(store: dict, limit=None) -> SimpleNamespace:
    return SimpleNamespace(store_get=store.get, metadata={}, scores={}, limit=limit, error=None)


DELIVERED = {"TurnState:agents": ["a01", "a02"], "TurnState:ended": {"a01": "delivered", "a02": "submitted"},
             "Delivered:after": 1,
             "Delivered:team": False, "Delivered:seen": {"a01": {"round": 2, "turn": 2}},
             "Delivered:halt_round": None, "Delivered:cut": [], "Delivered:checks": 3, "Delivered:errors": []}


def test_the_export_names_the_episodes_stop_after_delivery_ended():
    v = view(DELIVERED)
    block = {"after": 1, "team": False, "seen": {"a01": {"round": 2, "turn": 2}}, "halt_round": None, "cut": [],
             "checks": 3, "errors": [], "ended": ["a01"]}
    assert blocks.delivery_block(v) == block and blocks.recorded_run_blocks(v) == {"delivery": block}
    assert blocks.ended_by_delivery(v, "a01") and not blocks.ended_by_delivery(v, "a02")
    assert blocks.recorded_episode_facts(v, "a01") == {"delivery": {"round": 2, "turn": 2}}
    assert blocks.recorded_episode_facts(v, "a02") == {}
    # submit comes first; an episode the rule ended is "delivered" even when the sample later hit a limit
    assert export_episodes._ended_by(v, True, None, True) == "submit"
    assert export_episodes._ended_by(view(DELIVERED, limit="time"), False, "timeout", True) == "delivered"
    assert export_episodes._ended_by(view(DELIVERED, limit="time"), False, "timeout", False) == "limit"
    assert export_episodes._ended_by(v, False, None, False) == "end"
    # a run without the rule records no Delivered store and exports nothing new
    bare = view({"TurnState:agents": ["a01"], "TurnState:ended": {"a01": "submitted"}})
    assert blocks.delivery_block(bare) is None and not blocks.ended_by_delivery(bare, "a01")
    assert blocks.recorded_run_blocks(bare) == {} and blocks.recorded_episode_facts(bare, "a01") == {}
