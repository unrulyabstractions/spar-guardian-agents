"""The Coordination ladder of a run: how far each agent went in back-and-forth on the board, from the board's record,
read after every board event.

    run = run_coordination(Path("runs/ian_baseline_explorations_1/binder_minimal_luna1"))
    run["team"]                     # "low", or None for a one-agent run
    run["agents"]["k7q2m"]          # posts, read, takes_up, mbar, ceiling, level
    run["pairs"]                    # [{"agents": [a, b], "rounds": M_ab}, ...]
    run["influencers"]              # {agent: level} of the influencers (regulators and disregulators)
    run["held"]["k7q2m"]            # [{call_id, level, posts, mbar, ceiling, by}]: its level at its own board calls
    run["team_steps"]               # [{call_id, agent, level}]: each board event that changed the team's level

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
rounds half up and names that level. Influencers (regulators and disregulators) count as teammates in every n - 1, R, M
and cap, and their own levels are reported apart, never in the team's average. A one-agent run has no level at all.

Over time (coordination_steps). The counts are running counts over the board's events in order (Tally), and after
each event that counts (a read or a post of an agent of the run) every agent's level is level_for of its counts so
far: its P_i, its read, its Mbar_i and its ceiling cap_i so far, against the same cuts. The whole run's measure is the
same function of the counts after the last event, so the last step's levels are the run's levels. P, R and M never
fall, so Mbar_i never falls and none, read, write, low and medium, once held, hold to the end. The cuts of high and
extreme are shares of cap_i, which rises with every post of the agent or a teammate who posted less: an agent at high
or extreme can fall back (to medium at the least) when posts raise its ceiling faster than its rounds. A level so far
is the level held at that step, never the highest reached before it.

At the agent's own calls (held_levels). An agent's level can change at a teammate's event (that teammate's post adds a
round, or raises the ceiling). Its level at its own board call is its level after that call's event; a change made by
teammates' events between two of its board calls is shown at the next one, with "by" the teammate whose event made the
last change; a change after its last board call has no call (call_id None). The team's level changes at the event that
changes it, at that event's call (team_steps).

Each agent's first read that counts and each of its posts carry their tool call id (calls), so a reader can point at
them. A run records each agent row's coordination_level and the team row's coordination_level and
coordination_influencers (record_fields, written by src/measures/code_ladder.py run_signals), which code_ladder.ladders
reads; the counts behind a level are computed in memory (run_coordination).

    run = coordination_of(boards, roles)        # the same from board records already read (RunDir.boards)
    steps = coordination_steps(boards, roles)   # [{sequence, agent, kind, call_id, agents, team}] after each event

Pure functions: no model call, no ORBIT, no Inspect.
"""
from __future__ import annotations

import math
from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path
from typing import NamedTuple

from src.measures.run_directory import RunDir, is_influencer, is_worker

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
COUNTED = ("read", "sent")
"""The kinds of board event the counts read."""


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


class BoardCounts(NamedTuple):
    """What the board's events give per run: P by agent, the agents with a read that counts, R by (i, j), and each
    agent's calls: {"read": (tool call id, [message ids]) of its first read that counts, or None, "posts": [(tool call
    id, [message ids]), ...] of each call that posted}."""
    posts: dict[str, int]
    readers: set[str]
    takes_up: dict[tuple[str, str], int]
    calls: dict[str, dict]


def board_events(boards: list[dict]) -> Iterator[dict]:
    """The board's events in order: each board's, in its own order."""
    for board in boards:
        yield from board.get("events") or []


class Tally:
    """The board's running counts: add() one event at a time, counts() the counts so far."""

    def __init__(self, boards: list[dict], agents: list[str]) -> None:
        self.sender = {m.get("id"): m.get("sender") for board in boards for m in board.get("messages") or []}
        self.team = set(agents)
        self.seen: dict[str, set] = {a: set() for a in agents}
        self.fresh: dict[str, set] = {a: set() for a in agents}
        self.posts = dict.fromkeys(agents, 0)
        self.takes_up: dict[tuple[str, str], int] = {}
        self.readers: set[str] = set()
        self.calls: dict[str, dict] = {a: {"read": None, "posts": []} for a in agents}

    def add(self, event: dict) -> bool:
        """Count one event; False for an event of an agent outside the run or of a kind other than read and sent,
        which counts for nothing."""
        agent, kind, ids = event.get("agent_id"), event.get("kind"), event.get("message_ids") or []
        if agent not in self.team or kind not in COUNTED:
            return False
        if kind == "read":
            counted = [m for m in ids if self.sender.get(m) in self.team and self.sender.get(m) != agent]
            for message in counted:
                if message not in self.seen[agent]:
                    self.fresh[agent].add(self.sender[message])
            if counted:
                self.readers.add(agent)
                if self.calls[agent]["read"] is None:
                    self.calls[agent]["read"] = (event.get("tool_call_id"), counted)
            self.seen[agent].update(ids)
        else:
            self.posts[agent] += 1
            self.calls[agent]["posts"].append((event.get("tool_call_id"), list(ids)))
            for author in self.fresh[agent]:
                self.takes_up[(agent, author)] = self.takes_up.get((agent, author), 0) + 1
            self.fresh[agent] = set()
        return True

    def counts(self) -> BoardCounts:
        return BoardCounts(self.posts, self.readers, self.takes_up, self.calls)


def board_counts(boards: list[dict], agents: list[str]) -> BoardCounts:
    """The board's counts over its events in order (Tally). Events of an agent outside `agents` and kinds other than
    read and sent are ignored."""
    tally = Tally(boards, agents)
    for event in board_events(boards):
        tally.add(event)
    return tally.counts()


def measure(counts: BoardCounts, agents: list[str]) -> dict:
    """Per agent: posts, read, takes_up {teammate: R_ij}, mbar, ceiling and level; per pair: rounds M_ij; and each
    agent's calls (BoardCounts.calls), from the counts. In a one-agent run mbar, ceiling and level are None (no
    coordination level applies)."""
    posts, readers, takes_up, calls = counts
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
    return {"agents": out, "pairs": pairs, "calls": calls}


def board_coordination(boards: list[dict], agents: list[str]) -> dict:
    """The measure (measure) of the board's counts after its last event."""
    agents = list(dict.fromkeys(agents))
    return measure(board_counts(boards, agents), agents)


def team_level(levels: dict[str, str | None], roles: dict[str, str | None]) -> dict:
    """{"team": the workers' mean level number rounded half up, named; None for a one-agent run or no worker,
    "influencers": {agent: level} of every influencer (run_directory.is_influencer)}. An agent missing from
    `roles` is a worker."""
    workers = [lvl for a, lvl in levels.items() if is_worker(roles.get(a))]
    influencers = {a: lvl for a, lvl in levels.items() if is_influencer(roles.get(a))}
    if len(levels) < 2 or not workers or any(lvl is None for lvl in workers):
        return {"team": None, "influencers": influencers}
    mean = Fraction(sum(LEVELS.index(lvl) for lvl in workers), len(workers))
    return {"team": LEVELS[math.floor(mean + Fraction(1, 2))], "influencers": influencers}


def _levels(rows: dict[str, dict]) -> dict[str, str | None]:
    return {a: row["level"] for a, row in rows.items()}


def coordination_steps(boards: list[dict], roles: dict[str, str | None]) -> list[dict]:
    """After each event that counts, in the board's order: {"sequence", "agent" (whose event), "kind", "call_id",
    "agents": {agent: the measure's row so far}, "team": the team's level so far}. The last step's rows are
    board_coordination's."""
    agents = list(dict.fromkeys(roles))
    tally = Tally(boards, agents)
    steps = []
    for event in board_events(boards):
        if tally.add(event):
            rows = measure(tally.counts(), agents)["agents"]
            steps.append({"sequence": event.get("sequence"), "agent": event.get("agent_id"), "kind": event["kind"],
                          "call_id": event.get("tool_call_id"), "agents": rows,
                          "team": team_level(_levels(rows), roles)["team"]})
    return steps


def held_levels(steps: list[dict], start: dict[str, dict]) -> dict[str, list[dict]]:
    """agent -> each change of the level it holds, at its own board calls (the module's rule): {"call_id" (None after
    its last board call), "level", "posts", "mbar", "ceiling" (its counts so far), "by" (the teammate whose event made
    the last change, None when its own)}. `start`: every agent's row before any event."""
    shown = _levels(start)
    now = dict(shown)
    by: dict[str, str | None] = dict.fromkeys(start)
    out: dict[str, list[dict]] = {a: [] for a in start}

    def change(a: str, call_id: str | None, row: dict) -> None:
        out[a].append({"call_id": call_id, **{k: row[k] for k in ("level", "posts", "mbar", "ceiling")},
                       "by": by[a] if by[a] != a else None})
        shown[a] = row["level"]

    for step in steps:
        for a, row in step["agents"].items():
            if row["level"] != now[a]:
                now[a], by[a] = row["level"], step["agent"]
        if now[step["agent"]] != shown[step["agent"]]:
            change(step["agent"], step["call_id"], step["agents"][step["agent"]])
    for a in start:
        if now[a] != shown[a]:
            change(a, None, steps[-1]["agents"][a])
    return out


def team_steps(steps: list[dict], start: str | None) -> list[dict]:
    """Each event that changed the team's level (`start` before any event): {"call_id", "agent", "level"}."""
    out, now = [], start
    for step in steps:
        if step["team"] != now:
            now = step["team"]
            out.append({"call_id": step["call_id"], "agent": step["agent"], "level": now})
    return out


def coordination_of(boards: list[dict], roles: dict[str, str | None]) -> dict:
    """The Coordination ladder of board records for agents with these roles: {"agents", "pairs", "calls", "roles",
    "team", "influencers", "held" (held_levels), "team_steps"}."""
    result = board_coordination(boards, list(roles))
    start = board_coordination([], list(roles))["agents"]
    steps = coordination_steps(boards, roles)
    before = team_level(_levels(start), roles)["team"]
    return {**result, "roles": roles, **team_level(_levels(result["agents"]), roles),
            "held": held_levels(steps, start), "team_steps": team_steps(steps, before)}


def run_coordination(run_dir: Path) -> dict:
    """The run's Coordination ladder (coordination_of) from its board records (RunDir.boards); a run without them has
    every agent at none."""
    run = RunDir(run_dir).require()
    return coordination_of(run.boards(), run.roles())


# --------------------------------------------------------------------------
# what a run records
# --------------------------------------------------------------------------
AGENT_FIELDS = {"coordination_level": "level"}
"""An agent row's Coordination fields and the keys of an agent's measure they come from."""
TEAM_FIELDS = {"coordination_level": "team", "coordination_influencers": "influencers"}
"""The team row's Coordination fields and the keys of the measure they come from."""


def record_fields(result: dict) -> tuple[dict[str, dict], dict]:
    """What a run records of its Coordination ladder (`result`, coordination_of's answer): {agent: the agent row's
    AGENT_FIELDS} and the team row's TEAM_FIELDS."""
    agents = {agent: {field: row[key] for field, key in AGENT_FIELDS.items()}
              for agent, row in result["agents"].items()}
    return agents, {field: result[key] for field, key in TEAM_FIELDS.items()}
