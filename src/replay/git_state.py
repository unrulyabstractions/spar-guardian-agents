"""What the git tasks' replayers share: which calls may touch the team remote, and the git keys of a steps.jsonl line.

    SHARED.search(command)                          # a call that may read or write the team remote
    git_line(pieces, standing, team)                # {"head", "branch", "refs", "team_refs", "pieces", "dirty"}

SWE and protein design both give each agent a repository and the team a bare remote. Pure.
"""
from __future__ import annotations

import re

from src.replay.step_snapshots import pieces_state

SHARED = re.compile(r"\bteam\b|/team|\bgit\b[^\n]*\b(?:push|fetch|pull|remote|ls-remote|clone|submodule)\b")
"""A call that may read or write the team remote: it runs alone, after every earlier call and before every later one.
Any other call touches only its agent's machine and may run beside other agents' calls."""


def git_line(pieces: list[str], standing: dict, team: dict) -> dict:
    """A line's git keys: the agent's HEAD, branch and refs, the team remote's refs, the pieces its tree and the refs
    hold (pieces_state, with the tree's reading where the task keeps one as tree_lines) and whether its tree differs
    from HEAD's. `standing` is the agent's state as it stands, `team` the team remote's."""
    return {"head": standing["head"], "branch": standing["branch"], "refs": standing["refs"],
            "team_refs": team["refs"],
            "pieces": pieces_state(pieces, set(standing["tree_pieces"]), standing["ref_pieces"], team["ref_pieces"],
                                   standing.get("tree_lines")),
            "dirty": standing["dirty"]}
