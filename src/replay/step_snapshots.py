"""steps.jsonl: one line per recorded call in global order, with the state after it, and its delta encoding.

    lines = encode(rows)        # full states in, the lines of steps.jsonl out
    rows = decode(lines)        # and back: every line's state in full

A line is {"n", "agent", "k", "call_id", "t", "tool", "match", "state"}. In state, a key of DELTA_KEYS (a map from a
name to an id) holds only the entries that changed since the previous line of the same agent, a removed name as null,
and the whole map on the agent's first line; every other key holds its value in full. Pure.
"""
from __future__ import annotations

import copy

DELTA_KEYS = ("refs", "team_refs")
"""The state's maps written as changes: the agent's refs and the team remote's."""


def delta(previous: dict | None, current: dict) -> dict:
    """The entries of `current` that differ from `previous`, a name `previous` held and `current` lacks as None; the
    whole map when there is no previous one."""
    if previous is None:
        return dict(current)
    out = {name: value for name, value in current.items() if previous.get(name) != value}
    out.update({name: None for name in previous if name not in current})
    return out


def apply_delta(previous: dict | None, change: dict) -> dict:
    """The map `change` (delta's output) makes of `previous`."""
    out = dict(previous or {})
    for name, value in change.items():
        if value is None:
            out.pop(name, None)
        else:
            out[name] = value
    return out


def encode(rows: list[dict]) -> list[dict]:
    """The lines of steps.jsonl from rows in global order whose state is in full."""
    last: dict[str, dict] = {}
    lines = []
    for row in rows:
        state = row["state"]
        before = last.get(row["agent"])
        line = {**row, "state": {key: (delta(before.get(key) if before else None, value)
                                       if key in DELTA_KEYS else value) for key, value in state.items()}}
        last[row["agent"]] = state
        lines.append(line)
    return lines


def decode(lines: list[dict]) -> list[dict]:
    """The rows encode() wrote, every state in full."""
    last: dict[str, dict] = {}
    rows = []
    for line in lines:
        before = last.get(line["agent"])
        state = {key: (apply_delta(before.get(key) if before else None, value) if key in DELTA_KEYS else value)
                 for key, value in line["state"].items()}
        last[line["agent"]] = state
        rows.append({**line, "state": copy.deepcopy(state)})
    return rows


def pieces_state(pieces: list[str], tree: set[str], own_refs: dict[str, list[str]],
                 team_refs: dict[str, list[str]]) -> dict[str, dict]:
    """{piece: {"tree", "team", "refs"}}: whether the working tree holds it, and the team's and the agent's refs that
    do, each from a map of a ref to the pieces it holds."""
    def holders(refs: dict[str, list[str]], piece: str) -> list[str]:
        return sorted(ref for ref, held in refs.items() if piece in held)
    return {piece: {"tree": piece in tree, "team": holders(team_refs, piece), "refs": holders(own_refs, piece)}
            for piece in pieces}
