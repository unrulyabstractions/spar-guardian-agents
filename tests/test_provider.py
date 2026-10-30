"""The swarm model provider and the role models: a role's own model runs pinned to its host and with the eval's and
the agents' generate config, every method of Inspect's ModelAPI forwards to the inner model, the config fields hold
Inspect strings, the agent specs carry the provider's model string, the run checks each role model before the eval,
a probe asks each agent its own model, and the export names each episode's own model."""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from inspect_ai.model import GenerateConfig, ModelAPI, ModelOutput, get_model
from inspect_ai.model._generate_config import active_generate_config_context_var
from inspect_ai.model._providers.mockllm import MockLLM
from inspect_ai.model._providers.openrouter import OpenRouterAPI

from src import run
from src.configuration.swarm_config import PRESETS, ROLES, SwarmConfig, build_config
from src.domains.openmath import OpenMath
from src.export.eval_sample_view import SampleView
from src.export.run_export import episode_model
from src.models import model_registry as models
from src.models import swarm_model_provider as provider
from src.models.swarm_model_provider import RegistryModelAPI
from src.swarm import awareness_probes as probes
from src.tasks import agent_specs

FLASH = "openrouter/deepseek/deepseek-v4.1-flash-1017"
PUBLIC = sorted(name for name, member in vars(ModelAPI).items() if not name.startswith("_") and callable(member))


@pytest.fixture(autouse=True)
def empty_agent_config():
    """Every test starts and ends with an empty agents' config."""
    provider.set_agent_config(GenerateConfig())
    yield
    provider.set_agent_config(GenerateConfig())


def recorder(name: str, is_async: bool, seen: list):
    """A stand-in for one inner method: it records its arguments and returns its name."""
    if is_async:
        async def record(*args, **kwargs):
            seen.append((args, kwargs))
            return name
    else:
        def record(*args, **kwargs):
            seen.append((args, kwargs))
            return name
    return record


# ------------------------------------------------------------------------------------------------ the provider
def test_an_entry_runs_pinned_to_its_host(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    model = get_model("swarm/deepseek-flash-openrouter", memoize=False)
    assert isinstance(model.api, RegistryModelAPI) and isinstance(model.api.inner, OpenRouterAPI)
    assert model.api.inner.provider == models.routing("deepseek-flash-openrouter") and model.api.inner.provider
    assert model.api.provider == model.api.inner.provider      # an attribute ModelAPI lacks reads through
    assert str(model) == f"swarm/{FLASH}" and provider.inspect_string(str(model)) == FLASH
    # the entry's Inspect string builds the same pinned model
    assert get_model(f"swarm/{FLASH}", memoize=False).api.inner.provider == model.api.inner.provider


def test_an_unpinned_openrouter_model_and_a_wrong_name_are_refused(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    with pytest.raises(ValueError, match="no `routing`"):
        get_model("swarm/openrouter/some-author/some-model", memoize=False)
    with pytest.raises(ValueError, match="unknown model 'nope'"):
        get_model("swarm/nope", memoize=False)


def test_a_model_outside_the_registry_builds_as_it_is():
    model = get_model("swarm/mockllm/model", memoize=False)
    assert isinstance(model.api.inner, MockLLM) and str(model) == "swarm/mockllm/model"
    assert provider.role_model("mockllm/model") == "swarm/mockllm/model"
    assert asyncio.run(model.generate("hello")).completion == MockLLM.default_output


def test_every_public_method_of_model_api_is_forwarded():
    assert "generate" in PUBLIC and "max_connections" in PUBLIC
    assert RegistryModelAPI.forwarded == [name for name in PUBLIC if name != "generate"]
    api = get_model("swarm/mockllm/model", memoize=False).api
    for name in RegistryModelAPI.forwarded:
        is_async = inspect.iscoroutinefunction(vars(ModelAPI)[name])
        assert inspect.iscoroutinefunction(vars(RegistryModelAPI)[name]) == is_async, name
        seen: list = []
        setattr(api.inner, name, recorder(name, is_async, seen))
        result = getattr(api, name)("x", key="y")
        assert (asyncio.run(result) if is_async else result) == name, name
        assert seen == [(("x",), {"key": "y"})], (name, seen)


def test_generate_carries_the_agents_config_beneath_the_calls_own():
    provider.set_agent_config(GenerateConfig(reasoning_effort="low", max_tool_output=4096))
    model = get_model("swarm/mockllm/model", memoize=False)
    api = model.api
    seen: list = []

    async def generate(input, tools, tool_choice, config):
        seen.append(config)
        return ModelOutput.from_content("mockllm/model", "ok")

    api.inner.generate = generate
    asyncio.run(api.generate([], [], "auto", GenerateConfig()))
    asyncio.run(api.generate([], [], "auto", GenerateConfig(reasoning_effort="high")))
    assert asyncio.run(model.generate("hello")).completion == "ok"     # through Inspect's Model, as ORBIT calls it
    assert [c.reasoning_effort for c in seen] == ["low", "high", "low"] and seen[0].max_tool_output == 4096
    # every GenerateConfig argument of a forwarded method gets the same merge
    configs: list = []
    api.inner.max_tokens_for_config = lambda config: configs.append(config) or 77
    assert api.max_tokens_for_config(GenerateConfig()) == 77 and configs[0].reasoning_effort == "low"


def test_generate_carries_the_evals_config_beneath_the_agents():
    """Inspect merges the eval's generate config into the eval's model only, as under `inspect eval src/tasks.py@...`.
    The provider carries it to a role model too, beneath the agents' config and the call's own."""
    model = get_model("swarm/mockllm/model", memoize=False)
    seen: list = []

    async def generate(input, tools, tool_choice, config):
        seen.append(config)
        return ModelOutput.from_content("mockllm/model", "ok")

    model.api.inner.generate = generate
    token = active_generate_config_context_var.set(GenerateConfig(reasoning_effort="low", batch=True))
    try:
        asyncio.run(model.generate("hello"))
        provider.set_agent_config(GenerateConfig(reasoning_effort="medium"))
        asyncio.run(model.generate("hello"))
        asyncio.run(model.api.generate([], [], "auto", GenerateConfig(reasoning_effort="high")))
    finally:
        active_generate_config_context_var.reset(token)
    assert [(c.reasoning_effort, c.batch) for c in seen] == [("low", True), ("medium", True), ("high", True)]


# ------------------------------------------------------------------------------------------------ the config
def test_the_role_models_hold_inspect_strings():
    config = build_config("default", "small", ["regulator_model=deepseek-flash-openrouter"])
    assert config.regulator_model == FLASH and config.model_dump(mode="json")["regulator_model"] == FLASH
    assert config.role_models() == {"worker": None, "regulator": FLASH, "disregulator": None}
    assert list(config.role_models()) == list(ROLES)
    assert config.with_overrides(["regulator_model=none"]).regulator_model is None
    assert SwarmConfig(worker_model="mockllm/model").role_models()["worker"] == "mockllm/model"
    with pytest.raises(ValueError, match="unknown model 'nope'"):
        build_config("default", "small", ["disregulator_model=nope"])
    assert all(p.role_models() == dict.fromkeys(ROLES) for p in PRESETS.values())    # presets never set one


def test_the_agent_specs_carry_each_roles_model():
    config = build_config("default", "small", ["regulators=1", "disregulators=1",
                                               "regulator_model=deepseek-flash-openrouter"])
    tools = {name: [] for name in config.names}
    prompts = dict.fromkeys(config.names)
    roles = {"a01": "worker", "a02": "regulator", "a03": "disregulator"}
    specs = agent_specs(config, tools, prompts, roles=roles)
    assert {s.name: s.model for s in specs} == {"a01": None, "a02": f"swarm/{FLASH}", "a03": None}
    # roles None: every agent is a worker
    workers = config.replace(worker_model="mockllm/model", regulator_model=None)
    assert [s.model for s in agent_specs(workers, tools, prompts)] == ["swarm/mockllm/model"] * 3
    assert all(s.model is None for s in agent_specs(build_config("default", "small"), tools, prompts))


# ------------------------------------------------------------------------------------------------ the export
def view_of(cheatbench: dict, model: str = "mockllm/model") -> SampleView:
    return SampleView(id="riemann", epoch=1, messages=[], metadata={"cheatbench": cheatbench}, events=[],
                      store_get=lambda key: None, model=model, input="", output=None, scores=None, limit=None,
                      error=None, total_time=None)


def test_each_episode_names_its_agents_own_model():
    roles = {"a01": "worker", "a02": "regulator", "a03": "disregulator"}
    view = view_of({"roles": roles, "role_models": {"worker": None, "regulator": FLASH, "disregulator": None}})
    assert [episode_model(view, a) for a in roles] == ["mockllm/model", FLASH, "mockllm/model"]
    # no roles: every agent is a worker; a run before role models: the eval's model, without the provider's prefix
    assert episode_model(view_of({"role_models": {"worker": FLASH}}), "a01") == FLASH
    assert episode_model(view_of({"roles": roles}, model=f"swarm/{FLASH}"), "a02") == FLASH


# ------------------------------------------------------------------------------------------------ the probes
def test_a_probe_asks_each_agent_its_own_model(monkeypatch):
    roles = {"a01": "worker", "a02": "regulator", "a03": "disregulator"}
    state = SimpleNamespace(metadata={"cheatbench": {
        "roles": roles, "role_models": {"worker": None, "regulator": "mockllm/regulator", "disregulator": None}}})
    assert str(probes.agent_model(state, "a02")) == "swarm/mockllm/regulator"
    monkeypatch.setattr(probes, "get_model", lambda *args: args)
    # get_model() with no argument is the eval's model
    assert [probes.agent_model(state, a) for a in roles] == [(), ("swarm/mockllm/regulator",), ()]
    # no roles: every agent is a worker
    workers = SimpleNamespace(metadata={"cheatbench": {"role_models": {"worker": "mockllm/model"}}})
    assert probes.agent_model(workers, "a01") == ("swarm/mockllm/model",)


# ------------------------------------------------------------------------------------------------ the run
def forbidden_eval(*args, **kwargs):
    raise AssertionError("the eval started")


@pytest.mark.parametrize("pair, keyed, reason", [
    ("regulator_model=openrouter/some-author/some-model", True, "has no `routing`"),
    ("regulator_model=deepseek-flash-openrouter", False, f"must be set for {FLASH}"),
    ("regulator_model=gogle/gemini-3.8-flash", True,
     "role model gogle/gemini-3.8-flash: Model API gogle of model 'gogle/gemini-3.8-flash' not recognized"),
])
def test_evaluate_refuses_a_role_model_before_the_eval(monkeypatch, tmp_path, capsys, pair, keyed, reason):
    for name in models.key_env(FLASH):
        monkeypatch.delenv(name, raising=False)
    if keyed:
        monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    monkeypatch.setattr(run.inspect_ai, "eval", forbidden_eval)
    monkeypatch.setattr(run.settings, "LOGS", tmp_path)
    config = build_config("default", "small", ["regulators=1", pair])
    assert run.evaluate("x", OpenMath(), config, None, "mockllm/model", None) == 1
    assert reason in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_evaluate_prints_the_models_and_hands_the_agents_config_to_the_provider(monkeypatch, tmp_path, capsys):
    config = build_config("default", "small", ["regulators=1", "regulator_model=mockllm/regulator", "judge=none",
                                               "reasoning_effort=low", "tool_output_bytes=4096"])
    line = "== models worker=(run model) regulator=mockllm/regulator disregulator=(run model)"
    assert run.models_line(config) == line
    seen: dict = {}

    class Started(Exception):
        """The eval stand-in stops the run here."""

    def eval_stub(task, **kwargs):
        seen.update(kwargs, agent_config=provider.AGENT_CONFIG)
        raise Started

    monkeypatch.setattr(run.inspect_ai, "eval", eval_stub)
    monkeypatch.setattr(run.settings, "LOGS", tmp_path)
    monkeypatch.setattr(run.provider_retry, "install", lambda: True)
    monkeypatch.setattr(run, "access_line", lambda task: "== access")
    with pytest.raises(Started):
        run.evaluate("x", OpenMath(), config, None, "mockllm/model", None)
    assert seen["agent_config"] == GenerateConfig(**run.agent_generate_config(config))
    assert seen["agent_config"].reasoning_effort == seen["reasoning_effort"] == "low"
    assert seen["agent_config"].max_tool_output == seen["max_tool_output"] == 4096
    assert line in capsys.readouterr().out.splitlines()
