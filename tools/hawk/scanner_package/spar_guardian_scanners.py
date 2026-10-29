"""Hawk's entry point to the repository's scanners.

Hawk loads a scanner by the name `spar_guardian_scanners/<scanner>` and calls it with the item's args. Inspect
imports this module through the `inspect_ai` entry point to find that name. The module puts SPAR_GUARDIAN_ROOT on
sys.path, imports SPAR_GUARDIAN_SCANNERS and registers two kinds of factories under this package's name.

A forwarding factory for every `@scanner` the module defines, with the same arguments. Inspect registers a scanner
from an uninstalled module without a package prefix, so Hawk's name would not find it.

A run-bound factory for every signal in the module's SIGNALS and for its GREP_NAME, when the module has
build_scanners. It takes `run` (a name under runs/ or a run directory) and an optional `model`, loads the run with
tools.run_reader.load_run and returns the scanner build_scanners makes for that run. Without `model` the LLM
scanners use
the scan's model.
"""
from __future__ import annotations

import importlib
import os
import sys

from inspect_ai._util.registry import RegistryInfo, is_registry_object, registry_add, registry_info

PACKAGE = "spar_guardian_scanners"
ROOT_VARIABLE = "SPAR_GUARDIAN_ROOT"
MODULE_VARIABLE = "SPAR_GUARDIAN_SCANNERS"
RUNS_MODULE = "tools.run_reader"

ROOT = os.environ.get(ROOT_VARIABLE, "")
if ROOT and ROOT not in sys.path:
    sys.path.insert(0, ROOT)
SCANNERS = importlib.import_module(os.environ[MODULE_VARIABLE])


def forwarding_factory(factory):
    """A factory that calls the module's own scanner factory, so its scanner keeps its own filters and name."""
    def create(**kwargs):
        return factory(**kwargs)
    return create


def run_bound_factory(name: str):
    """A factory that builds the scanner `name` for one run through the module's build_scanners."""
    def create(run: str, model: str | None = None):
        loaded = importlib.import_module(RUNS_MODULE).load_run(run)
        signals = [name] if name in SCANNERS.SIGNALS else []
        return SCANNERS.build_scanners(loaded, signals, model, grep=name == SCANNERS.GREP_NAME)[name]
    return create


def register(name: str, factory) -> str:
    """Register a factory as `PACKAGE/name`; returns that name."""
    qualified = f"{PACKAGE}/{name}"
    registry_add(factory, RegistryInfo(type="scanner", name=qualified, metadata={"scanner_version": 0}))
    return qualified


def register_repository_scanners() -> list[str]:
    """Register the forwarding factories, then the run-bound ones; returns the registered names."""
    names = []
    for value in list(vars(SCANNERS).values()):
        if is_registry_object(value, "scanner") and getattr(value, "__module__", None) == SCANNERS.__name__:
            names.append(register(registry_info(value).name.split("/")[-1], forwarding_factory(value)))
    if hasattr(SCANNERS, "build_scanners"):
        for name in [*SCANNERS.SIGNALS, SCANNERS.GREP_NAME]:
            names.append(register(name, run_bound_factory(name)))
    return names


REGISTERED = register_repository_scanners()
