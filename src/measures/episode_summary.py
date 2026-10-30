"""One line per episode of a run directory, and the turns of lone agents' runs.

    print(HEADER)
    for line in summary_lines(Path("runs/gemini_riemann"), full=True):
        print(line)
    print("\\n".join(budget_lines([Path("runs/om_lone_1"), Path("runs/om_lone_2")])))

An EpisodeSummary joins an episode's record (episodes.jsonl) with its verdict (judge.jsonl): its role, tool calls,
wall time and failure, whether the judge says it cheated, and the task's other judge flags that hold
(src/measures/verdict_fields.py). A run's header lines give its swarm composition, and its shutdown rate (the share
of the team's agents that ended with stop_working) when run.json records one; with full each episode's line is
followed by the judge's summary. budget_lines gives each lone run's n_turns and their median, how long one agent
takes alone, beside the turn budget (settings.TURN_BUDGET).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path

from src.measures.run_directory import RunDir
from src.measures.verdict_fields import flag_names

HEADER = f"{'run':24} {'episode':18} {'role':12} {'calls':>5} {'s':>5} {'fail':9} {'cheat':5}  flags"
SUMMARY_CHARS = 1200


def yes_no(value) -> str:
    return {True: "yes", False: "no"}.get(value, "--")


@dataclass(frozen=True)
class EpisodeSummary:
    """One episode's summary row, from its record and its verdict."""

    run: str
    episode: str
    role: str | None
    calls: int | None
    seconds: float | None
    failure: str | None
    cheated: bool | None
    flags: tuple[str, ...]
    """The task's judge flags that hold, cheated aside."""
    summary: str
    """The judge's summary, "" without a verdict."""

    @classmethod
    def of(cls, run: str, episode: dict, verdict: dict | None) -> EpisodeSummary:
        verdict = verdict or {}
        flags = tuple(f for f in flag_names(episode.get("task")) if f != "cheated" and verdict.get(f) is True)
        return cls(run=run, episode=episode["episode"], role=episode.get("role"), calls=episode.get("n_tool_calls"),
                   seconds=episode.get("wall_time"), failure=episode.get("failure"), cheated=verdict.get("cheated"),
                   flags=flags, summary=verdict.get("summary") or "")

    def line(self) -> str:
        calls = "-" if self.calls is None else self.calls
        seconds = "-" if self.seconds is None else round(self.seconds)
        return (f"{self.run:24} {self.episode:18} {self.role or '-':12} {calls:>5} {seconds:>5} "
                f"{self.failure or '-':9} {yes_no(self.cheated):5}  {' '.join(self.flags)}")


def summary_lines(run_dir: Path, full: bool = False) -> list[str]:
    """The run's header lines and one line per episode, each followed by the judge's summary when full."""
    run = RunDir(run_dir)
    episodes = run.episodes()
    if not episodes:
        return [f"{run.name:24} (no episodes)"]
    swarm, rate = run.run_json.get("swarm"), run.run_json.get("shutdown_rate")
    lines = []
    if swarm:
        lines.append(f"{run.name:24} swarm {swarm.get('composition')} seed {swarm.get('seed')}")
    if rate is not None:
        lines.append(f"{run.name:24} shutdown rate {rate:.2f} (team agents that ended with stop_working before any "
                     "hand-in)")
    verdicts = run.verdicts()
    for episode in episodes:
        row = EpisodeSummary.of(run.name, episode, verdicts.get(episode["episode"]))
        lines.append(row.line())
        if full and row.summary:
            lines.append("    " + row.summary[:SUMMARY_CHARS])
    return lines


def lone_turns(run_dir: Path) -> list[int]:
    """n_turns of every episode of a lone agent's run. Raises ValueError for a run without episodes or with a team."""
    episodes = RunDir(run_dir).episodes()
    if not episodes:
        raise ValueError(f"{run_dir}: no episodes")
    if any(len(ep.get("orbit_roster") or [ep.get("orbit_agent")]) != 1 for ep in episodes):
        raise ValueError(f"{run_dir} is not a lone agent's run: an episode has a roster of several agents")
    return [int(ep["n_turns"]) for ep in episodes]


def budget_lines(run_dirs: list[Path]) -> list[str]:
    """Each lone run's n_turns, then their median over every episode."""
    lines, turns = [], []
    for run_dir in run_dirs:
        mine = lone_turns(run_dir)
        lines.append(f"{run_dir}: {len(mine)} episode(s) of a lone agent, n_turns {', '.join(map(str, mine))}")
        turns += mine
    return [*lines, f"median {int(statistics.median(turns))} over {len(turns)} lone episode(s)"]
