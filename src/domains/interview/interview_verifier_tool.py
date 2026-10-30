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

from collections.abc import Callable

from inspect_ai.tool import ToolParams
from orbit.tools import ToolFactory

from src.domains.interview.interview_settings import SPECS, Spec
from src.sandbox import cheatbench_task_files as task_files
from src.sandbox import machines

PARAMETER_DESCRIPTIONS = {
    "subset_indices": "The 0-based indices of the integers in the candidate subset.",
    "p": "One candidate prime factor, as a decimal string.",
    "q": "The other candidate prime factor, as a decimal string.",
}


GRADERS = {task: task_files.load_grader(spec.task_dir, f"cheatbench_{task}_grader") for task, spec in SPECS.items()}


def parameters(spec: Spec) -> ToolParams:
    """The grader's INPUT_SCHEMA as Inspect's parameters, each with our description."""
    return task_files.tool_params(GRADERS[spec.task].INPUT_SCHEMA, PARAMETER_DESCRIPTIONS)


def tool_name(spec: Spec) -> str:
    return GRADERS[spec.task].TOOL_BASENAME


def verifier_binding(spec: Spec, row: dict, owner_of: Callable[[str], str]) -> dict[str, ToolFactory]:
    """The verifier as a tool binding: one CheatBench state per owner, built from the row on the owner's first call."""
    grader = GRADERS[spec.task]
    return machines.host_tool(grader.TOOL_BASENAME, grader.DESCRIPTION, parameters(spec),
                              lambda owner: grader.make_tool(row, {}, machines.WORKDIR), owner_of)
