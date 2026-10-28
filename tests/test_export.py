"""The export's usage totals: prompt tokens with cache reads, reasoning inside output, and what the provider billed
for the calls whose raw response the log kept."""
from __future__ import annotations

from types import SimpleNamespace

from src.benchmark.transcript import billed_cost, usage_from_events


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
