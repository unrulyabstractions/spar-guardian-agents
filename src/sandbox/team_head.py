"""The head of a team remote's main, as the harness reads it for the push notices (turn_rules _turn_pushes).

The harness reads the ref itself, as root, never through a hook or a log an agent writes: an agent can write the
hooks of a remote it can push to where no lock stops it. The pusher comes from the reflog's last entry, which the remote
writes under core.logAllRefUpdates (team_remote_lock.sh, swe _swe_team_remote.LOCK) with the pusher's git identity: it
is named only when that entry moved main to the head read and names an agent of the roster, else None.

    head = await read_head(sandbox(), "/team/repo.git", "/team/repo.git", "main", ["k7q2m", "t4x9n"])
    head        # {"head": "<40 hex>", "by": "k7q2m"}, or None while main does not exist
"""
from __future__ import annotations

import re
import shlex


def head_command(git_dir: str, logs_dir: str, branch: str) -> str:
    """main's commit, then the last line of its reflog (nothing without one)."""
    return (f"git --git-dir={shlex.quote(git_dir)} rev-parse -q --verify refs/heads/{branch}; "
            f"tail -n 1 {shlex.quote(logs_dir)}/logs/refs/heads/{branch} 2>/dev/null || true")


def parse_head(text: str, agents: list[str]) -> dict | None:
    """{"head", "by"} from head_command's output; None when main is missing."""
    lines = (text or "").splitlines()
    if not lines or not re.fullmatch(r"[0-9a-f]{40}", lines[0].strip()):
        return None
    head, by = lines[0].strip(), None
    if len(lines) > 1:
        fields = lines[1].split("\t")[0].split()
        if len(fields) >= 3 and fields[1] == head and fields[2] in agents:
            by = fields[2]
    return {"head": head, "by": by}


async def read_head(box, git_dir: str, logs_dir: str, branch: str, agents: list[str]) -> dict | None:
    """The head of the branch in the box, read as root."""
    r = await box.exec(["bash", "-c", head_command(git_dir, logs_dir, branch)], user="root", timeout=60)
    return parse_head(r.stdout or "", agents)
