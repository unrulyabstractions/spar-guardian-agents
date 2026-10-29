"""The swarm model provider: a registry model behind a bare model string, so ORBIT can run one role on another model.

    get_model("swarm/deepseek-flash-openrouter")      -> the entry's model, pinned to the host its `routing` names
    get_model("swarm/mockllm/model")                  -> Inspect's mock model
    set_agent_config(GenerateConfig(reasoning_effort="low"))
    role_model("openrouter/deepseek/deepseek-v4.1-flash-1017")   -> "swarm/openrouter/deepseek/deepseek-v4.1-flash-1017"
    inspect_string("swarm/mockllm/model")             -> "mockllm/model"

ORBIT builds an agent's model from AgentSpec.model with a bare get_model(spec.model). That call passes no model
arguments, so an OpenRouter string there would run unpinned. Inspect also merges the eval's generate config into the
eval's own model only. This provider handles both. `swarm/<entry or Inspect string>` builds the model the registry
names with its model arguments, its OpenRouter routing among them. Every call of that model carries the eval's
generate config and the agents' generate config (AGENT_CONFIG, set by run.evaluate) beneath the call's own settings.
The eval's config also reaches a role model under `inspect eval src/tasks.py@...`, where AGENT_CONFIG stays empty. An
OpenRouter model whose entry pins no host is refused (models.check_routing).

Importing this module registers the provider; src/run.py and src/tasks.py import it. An eval log names a role
model's calls "swarm/<Inspect string>", and inspect_string drops the prefix. Every public method of Inspect's ModelAPI
forwards to the inner model's API, and a GenerateConfig argument gets both configs beneath it. An attribute
ModelAPI does not define, such as OpenRouter's provider routing, reads through to the inner API. The eval log's
model events record the call's config before that merge, so they do not show those settings.
"""
from __future__ import annotations

import abc
import functools
import inspect
from typing import Any

from inspect_ai.model import GenerateConfig, ModelAPI, get_model, modelapi
from inspect_ai.model._generate_config import active_generate_config  # the eval's config: no public accessor

from src.configuration import model_registry as models

PROVIDER = "swarm"
PREFIX = f"{PROVIDER}/"

AGENT_CONFIG = GenerateConfig()
"""The agents' generate config (run.agent_generate_config), beneath every call of a swarm model."""


def set_agent_config(config: GenerateConfig) -> None:
    """The agents' generate config for every later call of a swarm model."""
    global AGENT_CONFIG
    AGENT_CONFIG = config


def role_model(model: str) -> str:
    """The AgentSpec model string that runs an Inspect model string through this provider."""
    return PREFIX + model


def inspect_string(model: str) -> str:
    """The Inspect model string behind a model name, without this provider's prefix."""
    return model.removeprefix(PREFIX)


def _beneath(value: Any) -> Any:
    """A GenerateConfig with the eval's generate config and then the agents' config beneath it; its own settings win.
    Any other value as it is."""
    if not isinstance(value, GenerateConfig):
        return value
    return active_generate_config().merge(AGENT_CONFIG).merge(value)


def _forwarder(name: str, method):
    """A method that calls the inner API's method of this name, an async one for an async method."""
    if inspect.iscoroutinefunction(method):
        async def forward(self, *args, **kwargs):
            return await getattr(self.inner, name)(*map(_beneath, args),
                                                   **{k: _beneath(v) for k, v in kwargs.items()})
    else:
        def forward(self, *args, **kwargs):
            return getattr(self.inner, name)(*map(_beneath, args), **{k: _beneath(v) for k, v in kwargs.items()})
    # the name and docstring only: an abstract method of a later Inspect must not stay abstract here
    return functools.wraps(method, updated=())(forward)


def forwarding(cls: type[ModelAPI]) -> type[ModelAPI]:
    """Give the class a forwarder for every public method of ModelAPI that it does not define itself."""
    cls.forwarded = sorted(name for name, member in vars(ModelAPI).items()
                           if not name.startswith("_") and callable(member) and name not in vars(cls))
    for name in cls.forwarded:
        setattr(cls, name, _forwarder(name, vars(ModelAPI)[name]))
    abc.update_abstractmethods(cls)
    return cls


@forwarding
class RegistryModelAPI(ModelAPI):
    """swarm/<entry or Inspect string>: the model the registry names, built with its model arguments (its OpenRouter
    routing) and the agents' generate config, behind a bare per-agent model string."""

    forwarded: list[str] = []
    """The ModelAPI methods this class forwards to the inner API, set by `forwarding`."""

    def __init__(self, model_name: str, base_url: str | None = None, api_key: str | None = None,
                 config: GenerateConfig = GenerateConfig(), **model_args: Any) -> None:
        inner_name = models.resolve(model_name)
        models.check_routing(inner_name)
        inner = get_model(inner_name, config=config, base_url=base_url, api_key=api_key, memoize=False,
                          **{**models.model_args(inner_name), **model_args})
        self.inner = inner.api
        super().__init__(model_name=inner_name, base_url=self.inner.base_url, api_key=self.inner.api_key,
                         api_key_vars=[], config=config)

    async def generate(self, input, tools, tool_choice, config: GenerateConfig):
        """The inner API's generate, with the eval's and the agents' config beneath the call's own."""
        return await self.inner.generate(input, tools, tool_choice, _beneath(config))

    def __getattr__(self, name: str) -> Any:
        # only for what the instance and its class lack; `inner` itself is looked up while it may not exist yet
        if name.startswith("__") or name == "inner":
            raise AttributeError(name)
        return getattr(self.inner, name)


@modelapi(name=PROVIDER)
def swarm() -> type[ModelAPI]:
    return RegistryModelAPI
