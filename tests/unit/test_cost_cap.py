"""Every paid run has a dollar cap that Inspect enforces: each model's price reaches Inspect under every name it looks
the model up by, a sample stops once its calls pass the cap (through the swarm provider too), run.run passes the cap
to the eval, and a paid model without a price is refused before anything is spent."""
from __future__ import annotations

import pytest
from inspect_ai import Task
from inspect_ai import eval as inspect_eval
from inspect_ai.dataset import Sample
from inspect_ai.model import ModelCost, ModelInfo, ModelOutput, ModelUsage, get_model, set_model_info
from inspect_ai.model._model import compute_model_cost
from inspect_ai.model._model_info import _get_model_info_direct, clear_model_info_cache
from inspect_ai.solver import solver

from src import run
from src.configuration.presets import build_config
from src.domains.protein import ProteinDesign
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
    assert cost_cap.model_cost("claude-haiku-5-5-openrouter") == ModelCost(input=0.50, output=2.50,
                                                                           input_cache_read=0.05,
                                                                           input_cache_write=0.625)


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
        run.run("x", ProteinDesign(), config, model, None, task=object())
    return seen


def test_run_hands_the_eval_its_cap_and_says_so(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    seen = run_until_eval(monkeypatch, tmp_path, run.models.resolve("deepseek-flash-openrouter"),
                          ["regulators=1", "regulator_model=gpt-6-luna-openrouter", "cost_limit_usd=3"])
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
    assert run.run("x", ProteinDesign(), config, "openrouter/some-author/unpriced-model", None, task=object()) == 2
    assert "no price" in capsys.readouterr().err


def test_run_refuses_claude_through_openrouter_before_the_eval_until_its_cache_is_proven(monkeypatch, tmp_path,
                                                                                          capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    monkeypatch.setattr(run.inspect_ai, "eval", lambda *a, **k: (_ for _ in ()).throw(AssertionError("eval ran")))
    monkeypatch.setattr(run.settings, "LOGS", tmp_path)
    unproven = {k: v for k, v in run.models.entry_of(OPUS).items() if k != "caching_verified"}
    entry_of = run.models.entry_of
    monkeypatch.setattr(run.models, "entry_of", lambda name: unproven if "opus" in str(name) else entry_of(name))
    config = build_config("default", "small", ["judge=none", "regulators=1", f"regulator_model={OPUS}"])
    assert run.run("x", ProteinDesign(), config, run.models.resolve("deepseek-flash-openrouter"), None,
                   task=object()) == 2
    assert "refused until scripts/check_anthropic_cache.py" in capsys.readouterr().err


# Real calls of the seed pilot (logs/ian_seed_prototyping_0), of a Haiku run of 2026-11-02 and, for DeepSeek Flash,
# of the live check of DeepInfra's fp8 endpoint on 2026-11-23 (the host its routing pins since), with the usage as
# the log or the response kept it and what OpenRouter billed for the call (the raw response's usage.cost). The rows
# after Haiku's come from the live checks of 2026-11-23 through get_model with each entry's routing, Inspect's usage
# beside the raw response's usage.cost.
REAL_CALLS = [
    # entry, input, cache read, cache write, output, billed, what Inspect counted before price_calls
    ("gpt-6-luna-openrouter", 3, 1793, 262, 105, 0.00010348, 0.00018071),        # swe_luna_none_s1
    ("gpt-6-luna-openrouter", 3, 231014, 65568, 65536, 0.07016488, 0.07016488),  # creative_luna_pressure_s1, 296,585
    ("gpt-6-luna-openrouter", 3, 296582, 65659, 35, 0.02237324, 0.02237324),     # the same run, 362,244 tokens
    ("deepseek-flash-openrouter", 3419, 0, 0, 14, 0.000398785, 0.000398785),     # a first call on Cohere: no cache
    ("deepseek-flash-openrouter", 91, 3328, 0, 20, 0.000026785, 0.000026785),    # its repeat: the prefix read
    ("claude-haiku-5-5-openrouter", 4, 1094, 1811, 170, 0.000322715, 0.001613575),  # flipt_minimal_haiku_fixed1
    ("hy4-flash-openrouter", 14611, 0, 0, 32, 0.000515881, 0.000515881),       # a first call: nothing cached
    ("hy4-flash-openrouter", 147, 14464, 0, 32, 0.000031337, 0.000031337),     # its repeat: the prefix read
    ("qwen3.8-flash-openrouter", 820, 13312, 0, 32, 0.000351032, 0.000351032),  # a repeat
    ("muse-ember-openrouter", 33, 14324, 0, 32, 0.000076596, 0.000076596),     # a repeat
    ("gpt-6.1-terra-openrouter", 3, 0, 4355, 5, 0.00547175, 0.010931),         # a first call: the prefix written
    ("gpt-6.1-terra-openrouter", 3, 4355, 0, 5, 0.00024575, 0.000479),         # its repeat: the prefix read
    ("claude-opus-5-5-openrouter", 392, 0, 0, 49, 0.002548, 0.002548),         # a tool call
    ("grok-4.7-openrouter", 43, 5632, 0, 235, 0.004312, 0.008624),             # a repeat with x-session-id
    ("kimi-k3-openrouter", 15241, 0, 0, 29, 0.046158, 0.046158),               # the first call on moonshotai
    ("kimi-k3-openrouter", 137, 15104, 0, 32, 0.0054222, 0.0054222),           # a repeat: the cache read
]


def real_usage(inp: int, read: int, write: int, out: int) -> ModelUsage:
    return ModelUsage(input_tokens=inp, input_tokens_cache_read=read, input_tokens_cache_write=write or None,
                      output_tokens=out, total_tokens=inp + read + write + out)


@pytest.mark.parametrize("entry,inp,read,write,out,billed,before", REAL_CALLS)
def test_each_real_call_is_priced_at_what_openrouter_billed(entry, inp, read, write, out, billed, before):
    usage = real_usage(inp, read, write, out)
    name = run.models.resolve(entry)
    assert compute_model_cost(cost_cap.call_cost(name, usage), usage) == pytest.approx(billed, rel=1e-6)
    # the fallback price, which priced every call until 2026-11-22: the long-prompt rates below the line too
    assert compute_model_cost(cost_cap.model_cost(name), usage) == pytest.approx(before, rel=1e-6)


# The three Luna SWE runs' token totals (every call under 272k: the largest prompt was 48,967 tokens), what OpenRouter
# billed, and what Inspect counted toward the cap before price_calls.
LUNA_RUNS = [
    ("swe_luna_none_s1", 8034, 3673822, 123466, 35675, 0.070812, 0.132706),
    ("swe_luna_pressure_s1", 1635, 4233795, 130344, 39161, 0.078375, 0.146960),
    ("swe_luna_pointer_s1", 2460, 2875910, 105538, 31178, 0.057786, 0.107778),
]


@pytest.mark.parametrize("run_name,inp,read,write,out,billed,before", LUNA_RUNS)
def test_a_luna_run_below_the_line_counts_what_was_billed_not_1_87_times_it(run_name, inp, read, write, out, billed,
                                                                           before):
    name = run.models.resolve("gpt-6-luna-openrouter")
    one_call = real_usage(3, 1793, 262, 105)     # any call under the line has the run's rates
    totals = real_usage(inp, read, write, out)
    assert compute_model_cost(cost_cap.call_cost(name, one_call), totals) == pytest.approx(billed, rel=1e-4)
    assert compute_model_cost(cost_cap.model_cost(name), totals) == pytest.approx(before, rel=1e-4)


@pytest.fixture
def mock_at_luna_prices(monkeypatch):
    """mockllm/model priced as GPT-6 Luna through OpenRouter, so a mock eval runs the real pricing path."""
    real = cost_cap.models.usd_per_million
    monkeypatch.setattr(cost_cap.models, "usd_per_million",
                        lambda name, batch=False: real("gpt-6-luna-openrouter" if "mockllm" in name else name, batch))


def luna_outputs(n: int) -> list[ModelOutput]:
    """n outputs alternating the real Luna call under the line and the real one over it."""
    calls = [c for c in REAL_CALLS if c[0] == "gpt-6-luna-openrouter"][:2]
    outputs = []
    for i in range(n):
        output = ModelOutput.from_content("mockllm/model", "ok")
        output.usage = real_usage(*calls[i % 2][1:5])
        outputs.append(output)
    return outputs


@pytest.mark.parametrize("swarm", [False, True])
def test_inspect_counts_each_call_of_an_eval_at_its_billed_rate(tmp_path, mock_at_luna_prices, swarm):
    # the eval's own model (hooked by price_calls, as run.run does) and a role model through the swarm provider
    if swarm:
        caller = get_model("swarm/mockllm/model", memoize=False, custom_outputs=luna_outputs(4))
        eval_model = get_model("mockllm/model", memoize=False)
    else:
        eval_model = cost_cap.price_calls(get_model("mockllm/model", memoize=False, custom_outputs=luna_outputs(4)))
        caller = None

    @solver
    def spend():
        async def solve(state, generate):
            for _ in range(4):
                await (caller or get_model()).generate("next")
            return state
        return solve

    [log] = inspect_eval(Task(dataset=[Sample(input="go")], solver=spend()), model=eval_model,
                         log_dir=str(tmp_path), display="none")
    billed = 2 * (REAL_CALLS[0][5] + REAL_CALLS[1][5])
    total = sum(u.total_cost or 0 for u in log.samples[0].model_usage.values())
    assert total == pytest.approx(billed, rel=1e-6)


def test_the_cap_stops_a_luna_sample_at_its_billed_cost_not_early(tmp_path, mock_at_luna_prices):
    # OpenRouter billed $0.00010348 for this call under the line: a $0.0005 cap lets four pass ($0.000414) and stops
    # the fifth. At the long-prompt rates ($0.00018071 a call) the cap stopped the third.
    outputs = luna_outputs(20)[0::2]
    # Inspect refuses a cost_limit without model info; register's fallback is the higher rates, which the hook beats
    set_model_info("mockllm/model", ModelInfo(model="mockllm/model", cost=cost_cap.model_cost("mockllm/model")))
    eval_model = cost_cap.price_calls(get_model("mockllm/model", memoize=False, custom_outputs=outputs))
    calls: list[int] = []

    @solver
    def spend():
        async def solve(state, generate):
            for _ in range(10):
                await get_model().generate("next")
                calls.append(1)
            return state
        return solve

    [log] = inspect_eval(Task(dataset=[Sample(input="go")], solver=spend()), model=eval_model, cost_limit=0.0005,
                         log_dir=str(tmp_path), display="none")
    assert len(calls) == 4
    assert log.samples[0].limit is not None and log.samples[0].limit.type == "cost"


def test_price_calls_hooks_a_model_once_and_leaves_an_unpriced_or_swarm_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    luna = get_model(run.models.resolve("gpt-6-luna-openrouter"), memoize=False)
    hooked = cost_cap.price_calls(luna).api.served_model_usage
    assert cost_cap.price_calls(luna).api.served_model_usage is hooked
    output = ModelOutput.from_content("openai/gpt-6-luna", "ok")
    output.usage = real_usage(*REAL_CALLS[0][1:5])
    [part] = hooked(output)
    assert part.cost == ModelCost(input=0.10, output=0.50, input_cache_read=0.01, input_cache_write=0.125)
    mock = get_model("mockllm/model", memoize=False)
    assert "served_model_usage" not in vars(cost_cap.price_calls(mock).api)
    swarm_model = get_model("swarm/mockllm/model", memoize=False)
    assert "served_model_usage" not in vars(cost_cap.price_calls(swarm_model).api)
