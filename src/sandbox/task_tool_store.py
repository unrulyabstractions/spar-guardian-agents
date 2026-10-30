"""The sample store of a task's own tool: every call in order, and each owner's state after its last call.

    record({"agent": "a01", "owner": "team", "tool": "verify_subset", ...})
    accepted = states().get("team", {}).get("accepted")

The machines layer's host tool (the interview verifier, the protein checker) and knowledge work's review tool record
their calls here; the scorers, the delivery predicates and the export read them back. An owner is the agent, or the
team in shared_task.
"""
from __future__ import annotations

from inspect_ai.util import store

CALLS = "task_tool_calls"       # every call, in order: agent, tool, the arguments and what the tool said
STATES = "task_tool_state"      # {owner: the tool's state after its last call}


def record(entry: dict, st=None) -> None:
    """Append one call to the sample store (or `st`)."""
    st = st or store()
    st.set(CALLS, [*(st.get(CALLS) or []), entry])


def calls(st=None) -> list[dict]:
    """Every call of the task's tool in the sample, in order."""
    return list((st or store()).get(CALLS) or [])


def states(st=None) -> dict[str, dict]:
    """The tool's state per owner after its last call; an owner without a call is absent."""
    return dict((st or store()).get(STATES) or {})


def set_state(owner: str, state: dict, st=None) -> None:
    """Keep a copy of the owner's state."""
    st = st or store()
    st.set(STATES, {**states(st), owner: state})
