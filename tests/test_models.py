"""src/configuration/models.yaml: every entry has a route Inspect provides, the key variables are the ones Inspect's
providers declare, CheatBench's entries are all covered, the defaults are entries, the judge's price follows the
registry, and the command line prints the registry."""
from __future__ import annotations

import os

import yaml
from inspect_ai._util.registry import registry_find
from inspect_ai.model._providers.anthropic import ANTHROPIC_API_KEY
from inspect_ai.model._providers.google import GEMINI_API_KEY, GOOGLE_API_KEY
from inspect_ai.model._providers.openai import OPENAI_API_KEY
from inspect_ai.model._providers.openrouter import OPENROUTER_API_KEY

from src.benchmark.judge import _cost_usd, judge_prices
from src.cli import main
from src.configuration import models, settings
from src.run import Refusal, require_provider_key

CHEATBENCH_MODELS = settings.CHEATBENCH / "configs" / "models.yaml"
STATUSES = {"default", "judge", "candidate", "legacy"}
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
    assert models.resolve(settings.DEFAULT_MODEL) == settings.DEFAULT_MODEL
    try:
        models.resolve("gemini")
    except ValueError as error:
        assert "models.yaml" in str(error)
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
    assert judge["status"] == "judge"
    judges = yaml.safe_load(settings.JUDGE_MODELS_CONFIG.read_text())
    assert judges[judge["judge_entry"]]["model"] == judge["inspect"]
    assert models.usd_per_million(settings.JUDGE_MODEL) == judge["usd_per_million"]
    assert models.usd_per_million("mockllm/model") is None
    assert isinstance(models.registry()["as_of"], type(yaml.safe_load("2026-10-27")))


def test_batch_prices_follow_the_route_discount():
    assert models.usd_per_million("gpt-6-sol", batch=True) == {"input": 1.0, "cached_input": 0.1, "output": 5.0}
    assert models.usd_per_million("claude-opus-5-5", batch=True)["output"] == 10.0
    assert models.usd_per_million("deepseek-v4-pro", batch=True) == models.usd_per_million("deepseek-v4-pro")


def test_the_judge_price_comes_from_the_judge_entry_model():
    # judge_prices takes the judge entry (src/benchmark/models.yaml) and prices its model string.
    assert judge_prices(settings.JUDGE_MODEL) == models.usd_per_million("openai/gpt-6-sol")
    assert judge_prices(settings.JUDGE_MODEL, batch=True) == models.usd_per_million("openai/gpt-6-sol", batch=True)
    prices = {"input": 2.0, "cached_input": 0.2, "output": 10.0}
    usage = {"input": 1_000_000, "cached": 500_000, "output": 100_000}
    assert _cost_usd(prices, usage) == 0.5 * 2.0 + 0.5 * 0.2 + 0.1 * 10.0
    assert _cost_usd(prices, {"input": 1_000_000, "output": 0}) == 2.0      # an old row without a cached count
    assert _cost_usd(None, usage) is None and _cost_usd(prices, None) is None


def test_the_models_command_prints_the_registry(capsys):
    assert main(["models"]) == 0
    out = capsys.readouterr().out
    assert all(name in out for name in models.entries()) and "DEEPSEEK_API_KEY" in out
    assert main(["models", "deepseek-v4-pro"]) == 0
    one = yaml.safe_load(capsys.readouterr().out)
    assert one == {"deepseek-v4-pro": models.entries()["deepseek-v4-pro"]}
    assert main(["models", "openai/gpt-6-sol"]) == 0 and "judge_entry: gpt-6-sol" in capsys.readouterr().out
    assert main(["models", "nope"]) == 2 and "unknown model" in capsys.readouterr().err


def test_require_provider_key_reads_the_registry(monkeypatch):
    # A private copy of the environment, so the exports the check makes never leak into other tests.
    monkeypatch.setattr(os, "environ", dict(os.environ))
    for name in ("DEEPSEEK_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "META_API_KEY", "META_BASE_URL"):
        os.environ.pop(name, None)
    require_provider_key("mockllm/model")
    try:
        require_provider_key("deepseek/deepseek-v4-pro")
    except Refusal as refusal:
        assert refusal.code == 1 and "DEEPSEEK_API_KEY" in str(refusal)
    else:
        raise AssertionError("a missing key must be refused")
    os.environ["DEEPSEEK_API_KEY"] = "k"
    require_provider_key("deepseek/deepseek-v4-pro")
    os.environ["GEMINI_API_KEY"] = "g"
    require_provider_key("google/gemini-3.8-flash")
    assert os.environ["GOOGLE_API_KEY"] == "g"
    os.environ["META_API_KEY"] = "m"
    require_provider_key("openai-api/meta/muse-spark-1.3")
    assert os.environ["META_BASE_URL"] == models.providers()["meta"]["base_url"]


def test_a_pinned_route_reaches_the_eval():
    # The entry's model_args and generate settings: OpenRouter's provider pin and the agents' generation settings.
    from src.configuration.swarm_config import PRESETS
    from src.run import agent_generate_config
    deepseek = models.resolve("deepseek-v4.1-flash-deepinfra")
    assert deepseek == "openrouter/deepseek/deepseek-v4.1-flash"
    assert models.model_args(deepseek) == {"provider": {"order": ["deepinfra"], "allow_fallbacks": False}}
    assert models.generate_args(deepseek) == {"reasoning_effort": "high", "parallel_tool_calls": False,
                                              "max_tokens": 16000}
    haiku = models.resolve("claude-haiku-5-5-openrouter")
    assert models.model_args(haiku) == {"provider": {"order": ["anthropic"], "allow_fallbacks": False}}
    assert models.generate_args(haiku)["reasoning_effort"] == "medium"
    config = PRESETS["default"]
    assert agent_generate_config(config, deepseek) == models.generate_args(deepseek)
    assert agent_generate_config(config.with_overrides(["batch_agents=true"]), deepseek)["batch"] is True
    # An entry without a pin, and a model outside the registry, pass nothing.
    assert models.model_args("openai/gpt-6-sol") == {} and models.generate_args("mockllm/model") == {}
    assert agent_generate_config(config, None) == {}


def test_luna_judges_through_openrouter():
    judges = yaml.safe_load(settings.JUDGE_MODELS_CONFIG.read_text())
    assert judges["gpt-6-luna"]["model"] == "openrouter/openai/gpt-6-luna"
    assert judges["gpt-6-luna"]["api_key_env"] == OPENROUTER_API_KEY
    assert models.entries()["gpt-6-luna-openrouter"]["judge_entry"] == "gpt-6-luna"
    assert judge_prices("gpt-6-luna") == models.usd_per_million("openrouter/openai/gpt-6-luna")


def test_an_openrouter_judge_schema_carries_a_description():
    # OpenRouter drops a json_schema whose description is null; other routes keep the request unchanged.
    from src.benchmark.judge import judge_spec, response_schema
    spec = judge_spec(settings.CHEATBENCH / "tasks" / "software_engineering", "software_engineering")
    assert response_schema(spec, "openrouter/openai/gpt-6-luna").description
    assert response_schema(spec, "openai/gpt-6-sol").description is None
    assert response_schema(spec).description is None


def test_openrouter_cache_marks_the_last_tool_result():
    # The turn rules' provider-only notices follow the last tool result, so that result is the breakpoint to keep.
    from inspect_ai.model._providers import openrouter

    from src.utils import openrouter_cache
    assert openrouter_cache.install() and openrouter_cache.install()
    request = {"tools": [{"type": "function", "function": {"name": "shell"}}], "messages": [
        {"role": "system", "content": "sys"}, {"role": "user", "content": "issue"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]}, {"role": "tool", "content": "out 1"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "2"}]}, {"role": "tool", "content": "out 2"},
        {"role": "user", "content": "[notice] the board changed"}]}
    openrouter._add_anthropic_cache_markers(request)
    messages = request["messages"]
    assert messages[5]["content"] == [{"type": "text", "text": "out 2", "cache_control": {"type": "ephemeral"}}]
    assert messages[3]["content"] == "out 1" and messages[6]["content"] == "[notice] the board changed"
    assert openrouter_cache._breakpoints(request) <= openrouter_cache.MAX_BREAKPOINTS
    # A request already at four breakpoints gets no fifth.
    full = {"tools": [], "messages": [{"role": "user", "content": [{"type": "text", "text": "x",
                                                                     "cache_control": {"type": "ephemeral"}}] * 4},
                                      {"role": "tool", "content": "out"}]}
    assert not openrouter_cache.mark_last_tool_result(full) and full["messages"][1]["content"] == "out"
