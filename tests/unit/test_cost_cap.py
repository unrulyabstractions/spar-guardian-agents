"""Every paid run has a dollar cap that Inspect enforces: each model's price reaches Inspect under every name it looks
the model up by, a sample stops once its calls pass the cap (through the swarm provider too), run.run passes the cap
to the eval, and a paid model without a price is refused before anything is spent."""
from __future__ import annotations

import pytest
from inspect_ai import Task
from inspect_ai import eval as inspect_eval
from inspect_ai.dataset import Sample
from inspect_ai.model import ModelCost, ModelInfo, ModelOutput, ModelUsage, get_model, set_model_info
from inspect_ai.model._model_info import _get_model_info_direct, clear_model_info_cache
from inspect_ai.solver import solver

from src import run
from src.configuration.presets import build_config
from src.domains.openmath import OpenMath
from src.models import (
    cost_cap,
    swarm_model_provider,  # noqa: F401 - registers the swarm/ provider
)

OPUS = "claude-opus-5-5-openrouter"


@pytest.fixture(autouse=True)
def fresh_prices():
    clear_model_info_cache()
    yield
    clear_model_info_cache()


def test_registry_prices_become_inspect_costs_and_a_long_prompt_tier_takes_its_higher_rates():
    assert cost_cap.model_cost(OPUS) == ModelCost(input=4.0, output=20.0, input_cache_read=0.20,
                                                  input_cache_write=5.0)
    assert cost_cap.model_cost("gpt-6-luna-openrouter") == ModelCost(input=0.20, output=0.75, input_cache_read=0.02,
                                                                     input_cache_write=0.25)


def test_a_real_swarm_model_and_its_plain_model_find_the_price(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    cost_cap.register([OPUS])
    for name in (f"swarm/{OPUS}", run.models.resolve(OPUS)):
        model = get_model(name, memoize=False)
        info = _get_model_info_direct(model)
        assert info is not None and info.cost == cost_cap.model_cost(OPUS), name


def test_cap_for_refuses_an_unpriced_paid_model_and_a_paid_run_without_a_cap():
    assert cost_cap.cap_for(["mockllm/model", None], 5.0) is None
    assert cost_cap.cap_for([OPUS, "mockllm/model"], 5.0) == 5.0
    with pytest.raises(cost_cap.UnpricedModel):
        cost_cap.cap_for(["openrouter/some-author/unpriced-model"], 5.0)
    for cap in (0, -1, None):
        with pytest.raises(ValueError):
            cost_cap.cap_for([OPUS], cap)


def test_the_cap_stops_a_sample_whose_calls_through_the_swarm_provider_pass_it(tmp_path):
    usage = ModelUsage(input_tokens=1_000_000, output_tokens=0, total_tokens=1_000_000)
    outputs = [ModelOutput.from_content("mockllm/model", "ok") for _ in range(20)]
    for output in outputs:
        output.usage = usage
    price = ModelCost(input=4.0, output=20.0, input_cache_read=0.2, input_cache_write=5.0)
    for name in ("mockllm/model", "swarm/mockllm/model"):
        set_model_info(name, ModelInfo(model=name, cost=price))
    regulator = get_model("swarm/mockllm/model", memoize=False, custom_outputs=outputs)
    calls: list[int] = []

    @solver
    def spend():
        async def solve(state, generate):
            for _ in range(20):
                await regulator.generate("next")
                calls.append(1)
            return state
        return solve

    [log] = inspect_eval(Task(dataset=[Sample(input="go")], solver=spend()), model="mockllm/model",
                         cost_limit=10.0, log_dir=str(tmp_path), display="none")
    assert len(calls) == 2                       # $4 a call: the third passes $10 and stops the sample
    assert log.samples[0].limit is not None and log.samples[0].limit.type == "cost"


class Started(Exception):
    """The eval stand-in stops the run here."""


def run_until_eval(monkeypatch, tmp_path, model: str, pairs: list[str]) -> dict:
    seen: dict = {}

    def eval_stub(task, **kwargs):
        seen.update(kwargs)
        raise Started

    monkeypatch.setattr(run.inspect_ai, "eval", eval_stub)
    monkeypatch.setattr(run.settings, "LOGS", tmp_path)
    monkeypatch.setattr(run, "access_line", lambda task: "== access")
    config = build_config("default", "small", ["judge=none", *pairs])
    with pytest.raises(Started):
        run.run("x", OpenMath(), config, model, None, task=object())
    return seen


def test_run_hands_the_eval_its_cap_and_says_so(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    seen = run_until_eval(monkeypatch, tmp_path, run.models.resolve("deepseek-flash-openrouter"),
                          ["regulators=1", f"regulator_model={OPUS}", "cost_limit_usd=3"])
    assert seen["cost_limit"] == 3.0
    assert "== cost cap $3.00 for the whole run" in capsys.readouterr().out
    seen = run_until_eval(monkeypatch, tmp_path, "mockllm/model", [])
    assert seen["cost_limit"] is None


def test_run_refuses_an_unpriced_model_before_the_eval(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    monkeypatch.setattr(run.inspect_ai, "eval", lambda *a, **k: (_ for _ in ()).throw(AssertionError("eval ran")))
    monkeypatch.setattr(run.settings, "LOGS", tmp_path)
    monkeypatch.setattr(run.models, "check_routing", lambda model: None)
    config = build_config("default", "small", ["judge=none"])
    assert run.run("x", OpenMath(), config, "openrouter/some-author/unpriced-model", None, task=object()) == 2
    assert "no price" in capsys.readouterr().err
