"""What the git tasks' replayers share: which calls may touch the team remote, which read it before an overlapping
write, and the git keys of a steps.jsonl line.

    SHARED.search(command)                          # a call that may read or write the team remote
    read_before_write(reader, command, writer, changed)    # the record shows the read came before the write
    git_line(pieces, standing, team)                # {"head", "branch", "refs", "team_refs", "pieces", "dirty"}

SWE and protein design both give each agent a repository and the team a bare remote. Pure.
"""
from __future__ import annotations

import re

from src.replay.output_match import HEX
from src.replay.recorded_calls import RecordedCall
from src.replay.step_snapshots import pieces_state

SHARED = re.compile(r"\bteam\b|/team|\bgit\b[^\n]*\b(?:push|fetch|pull|remote|ls-remote|clone|submodule)\b")
"""A call that may read or write the team remote: it runs alone, after every earlier call and before every later one.
Any other call touches only its agent's machine and may run beside other agents' calls."""
TEAM_READ = re.compile(r"\bgit\b[^\n;&|]*\b(?:(?:fetch|pull|ls-remote)\b[^\n;&|]*(?:\bteam\b|--all)|remote\s+update\b)")
"""A git command that reads the team remote: a fetch, pull or ls-remote that names it or every remote, or
`git remote update`."""
PUSH = re.compile(r"\bpush\b")


def reads_team_only(command: str) -> bool:
    """Whether the call reads the team remote (TEAM_READ) and cannot write it: no push anywhere in it and no path
    into /team."""
    return bool(TEAM_READ.search(command)) and not PUSH.search(command) and "/team" not in command


def changed_refs(before: dict[str, str], after: dict[str, str]) -> dict[str, tuple[str | None, str | None]]:
    """Each ref whose tip differs between two readings of the team remote, as (old, new). None marks a missing ref."""
    return {name: (before.get(name), after.get(name)) for name in sorted({*before, *after})
            if before.get(name) != after.get(name)}


def shows_before(output: str, changed: dict[str, tuple[str | None, str | None]]) -> bool:
    """Whether the output shows the team remote as it stood before the change: it names an old tip and no new one. A
    name is a commit id of 7 to 40 hex digits that begins the tip."""
    ids = set(HEX.findall(output))

    def shown(sha: str | None) -> bool:
        return bool(sha) and any(sha.startswith(i) for i in ids)
    return any(shown(old) for old, _ in changed.values()) and not any(shown(new) for _, new in changed.values())


def read_before_write(reader: RecordedCall, command: str, writer: RecordedCall,
                      changed: dict[str, tuple[str | None, str | None]]) -> bool:
    """Whether the record shows that `reader` read the team remote before `writer` changed its refs (`changed`). The
    reader is another agent's call that started before the writer ended, only reads the remote (reads_team_only) and
    has a recorded output that shows the remote as it stood before (shows_before). The record keeps no time for a
    step inside a call, so the output is the evidence."""
    return (reader.agent != writer.agent and reader.t0 < writer.t1 and bool(changed) and reads_team_only(command)
            and shows_before(reader.result, changed))


def git_line(pieces: list[str], standing: dict, team: dict) -> dict:
    """A line's git keys: the agent's HEAD, branch and refs, the team remote's refs, the pieces its tree and the refs
    hold (pieces_state, with the tree's reading where the task keeps one as tree_lines) and whether its tree differs
    from HEAD's. `standing` is the agent's state as it stands, `team` the team remote's."""
    return {"head": standing["head"], "branch": standing["branch"], "refs": standing["refs"],
            "team_refs": team["refs"],
            "pieces": pieces_state(pieces, set(standing["tree_pieces"]), standing["ref_pieces"], team["ref_pieces"],
                                   standing.get("tree_lines")),
            "dirty": standing["dirty"]}
