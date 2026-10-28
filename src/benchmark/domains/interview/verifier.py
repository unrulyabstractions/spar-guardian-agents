"""verify_subset and verify_factors: CheatBench's verifier tools, run on the host and bound per agent.

Each task's grader.py is loaded unchanged (stdlib at its top level; the Claude SDK import sits inside make_grader,
which is never called) and its make_tool(row) builds the verifier's `(state, call)` from the row's numbers, as
CheatBench's runners do. machines.host_tool binds it per agent under the grader's TOOL_BASENAME and DESCRIPTION: one
state per owner (the agent, or the team in shared_task), every call and the owner's state in the sample store.

Departures from CheatBench. The parameters carry our descriptions: Inspect requires one for each, and CheatBench's
INPUT_SCHEMA gives none. Inspect checks the arguments against that schema before the verifier runs. An argument of
the wrong type, such as a JSON number for p, comes back as a tool error and does not count in the state's `rejected`.
"""
from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable

from inspect_ai.tool import ToolParam, ToolParams
from orbit.tools import ToolFactory

from src.benchmark import machines
from src.benchmark.domains.interview.constants import SPECS, Spec

PARAMETER_DESCRIPTIONS = {
    "subset_indices": "The 0-based indices of the integers in the candidate subset.",
    "p": "One candidate prime factor, as a decimal string.",
    "q": "The other candidate prime factor, as a decimal string.",
}


def load_grader(spec: Spec):
    """CheatBench's grader.py of the task, as a module, with no __pycache__ in the submodule's task folder."""
    module_spec = importlib.util.spec_from_file_location(f"cheatbench_{spec.task}_grader", spec.task_dir / "grader.py")
    module = importlib.util.module_from_spec(module_spec)
    before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        module_spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = before
    return module


GRADERS = {task: load_grader(spec) for task, spec in SPECS.items()}


def _param(schema: dict, name: str | None = None) -> ToolParam:
    items = _param(schema["items"]) if "items" in schema else None
    return ToolParam(type=schema["type"], items=items,
                     description=PARAMETER_DESCRIPTIONS[name] if name else None)


def parameters(spec: Spec) -> ToolParams:
    """The grader's INPUT_SCHEMA as Inspect's parameters, each with our description."""
    schema = GRADERS[spec.task].INPUT_SCHEMA
    return ToolParams(properties={name: _param(prop, name) for name, prop in schema["properties"].items()},
                      required=list(schema["required"]))


def tool_name(spec: Spec) -> str:
    return GRADERS[spec.task].TOOL_BASENAME


def verifier_binding(spec: Spec, row: dict, owner_of: Callable[[str], str]) -> dict[str, ToolFactory]:
    """The verifier as a tool binding: one CheatBench state per owner, built from the row on the owner's first call."""
    grader = GRADERS[spec.task]
    return machines.host_tool(grader.TOOL_BASENAME, grader.DESCRIPTION, parameters(spec),
                              lambda owner: grader.make_tool(row, {}, machines.WORKDIR), owner_of)
