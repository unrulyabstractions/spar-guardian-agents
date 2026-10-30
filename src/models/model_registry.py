"""The model registry src/models/model_registry.yaml: every model we can consider, and the name each route needs.

    resolve("deepseek-v4-pro")               -> "deepseek/deepseek-v4-pro"
    resolve("mockllm/model")                 -> "mockllm/model"
    provider_of("deepseek/deepseek-v4-pro")  -> "deepseek"
    key_env("deepseek/deepseek-v4-pro")      -> ["DEEPSEEK_API_KEY"]
    usd_per_million("gpt-6-sol")             -> {"input": 2.0, "cached_input": 0.2, "output": 10.0}
    usd_per_million("gpt-6-sol", batch=True) -> {"input": 1.0, "cached_input": 0.1, "output": 5.0}
    model_args("deepseek-flash-openrouter")  -> {"provider": {"order": ["deepinfra"], "allow_fallbacks": False}}
    check_routing("openrouter/x/y")          -> ValueError: an OpenRouter model without a `routing`
    render()                                 -> the table `python -m src models` prints
"""
from __future__ import annotations

import functools

import yaml

from src.configuration import settings

COLUMNS = ["entry", "inspect", "key", "context", "usd/M in", "usd/M out", "cheatbench", "status"]


@functools.cache
def registry() -> dict:
    return settings.load_models()


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
                     f"(python -m src models) or an Inspect model string such as {settings.DEFAULT_MODEL}")


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


def usd_per_million(name: str, batch: bool = False) -> dict | None:
    """The entry's prices in USD per million tokens (input, output, maybe cached_input), or None.

    With batch, a route with a batch_discount (OpenAI, Anthropic and Google bill batch calls at half) gives the
    batch prices; a route without one gives the list prices."""
    entry = entry_of(name)
    if entry is None:
        return None
    discount = providers()[entry["provider"]].get("batch_discount") if batch else None
    return {k: v * discount if discount else v for k, v in entry["usd_per_million"].items()}


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
