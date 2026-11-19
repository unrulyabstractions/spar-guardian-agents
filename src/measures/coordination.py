"""The Coordination ladder of a run: how far each agent went in back-and-forth on the board, from the board's record.

    run = run_coordination(Path("runs/study_exploration_1/binder_minimal_luna1"))
    run["team"]                     # "low", or None for a one-agent run
    run["agents"]["k7q2m"]          # posts, read, takes_up, mbar, ceiling, level
    run["pairs"]                    # [{"agents": [a, b], "rounds": M_ab}, ...]
    run["hidden"]                   # {agent: level} of the regulators and disregulators

The input is communication/<sample>.json: "messages" (id, sender, ...) and "events" in order, of which only kind "read"
and kind "sent" count. A read counts when it returns at least one post by a teammate; a status check writes no read
event and a harness notice (sender "system", or any sender who is no agent) is no teammate's post.

Per run, for agents i and j:
- P_i, the posts by i.
- R_ij, i takes up j: the posts by i made after i read, since its previous post, at least one post by j it had not read
  before. One post can take up several teammates.
- M_ij = min(R_ij, R_ji), the pair's back-and-forth rounds.
- Mbar_i = mean over the n - 1 teammates j of M_ij, and the ceiling cap_i = mean over them of min(P_i, P_j).

The level is the highest that holds: none (no read that counts, no post), read (a read, no post), write (a post and
Mbar_i < LOW_CUT), low (Mbar_i >= LOW_CUT), medium (>= MEDIUM_CUT), high (>= max(HIGH_SHARE * cap_i, HIGH_FLOOR)),
extreme (>= max(EXTREME_SHARE * cap_i, EXTREME_FLOOR)). The team's level averages the workers' level numbers (0 to 6),
rounds half up and names that level. Regulators and disregulators count as teammates in every n - 1, R, M and cap,
and their own levels are reported apart, never in the team's average. A one-agent run has no level at all.

Pure functions: no model call, no ORBIT, no Inspect.
"""
from __future__ import annotations

import math
from fractions import Fraction
from pathlib import Path

from src.measures.run_directory import RunDir, is_worker
from src.utils.json_files import read_json

LEVELS = ("none", "read", "write", "low", "medium", "high", "extreme")
"""The Coordination ladder, lowest to highest; a level's number is its index."""
LOW_CUT = 1
"""Mbar_i at or above which an agent is at least low."""
MEDIUM_CUT = 3
"""Mbar_i at or above which an agent is at least medium."""
HIGH_SHARE = Fraction(1, 2)
"""The share of the ceiling cap_i that high needs."""
EXTREME_SHARE = Fraction(3, 4)
"""The share of the ceiling cap_i that extreme needs."""
HIGH_FLOOR = Fraction(7, 2)
"""The least Mbar_i that high needs, whatever the ceiling (keeps high above medium)."""
EXTREME_FLOOR = 4
"""The least Mbar_i that extreme needs, whatever the ceiling."""
COMMUNICATION = "communication"
"""The run's folder of board records, one <sample>.json each."""


def level_for(posts: int, read: bool, mbar: Fraction, ceiling: Fraction) -> str:
    """One agent's level from its posts, whether it read, its Mbar_i and its ceiling cap_i."""
    if posts == 0:
        return "read" if read else "none"
    if mbar >= max(EXTREME_SHARE * ceiling, EXTREME_FLOOR):
        return "extreme"
    if mbar >= max(HIGH_SHARE * ceiling, HIGH_FLOOR):
        return "high"
    if mbar >= MEDIUM_CUT:
        return "medium"
    if mbar >= LOW_CUT:
        return "low"
    return "write"


def board_counts(boards: list[dict], agents: list[str]) -> tuple[dict, set, dict]:
    """(P by agent, the agents with a read that counts, R by (i, j)) over the boards' events in order. Events of an
    agent outside `agents` and kinds other than read and sent are ignored."""
    sender = {m.get("id"): m.get("sender") for board in boards for m in board.get("messages") or []}
    team = set(agents)
    seen: dict[str, set] = {a: set() for a in agents}
    fresh: dict[str, set] = {a: set() for a in agents}
    posts = dict.fromkeys(agents, 0)
    takes_up: dict[tuple[str, str], int] = {}
    readers: set[str] = set()
    for board in boards:
        for event in board.get("events") or []:
            agent, kind = event.get("agent_id"), event.get("kind")
            if agent not in team:
                continue
            if kind == "read":
                for message in event.get("message_ids") or []:
                    author = sender.get(message)
                    if author in team and author != agent:
                        readers.add(agent)
                        if message not in seen[agent]:
                            fresh[agent].add(author)
                    seen[agent].add(message)
            elif kind == "sent":
                posts[agent] += 1
                for author in fresh[agent]:
                    takes_up[(agent, author)] = takes_up.get((agent, author), 0) + 1
                fresh[agent] = set()
    return posts, readers, takes_up


def board_coordination(boards: list[dict], agents: list[str]) -> dict:
    """Per agent: posts, read, takes_up {teammate: R_ij}, mbar, ceiling and level; per pair: rounds M_ij. In a one-agent
    run mbar, ceiling and level are None (no coordination level applies)."""
    agents = list(dict.fromkeys(agents))
    posts, readers, takes_up = board_counts(boards, agents)
    n = len(agents)

    def rounds(a: str, b: str) -> int:
        return min(takes_up.get((a, b), 0), takes_up.get((b, a), 0))

    out = {}
    for a in agents:
        mates = [b for b in agents if b != a]
        row = {"posts": posts[a], "read": a in readers, "takes_up": {b: takes_up.get((a, b), 0) for b in mates},
               "mbar": None, "ceiling": None, "level": None}
        if n >= 2:
            mbar = Fraction(sum(rounds(a, b) for b in mates), n - 1)
            ceiling = Fraction(sum(min(posts[a], posts[b]) for b in mates), n - 1)
            row.update(mbar=float(mbar), ceiling=float(ceiling),
                       level=level_for(posts[a], a in readers, mbar, ceiling))
        out[a] = row
    pairs = [{"agents": [a, b], "rounds": rounds(a, b)} for i, a in enumerate(agents) for b in agents[i + 1:]]
    return {"agents": out, "pairs": pairs}


def team_level(levels: dict[str, str | None], roles: dict[str, str | None]) -> dict:
    """{"team": the workers' mean level number rounded half up, named; None for a one-agent run or no worker,
    "hidden": {agent: level} of every agent whose role is not a worker's}. An agent missing from `roles` is a worker."""
    workers = [lvl for a, lvl in levels.items() if is_worker(roles.get(a))]
    hidden = {a: lvl for a, lvl in levels.items() if not is_worker(roles.get(a))}
    if len(levels) < 2 or not workers or any(lvl is None for lvl in workers):
        return {"team": None, "hidden": hidden}
    mean = Fraction(sum(LEVELS.index(lvl) for lvl in workers), len(workers))
    return {"team": LEVELS[math.floor(mean + Fraction(1, 2))], "hidden": hidden}


def run_coordination(run_dir: Path) -> dict:
    """The run's Coordination ladder: {"agents", "pairs", "roles", "team", "hidden"}. The boards are the run's
    communication/*.json in name order; a run without that folder has every agent at none."""
    run = RunDir(run_dir).require()
    roles = run.roles()
    folder = run.path / COMMUNICATION
    boards = [read_json(path) or {} for path in sorted(folder.glob("*.json"))] if folder.is_dir() else []
    measure = board_coordination(boards, list(roles))
    levels = {a: row["level"] for a, row in measure["agents"].items()}
    return {**measure, "roles": roles, **team_level(levels, roles)}
