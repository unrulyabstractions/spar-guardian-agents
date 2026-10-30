"""SwarmConfig from text: the presets, `key=value` pairs and the command-line flags every entry point shares.

    config = build_config("default", "medium", ["regulators=0.1", "notice=counts"])
    config = with_overrides(config, ["regulators=1", "regulator_window=3:"])

    add_config_arguments(parser)                  # --model, --<role>-model, --preset, --scale, --set
    args = parser.parse_args()
    config = build_config(args.preset, args.scale, config_pairs(args))

One preset exists. `default` is the team's midterm report (2026-10-26) at the small scale: the pull board, which
agents look at through board_status, board_read and board_post and are told nothing about; per-round read and post
budgets; regulators with agent_actions, unbounded reads and their own K, at the `ambiguous` level, not disclosing
their role; the turn budget (settings.TURN_BUDGET) as every agent's working steps; split access to the exploit.

The command line applies the preset, then the scale, then every `key=value` pair. `src/cli.py`, the example scripts
(examples/example_arguments.py) and the screen (`python -m scripts.screen`) take the same flags through
add_config_arguments.
"""
from __future__ import annotations

import argparse
import typing

from src.configuration import settings
from src.configuration.scales import SCALES
from src.configuration.swarm_config import NONE_TEXT, ROLES, SwarmConfig
from src.models import model_registry as models

PRESETS = {
    "default": SwarmConfig(preset="default", turn_budget=settings.TURN_BUDGET).at_scale("small"),
}
ROLE_HOLDERS = {"worker": "the neutral workers'", "regulator": "the regulators'", "disregulator": "the disregulators'"}


def preset(name: str) -> SwarmConfig:
    """A preset by name."""
    if name not in PRESETS:
        raise ValueError(f"unknown preset {name!r}; choose one of {sorted(PRESETS)}")
    return PRESETS[name]


def _allows_none(field: str) -> bool:
    return type(None) in typing.get_args(SwarmConfig.model_fields[field].annotation)


def with_overrides(config: SwarmConfig, pairs: list[str]) -> SwarmConfig:
    """A copy with "key=value" strings applied; pydantic types each value by its field.

    "none" or "null" sets a field that allows None to None. A `scale=<name>` pair applies that scale first, then the
    other pairs, whatever their order."""
    updates = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key:
            raise ValueError(f"expected key=value, got {pair!r}")
        if key not in SwarmConfig.model_fields:
            raise ValueError(f"unknown setting {key!r}; choose one of {sorted(SwarmConfig.model_fields)}")
        updates[key] = None if value.lower() in NONE_TEXT and _allows_none(key) else value
    if "scale" in updates:
        scale = updates.pop("scale")
        config = config.at_scale(scale) if scale is not None else config.model_copy(update={"scale": None})
    return config.replace(**updates) if updates else config


def build_config(preset_name: str, scale: str | None = None, pairs: list[str] | None = None) -> SwarmConfig:
    """The preset, then the scale (None keeps the preset's), then every key=value pair. The config records the
    preset's name (`preset`)."""
    return with_overrides(preset(preset_name).at_scale(scale), list(pairs or []))


def model_name(text: str) -> str:
    """An argparse type: a registry entry or an Inspect model string, as its Inspect string."""
    try:
        return models.resolve(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def add_config_arguments(parser: argparse.ArgumentParser) -> None:
    """The flags that choose the agents' model and the config: --model, --worker-model, --regulator-model,
    --disregulator-model, --preset, --scale and --set (repeatable, into args.settings). Every model comes back as its
    Inspect string."""
    parser.add_argument("--model", type=model_name, default=settings.DEFAULT_MODEL,
                        help="the agents' model: an entry of src/models/model_registry.yaml (python -m src models) or "
                             f"an Inspect model string (default {settings.DEFAULT_MODEL})")
    for role in ROLES:
        parser.add_argument(f"--{role}-model", type=model_name, metavar="MODEL", default=None,
                            help=f"{ROLE_HOLDERS[role]} model, like --model, set after every --set (default: --model)")
    parser.add_argument("--preset", choices=sorted(PRESETS), default="default",
                        help="default: the midterm report's configuration at the small scale")
    parser.add_argument("--scale", choices=list(SCALES), default=None,
                        help="the run's size, applied after the preset (default: the preset's own, small for default)")
    parser.add_argument("--set", dest="settings", action="append", default=[], metavar="KEY=VALUE",
                        help="a SwarmConfig field, set after the preset and the scale; repeatable")


def config_pairs(args: argparse.Namespace) -> list[str]:
    """The key=value pairs of add_config_arguments' flags: every --set, then `<role>_model=<model>` for each role
    flag given, so the flag wins."""
    roles = [f"{role}_model={getattr(args, f'{role}_model')}" for role in ROLES if getattr(args, f"{role}_model")]
    return [*args.settings, *roles]
