"""The model registry src/models/model_registry.yaml: every model we can consider, and the name each route needs.

    resolve("deepseek-v4-pro")               -> "deepseek/deepseek-v4-pro"
    resolve("mockllm/model")                 -> "mockllm/model"
    provider_of("deepseek/deepseek-v4-pro")  -> "deepseek"
    key_env("deepseek/deepseek-v4-pro")      -> ["DEEPSEEK_API_KEY"]
    usd_per_million("gpt-6-sol")             -> {"input": 2.0, "cached_input": 0.2, "output": 10.0}
    usd_per_million("gpt-6-sol", batch=True) -> {"input": 1.0, "cached_input": 0.1, "output": 5.0}
    cost_usd(usd_per_million("gpt-6-sol"), input_tokens=1000, cached_tokens=0, output_tokens=100)  -> 0.003
    model_args("deepseek-flash-openrouter")  -> {"provider": {"order": ["deepinfra"], "allow_fallbacks": False}}
    check_routing("openrouter/x/y")          -> ValueError: an OpenRouter model without a `routing`
    prepare_env("deepseek/deepseek-v4-pro")  -> ValueError unless DEEPSEEK_API_KEY is set; sets DEEPSEEK_BASE_URL
    render()                                 -> the table `python -m src models` prints
"""
from __future__ import annotations

import os

import yaml

from src.configuration import settings

COLUMNS = ["entry", "inspect", "key", "context", "usd/M in", "usd/M out", "cheatbench", "status"]


def registry() -> dict:
    return settings.load_yaml(settings.MODELS)


def entries() -> dict[str, dict]:
    return registry()["models"]


def providers() -> dict[str, dict]:
    return registry()["providers"]


def resolve(name: str) -> str:
    """The Inspect model string of an entry, or `name` itself when it already carries a provider prefix."""
    if name in entries():
        return entries()[name]["inspect"]
    if "/" in name:
        return name
    raise ValueError(f"unknown model {name!r}: give an entry of src/models/model_registry.yaml "
                     "(python -m src models) or an Inspect model string such as mockllm/model")


def provider_of(model: str) -> str | None:
    """The registry's provider of an Inspect model string, by its longest prefix, or None (mockllm and the like)."""
    matches = [(len(route["inspect_prefix"]), name) for name, route in providers().items()
               if model.startswith(route["inspect_prefix"] + "/")]
    return max(matches)[1] if matches else None


def key_env(model: str) -> list[str]:
    """The variables Inspect accepts for the model's key, any one of which suffices; empty outside the registry."""
    provider = provider_of(model)
    return list(providers()[provider]["key_env"]) if provider else []


def entry_name(name: str) -> str | None:
    """`name` when it is an entry, else the entry whose Inspect string is `name`, else None."""
    if name in entries():
        return name
    return next((key for key, entry in entries().items() if entry["inspect"] == name), None)


def entry_of(name: str) -> dict | None:
    key = entry_name(name)
    return entries()[key] if key else None


def routing(name: str) -> dict | None:
    """The OpenRouter provider routing an entry pins (its `routing`), or None: no entry, or a route without one."""
    entry = entry_of(name)
    return dict(entry["routing"]) if entry and entry.get("routing") else None


def model_args(name: str) -> dict:
    """The model arguments get_model takes for this model beyond its name: {"provider": routing} on OpenRouter,
    nothing elsewhere."""
    pin = routing(name)
    return {"provider": pin} if pin else {}


def check_routing(model: str) -> None:
    """Raise ValueError for an OpenRouter model whose entry pins no host. Without a routing OpenRouter sends each
    call to a different host of the model, priced up to five times apart (2026-10-28)."""
    if provider_of(model) == "openrouter" and routing(model) is None:
        raise ValueError(f"{model} has no `routing` in src/models/model_registry.yaml: "
                         "an OpenRouter model runs only pinned to one host")


def prepare_env(model: str) -> None:
    """Raise ValueError unless one of the key variables of the model's route is set, then export what Inspect reads:
    a Gemini key given as GEMINI_API_KEY also as GOOGLE_API_KEY, and the base URL of an openai-api route. A model
    outside the registry (mockllm) needs nothing."""
    provider = provider_of(model)
    if provider is None:
        return
    names = key_env(model)
    if not any(os.environ.get(name) for name in names):
        raise ValueError(f"{' or '.join(names)} must be set for {model}")
    if "GOOGLE_API_KEY" in names and os.environ.get("GEMINI_API_KEY"):
        os.environ.setdefault("GOOGLE_API_KEY", os.environ["GEMINI_API_KEY"])
    route = providers()[provider]
    if route["inspect_prefix"].startswith("openai-api/") and route.get("base_url"):
        os.environ.setdefault(route["base_url_env"], route["base_url"])


def usd_per_million(name: str, batch: bool = False) -> dict | None:
    """The entry's prices in USD per million tokens (input, output, maybe cached_input), or None.

    With batch, a route with a batch_discount (OpenAI, Anthropic and Google bill batch calls at half) gives the
    batch prices; a route without one gives the list prices."""
    entry = entry_of(name)
    if entry is None:
        return None
    discount = providers()[entry["provider"]].get("batch_discount") if batch else None
    return {k: v * discount if discount else v for k, v in entry["usd_per_million"].items()}


def cost_usd(prices: dict | None, input_tokens: int, cached_tokens: int, output_tokens: int,
             cache_write: int = 0) -> float | None:
    """What the tokens cost at these prices (usd_per_million): cache reads at the cached rate, cache writes at the
    write rate (the input rate where the entry has none), the other input at the input rate, output at the output
    rate. input_tokens counts the reads and the writes. None without prices."""
    if not prices:
        return None
    cached_rate = prices.get("cached_input", prices["input"])
    write_rate = prices.get("cache_write", prices["input"])
    return ((input_tokens - cached_tokens - cache_write) * prices["input"] + cached_tokens * cached_rate
            + cache_write * write_rate + output_tokens * prices["output"]) / 1_000_000


def rows() -> list[list[str]]:
    """One row per entry, in COLUMNS order."""
    out = []
    for name, entry in entries().items():
        price = entry["usd_per_million"]
        out.append([name, entry["inspect"], " or ".join(key_env(entry["inspect"])), str(entry["context"]),
                    f"{price['input']:g}", f"{price['output']:g}", " ".join(entry["cheatbench"]) or "-",
                    entry["status"]])
    return out


def render(name: str | None = None) -> str:
    """The table of every entry, or one entry in full as YAML."""
    if name is not None:
        key = entry_name(name)
        if key is None:
            raise ValueError(f"unknown model {name!r}; python -m src models lists the entries")
        return yaml.safe_dump({key: entries()[key]}, sort_keys=False, width=110)
    table = [COLUMNS, *rows()]
    widths = [max(len(row[i]) for row in table) for i in range(len(COLUMNS))]
    lines = ["  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in table]
    lines.insert(1, "  ".join("-" * width for width in widths))
    lines.append(f"\nlist prices as of {registry()['as_of']}, USD per million tokens; "
                 "src/models/model_registry.yaml has the rest")
    return "\n".join(lines) + "\n"
