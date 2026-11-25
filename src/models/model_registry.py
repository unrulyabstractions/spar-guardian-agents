"""The model registry src/models/model_registry.yaml: every model we can consider, and the name each route needs.

    resolve("deepseek-v4-pro")               -> "deepseek/deepseek-v4-pro"
    resolve("mockllm/model")                 -> "mockllm/model"
    provider_of("deepseek/deepseek-v4-pro")  -> "deepseek"
    key_env("deepseek/deepseek-v4-pro")      -> ["DEEPSEEK_API_KEY"]
    usd_per_million("gpt-5")                 -> {"input": 1.25, "cached_input": 0.125, "output": 10.0}
    usd_per_million("gpt-5", batch=True)     -> {"input": 0.625, "cached_input": 0.0625, "output": 5.0}
    cost_usd(usd_per_million("gpt-6-sol"), input_tokens=1000, cached_tokens=0, output_tokens=100)  -> 0.003
    model_args("deepseek-flash-openrouter")  -> {"provider": {"order": ["deepinfra/fp8"], ...}, "strict_tools": False}
    check_routing("openrouter/x/y")          -> ValueError: an OpenRouter model without a `routing`
    prepare_env("deepseek/deepseek-v4-pro")  -> ValueError unless DEEPSEEK_API_KEY is set; sets DEEPSEEK_BASE_URL
    prepare_env("anthropic/claude-opus-5-5") -> ANTHROPIC_API_KEY from the provider's key_file; ValueError without it
    key_source("anthropic/claude-opus-5-5")  -> "/Users/me/.config/spar-guardian-agents/anthropic_key"
    model_args("claude-opus-5-5")            -> {"default_headers": {"anthropic-workspace-id": "wrkspc_..."}} with a
                                                workspace file beside the key file, {} without one
    render()                                 -> the table `python -m src models` prints
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

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
    """The model arguments get_model takes for this model beyond its name. On OpenRouter: the routing, and tools sent
    without `strict`. Inspect's OpenRouter route marks every tool strict by default, and OpenAI then refuses a tool
    with an optional parameter (board_read's `latest`), which the direct OpenAI route never marks. On a provider with
    a workspace file: the header that carries the workspace ID on every request (workspace_id). Nothing otherwise."""
    pin = routing(name)
    if pin:
        return {"provider": pin, "strict_tools": False}
    entry = entry_of(name)
    provider = provider_of(entry["inspect"] if entry else name)
    workspace = workspace_id(provider) if provider else None
    return {"default_headers": {providers()[provider]["workspace_header"]: workspace}} if workspace else {}


def check_routing(model: str) -> None:
    """Raise ValueError for an OpenRouter model whose entry pins no host. Without a routing OpenRouter sends each
    call to a different host of the model, priced up to five times apart (2026-10-28)."""
    if provider_of(model) == "openrouter" and routing(model) is None:
        raise ValueError(f"{model} has no `routing` in src/models/model_registry.yaml: "
                         "an OpenRouter model runs only pinned to one host")


KEY_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
WORKSPACE_PATTERN = re.compile(r"wrkspc_[A-Za-z0-9]+")
WORKSPACE_SUFFIX = ".workspace"
"""The file beside a key file that holds a workspace ID."""


def key_path(provider: str) -> Path | None:
    """The file the provider's `key_file` names, with ~ expanded, or None for a provider without one."""
    name = providers()[provider].get("key_file")
    return Path(name).expanduser() if name else None


def workspace_path(provider: str) -> Path | None:
    """The workspace file beside the provider's key file, or None for a provider without a workspace header."""
    path = key_path(provider)
    if path is None or not providers()[provider].get("workspace_header"):
        return None
    return path.with_name(path.name + WORKSPACE_SUFFIX)


def read_one_line(path: Path, pattern: re.Pattern, what: str) -> str:
    """The value a private file holds on its one line, the final newline stripped. Raises ValueError naming the file
    and the fix, never its content: for a file group or others may open, one that cannot be read (a folder
    included), or one whose line is not `pattern` in ASCII."""
    try:
        if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ValueError(f"{path} is open to group or others: chmod 600 {path}")
        value = path.read_bytes().decode("ascii").removesuffix("\n")
    except (OSError, UnicodeDecodeError):
        raise ValueError(f"{path} cannot be read as one line of ASCII: write {what} to it (chmod 600)") from None
    if not pattern.fullmatch(value):
        raise ValueError(f"{path} must hold {what} alone on one line ({pattern.pattern})")
    return value


def read_key_file(path: Path) -> str:
    """The key a key file holds (read_one_line, [A-Za-z0-9_-]+)."""
    return read_one_line(path, KEY_PATTERN, "the key")


def workspace_id(provider: str) -> str | None:
    """The workspace ID in the provider's workspace file (read_one_line, wrkspc_...), or None when no file or link is
    there."""
    path = workspace_path(provider)
    if path is None or not (path.exists() or path.is_symlink()):
        return None
    return read_one_line(path, WORKSPACE_PATTERN, "the workspace ID")


def key_source(model: str) -> str | None:
    """Where prepare_env takes the model's key from, never the key: its provider's key file, else the first of its key
    variables that is set. None outside the registry or when no key variable is set."""
    provider = provider_of(model)
    if provider is None:
        return None
    path = key_path(provider)
    if path is not None:
        return str(path)
    return next((name for name in key_env(model) if os.environ.get(name)), None)


def refuse_redirects(model: str, route: dict) -> None:
    """Raise ValueError while a variable would send the key elsewhere: one of the route's `shadow_env`, which Inspect
    sends in place of the key, or a `base_url_env` that holds another host than the route's `pinned_base_url`."""
    if shadows := [name for name in route.get("shadow_env", []) if os.environ.get(name)]:
        raise ValueError(f"unset {' and '.join(shadows)} for {model}: Inspect would send it in place of the key")
    pinned, url = route.get("pinned_base_url"), os.environ.get(route["base_url_env"])
    if pinned and url and url.rstrip("/") != pinned:
        raise ValueError(f"unset {route['base_url_env']} for {model} (or set it to {pinned}): the key would go to "
                         "that host")


def file_key(model: str, path: Path) -> str:
    """The key in a provider's key file, refused when the file is missing, a dangling link or a folder: no fallback to
    the environment's key."""
    if not path.is_file():
        raise ValueError(f"{model} takes its key from its key file alone: write the key to {path} (chmod 600)")
    return read_key_file(path)


def prepare_env(model: str) -> None:
    """Raise ValueError unless the model's route has a key and nothing would send it elsewhere (refuse_redirects),
    then export what Inspect reads. A provider that names a key file takes that file's key (file_key), set in its first
    key variable over the environment's value; any other route needs one of its key variables set. A provider's
    workspace file is checked too (workspace_id). A Gemini key given as GEMINI_API_KEY is also exported as
    GOOGLE_API_KEY, and an openai-api route gets its base URL. A refused call changes no variable. A model outside the
    registry (mockllm) needs nothing. The key is never printed or returned."""
    provider = provider_of(model)
    if provider is None:
        return
    names, route, path = key_env(model), providers()[provider], key_path(provider)
    refuse_redirects(model, route)
    key = file_key(model, path) if path is not None else None
    workspace_id(provider)
    if key is None and not any(os.environ.get(name) for name in names):
        raise ValueError(f"{' or '.join(names)} must be set for {model}")
    if key is not None:
        os.environ[names[0]] = key
    if "GOOGLE_API_KEY" in names and os.environ.get("GEMINI_API_KEY"):
        os.environ.setdefault("GOOGLE_API_KEY", os.environ["GEMINI_API_KEY"])
    if route["inspect_prefix"].startswith("openai-api/") and route.get("base_url"):
        os.environ.setdefault(route["base_url_env"], route["base_url"])


def usd_per_million(name: str, batch: bool = False) -> dict | None:
    """The entry's prices in USD per million tokens (input, output, maybe cached_input, cache_write and long_prompt),
    or None.

    With batch, a route with a batch_discount (OpenAI, Anthropic and Google bill batch calls at half) gives the
    batch prices; a route without one gives the list prices."""
    entry = entry_of(name)
    if entry is None:
        return None
    discount = (providers()[entry["provider"]].get("batch_discount") if batch else None) or 1

    def scaled(rates: dict) -> dict:
        return {k: v * discount for k, v in rates.items() if k != "over"}

    long_prompt = entry.get("long_prompt")
    return {**scaled(entry["usd_per_million"]),
            **({"long_prompt": {"over": long_prompt["over"], **scaled(long_prompt)}} if long_prompt else {})}


def call_rates(prices: dict, prompt_tokens: int) -> dict:
    """The rates one call pays at these prices (usd_per_million): the long_prompt rates when its prompt (cache reads
    and writes included) is over the entry's line, the normal rates otherwise. Without the long_prompt and over
    keys."""
    long_prompt = prices.get("long_prompt")
    chosen = long_prompt if long_prompt and prompt_tokens > long_prompt["over"] else prices
    return {k: v for k, v in chosen.items() if k not in ("long_prompt", "over")}


def cost_usd(prices: dict | None, input_tokens: int, cached_tokens: int, output_tokens: int,
             cache_write: int = 0, cache_write_1h: int = 0) -> float | None:
    """What one call's tokens cost at these prices (usd_per_million): cache reads at the cached rate, cache writes at
    the write rate (the input rate where the entry has none), the other input at the input rate, output at the output
    rate. Of the cache writes, cache_write_1h were made at the one-hour lifetime and pay the cache_write_1h rate (the
    write rate where the entry has none). input_tokens counts the reads and the writes. A prompt over the entry's
    long_prompt line pays those rates on every token (call_rates), so a sum of calls is priced call by call. None
    without prices."""
    if not prices:
        return None
    prices = call_rates(prices, input_tokens)
    cached_rate = prices.get("cached_input", prices["input"])
    write_rate = prices.get("cache_write", prices["input"])
    hour_rate = prices.get("cache_write_1h", write_rate)
    return ((input_tokens - cached_tokens - cache_write) * prices["input"] + cached_tokens * cached_rate
            + (cache_write - cache_write_1h) * write_rate + cache_write_1h * hour_rate
            + output_tokens * prices["output"]) / 1_000_000


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
