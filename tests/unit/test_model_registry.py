"""src/models/model_registry.yaml: every entry has a route Inspect provides, the key variables are the ones Inspect's
providers declare, CheatBench's entries are all covered, the defaults are entries, the judge's price follows the
registry, and the command line prints the registry."""
from __future__ import annotations

import os

import pytest
import yaml
from inspect_ai._util.registry import registry_find
from inspect_ai.model._providers.anthropic import ANTHROPIC_API_KEY
from inspect_ai.model._providers.google import GEMINI_API_KEY, GOOGLE_API_KEY
from inspect_ai.model._providers.openai import OPENAI_API_KEY
from inspect_ai.model._providers.openrouter import OPENROUTER_API_KEY

from src.cli import main
from src.configuration import settings
from src.judging.cheatbench_judge import judge_prices
from src.models import model_registry as models
from src.run import Refusal, require_model

CHEATBENCH_MODELS = settings.CHEATBENCH / "configs" / "models.yaml"
STATUSES = {"default", "judge", "candidate", "superseded"}
# The variables Inspect's providers declare, any one of which suffices. The grok provider cannot be imported
# without the xAI SDK, so its two names are written out (inspect_ai/model/_providers/grok.py:90-92).
INSPECT_KEYS = {
    "google": [GOOGLE_API_KEY, GEMINI_API_KEY], "openai": [OPENAI_API_KEY], "anthropic": [ANTHROPIC_API_KEY],
    "xai": ["XAI_API_KEY", "GROK_API_KEY"], "openrouter": [OPENROUTER_API_KEY],
}


def inspect_provider_names() -> set[str]:
    return {info.name.split("/")[-1] for info in
            (o.__registry_info__ for o in registry_find(lambda info: info.type == "modelapi"))}


def test_every_provider_is_one_inspect_has_with_its_key_variables():
    for name, route in models.providers().items():
        prefix = route["inspect_prefix"]
        assert prefix.split("/")[0] in inspect_provider_names()
        if name in INSPECT_KEYS:
            assert route["key_env"] == INSPECT_KEYS[name]
        else:
            # An OpenAI-compatible service: Inspect derives the variables from the service name.
            service = prefix.split("/")[-1].upper().replace("-", "_")
            assert route["key_env"] == [f"{service}_API_KEY"] and route["base_url_env"] == f"{service}_BASE_URL"
        if prefix.startswith("openai-api/"):
            assert route["base_url"]


def test_every_entry_is_complete_and_resolves():
    assert models.entries()
    for name, entry in models.entries().items():
        assert entry["provider"] in models.providers(), name
        prefix = models.providers()[entry["provider"]]["inspect_prefix"]
        assert entry["inspect"].startswith(prefix + "/") and models.provider_of(entry["inspect"]) == entry["provider"]
        assert entry["status"] in STATUSES and isinstance(entry["context"], int) and entry["context"] > 0
        assert isinstance(entry["cheatbench"], list)
        price = entry["usd_per_million"]
        assert set(price) >= {"input", "output"} and all(isinstance(v, (int, float)) and v >= 0 for v in price.values())
        assert models.resolve(name) == entry["inspect"] and models.entry_name(entry["inspect"]) == name
        assert models.key_env(entry["inspect"]) == models.providers()[entry["provider"]]["key_env"]


def test_resolve_passes_inspect_strings_through_and_refuses_bare_names():
    assert models.resolve("mockllm/model") == "mockllm/model"
    assert models.provider_of("mockllm/model") is None and models.key_env("mockllm/model") == []
    assert models.resolve(settings.DEFAULT_MODEL) == "openrouter/deepseek/deepseek-v4.1-flash-1017"
    assert models.resolve("openrouter/x/y") == "openrouter/x/y"     # an Inspect string passes through
    try:
        models.resolve("gemini")
    except ValueError as error:
        assert "model_registry.yaml" in str(error)
    else:
        raise AssertionError("a bare name that is no entry must be refused")


def test_cheatbench_entries_are_each_covered_once_by_the_same_model():
    cheatbench = yaml.safe_load(CHEATBENCH_MODELS.read_text())
    covered = [cb for entry in models.entries().values() for cb in entry["cheatbench"]]
    assert sorted(covered) == sorted(cheatbench)
    for entry in models.entries().values():
        ours = entry["inspect"].split("/")[-1]
        for cb in entry["cheatbench"]:
            theirs = cheatbench[cb]["model"].split("/")[-1]
            assert ours.startswith(theirs), (entry["inspect"], cb, cheatbench[cb]["model"])


def test_the_defaults_are_entries_with_prices():
    agents = models.entry_of(settings.DEFAULT_MODEL)
    assert agents and agents["status"] == "default"
    assert sum(e["status"] == "default" for e in models.entries().values()) == 1
    judge = models.entries()[settings.JUDGE_MODEL]
    assert judge["status"] in ("judge", "default") and judge["judge_entry"] == settings.JUDGE_MODEL
    judges = yaml.safe_load(settings.JUDGE_MODELS_CONFIG.read_text())
    assert judges[judge["judge_entry"]]["model"] == judge["inspect"]
    assert {k: v for k, v in models.usd_per_million(settings.JUDGE_MODEL).items() if k != "long_prompt"} == \
        judge["usd_per_million"]
    assert models.usd_per_million("mockllm/model") is None
    assert isinstance(models.registry()["as_of"], type(yaml.safe_load("2026-10-27")))


def test_batch_prices_follow_the_route_discount():
    assert models.usd_per_million("gpt-5", batch=True) == {"input": 0.625, "cached_input": 0.0625, "output": 5.0}
    assert models.usd_per_million("claude-opus-5-5", batch=True)["output"] == 10.0
    assert models.usd_per_million("deepseek-v4-pro", batch=True) == models.usd_per_million("deepseek-v4-pro")


@pytest.mark.parametrize(("entry", "write"), [
    ("gpt-6-luna", 0.125), ("gpt-6-sol", 2.50), ("gpt-6.1-sol", 2.50), ("gpt-6.1-terra", 1.25), ("gpt-6-astra", 12.50),
    ("gpt-5.6-sol", 5.00), ("gpt-5.6-terra", 2.50), ("gpt-5.6-luna", 0.25)])
def test_openai_lists_a_write_rate_and_batch_halves_it(entry, write):
    """developers.openai.com/api/docs/pricing, the 30m write column: 1.25 times the input rate."""
    assert models.usd_per_million(entry)["cache_write"] == write == 1.25 * models.usd_per_million(entry)["input"]
    assert models.usd_per_million(entry, batch=True)["cache_write"] == write / 2
    assert models.usd_per_million(entry)["long_prompt"]["over"] == 272_000
    assert models.usd_per_million(entry, batch=True)["long_prompt"]["cache_write"] == write


def test_a_prompt_over_the_line_pays_the_long_rates_on_every_token():
    """Claude Haiku 5.5 over 100,000 prompt tokens: 0.50 in, 0.625 written, 0.05 read, 2.50 out. game runs held five
    such calls, up to 106,119 tokens. The line counts reads and writes, and a prompt exactly on it stays below."""
    prices = models.usd_per_million("claude-haiku-5-5")
    below = (100_000 - 90_000 - 1_000) * 0.10 + 90_000 * 0.01 + 1_000 * 0.125 + 2_000 * 0.50
    assert models.cost_usd(prices, 100_000, 90_000, 2_000, cache_write=1_000) == pytest.approx(below / 1e6)
    above = (100_001 - 90_000 - 1_000) * 0.50 + 90_000 * 0.05 + 1_000 * 0.625 + 2_000 * 2.50
    assert models.cost_usd(prices, 100_001, 90_000, 2_000, cache_write=1_000) == pytest.approx(above / 1e6)
    # a batch call pays half of them, and an entry with no line pays its one rate
    batch = models.usd_per_million("claude-haiku-5-5", batch=True)
    assert models.cost_usd(batch, 100_001, 90_000, 2_000, cache_write=1_000) == pytest.approx(above / 2e6)
    plain = models.usd_per_million("gpt-5")
    assert models.cost_usd(plain, 10**6, 0, 0) == 1.25
    # gpt-6-luna's line: the judge sent prompts up to 701,954 tokens
    luna = models.usd_per_million("gpt-6-luna")
    assert models.cost_usd(luna, 300_000, 0, 1_000) == pytest.approx((300_000 * 0.20 + 1_000 * 0.75) / 1e6)
    assert models.cost_usd(luna, 272_000, 0, 1_000) == pytest.approx((272_000 * 0.10 + 1_000 * 0.50) / 1e6)


def test_the_judge_price_comes_from_the_judge_entry_model():
    # judge_prices takes the judge entry (src/judging/judge_models.yaml) and prices its model string.
    assert judge_prices(settings.JUDGE_MODEL) == models.usd_per_million("openai/gpt-6-luna")
    assert judge_prices(settings.JUDGE_MODEL, batch=True) == models.usd_per_million("openai/gpt-6-luna", batch=True)
    assert judge_prices("gpt-6-sol") == models.usd_per_million("openai/gpt-6-sol")          # the reference judge
    assert judge_prices("claude-haiku-5-5") == models.usd_per_million("anthropic/claude-haiku-5-5")
    assert settings.JUDGE_MODEL == "gpt-6-luna"
    assert judge_prices("gpt-6-sol")["input"] == 20 * judge_prices(settings.JUDGE_MODEL)["input"]
    prices = {"input": 2.0, "cached_input": 0.2, "output": 10.0}
    assert models.cost_usd(prices, 1_000_000, 500_000, 100_000) == 0.5 * 2.0 + 0.5 * 0.2 + 0.1 * 10.0
    assert models.cost_usd({"input": 2.0, "output": 10.0}, 1_000_000, 0, 0) == 2.0     # no cached rate: the input rate
    assert models.cost_usd(None, 1, 0, 1) is None


def test_the_models_command_prints_the_registry(capsys):
    assert main(["models"]) == 0
    out = capsys.readouterr().out
    assert all(name in out for name in models.entries()) and "DEEPSEEK_API_KEY" in out
    assert main(["models", "deepseek-v4-pro"]) == 0
    one = yaml.safe_load(capsys.readouterr().out)
    assert one == {"deepseek-v4-pro": models.entries()["deepseek-v4-pro"]}
    assert main(["models", "openai/gpt-6-sol"]) == 0 and "judge_entry: gpt-6-sol" in capsys.readouterr().out
    assert main(["models", "nope"]) == 2 and "unknown model" in capsys.readouterr().err


def test_every_openrouter_entry_pins_one_host_and_the_runner_refuses_an_unpinned_one():
    for name, entry in models.entries().items():
        if entry["provider"] == "openrouter":
            pin = entry.get("routing")
            assert pin and len(pin["order"]) == 1 and pin["allow_fallbacks"] is False, name
            assert models.model_args(name) == {"provider": pin} == models.model_args(entry["inspect"])
        else:
            assert "routing" not in entry and models.model_args(name) == {}, name
    assert models.routing("mockllm/model") is None and models.model_args("openai/gpt-6-luna") == {}
    models.check_routing("openrouter/deepseek/deepseek-v4.1-flash-1017")
    models.check_routing("openai/gpt-6-luna")
    with pytest.raises(ValueError, match="no `routing`"):
        models.check_routing("openrouter/some-author/some-model")


def test_prepare_env_reads_the_registry(monkeypatch):
    # A private copy of the environment, so the exports the check makes never leak into other tests.
    monkeypatch.setattr(os, "environ", dict(os.environ))
    for name in ("DEEPSEEK_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "META_API_KEY", "META_BASE_URL",
                 "OPENROUTER_API_KEY"):
        os.environ.pop(name, None)
    models.prepare_env("mockllm/model")
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        models.prepare_env("deepseek/deepseek-v4-pro")
    os.environ["DEEPSEEK_API_KEY"] = "k"
    models.prepare_env("deepseek/deepseek-v4-pro")
    os.environ["GEMINI_API_KEY"] = "g"
    models.prepare_env("google/gemini-3.8-flash")
    assert os.environ["GOOGLE_API_KEY"] == "g"
    os.environ["META_API_KEY"] = "m"
    models.prepare_env("openai-api/meta/muse-spark-1.3")
    assert os.environ["META_BASE_URL"] == models.providers()["meta"]["base_url"]
    # A run refuses with exit code 1 for a missing key or an unpinned OpenRouter model.
    require_model("mockllm/model")
    with pytest.raises(Refusal, match="OPENROUTER_API_KEY") as refusal:
        require_model("openrouter/deepseek/deepseek-v4.1-flash-1017")
    assert refusal.value.code == 1
    os.environ["OPENROUTER_API_KEY"] = "o"
    require_model("openrouter/deepseek/deepseek-v4.1-flash-1017")
    with pytest.raises(Refusal, match="no `routing`"):
        require_model("openrouter/some-author/some-model")
