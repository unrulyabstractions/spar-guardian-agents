"""A run's state after every call, as a faithful replay recorded it (src/replay: steps.jsonl behind replay.json's gate),
for the ladders that read each rung at the call that earned it.

    steps, notes = load_steps(Path("OLD/runs/ian_baseline_explorations_1/flipt_minimal_cheat_dsflash_fixed3"), call_ids)
    steps is None                    # no steps.jsonl, or one refused (notes say why): the record is the last step
    line = steps.of("k4q8z")[51]     # Line(n, agent, k, call_id, tool, state, changed)
    line.tree()                      # the pieces its working tree holds after the call
    line.branches()                  # {piece: [its own branches holding it]}
    line.team()                      # {piece: [team remote refs holding it]}
    line.changed                     # the team remote's refs this call changed
    steps.position("k4q8z", 51)      # n, the call's place in the run's one order
    steps.accepted_submit(run_json["team_submission"])      # the line of the team's accepted hand-in, or None

A state's pieces carry no seen gate: tree() is the replay's flag, and a reader applies its own gate, on the line hits
the state keeps beside the flag where the task's rule needs them (src/measures/cheating_steps.py). A branch is a ref
under refs/heads/: the remote-tracking refs are other repositories' copies, which a fetch updates whatever it names.
Keys of the state other than those read here (a shared workspace's files, a checker's inputs) are the task's and pass
through untouched in Line.state.

The replay's lines must be the run's calls: each agent's lines number its calls 0, 1, ... with the call ids its
transcript holds, or the file is refused with a note and the stored record is read alone. Pure: nothing is written.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from src.measures.run_directory import RunDir
from src.replay.replay_report import REPORT, STEPS, NotFaithful, faithful_steps

BRANCH = "refs/heads/"
"""Where an agent's own branches live in its repository."""


class Line(NamedTuple):
    """One call and the state after it. `changed` names the team remote's refs this call changed: those whose commit
    differs from the line before it in the run's order (none on the run's first line)."""
    n: int
    agent: str
    k: int
    call_id: str
    tool: str
    state: dict
    changed: frozenset[str]

    def tree(self) -> set[str]:
        """The pieces the agent's working tree holds."""
        return {piece for piece, where in (self.state.get("pieces") or {}).items() if where.get("tree")}

    def branches(self) -> dict[str, list[str]]:
        """Each piece some branch of the agent's own holds, with those branches."""
        found = {piece: [ref for ref in where.get("refs") or [] if ref.startswith(BRANCH)]
                 for piece, where in (self.state.get("pieces") or {}).items()}
        return {piece: refs for piece, refs in found.items() if refs}

    def team(self) -> dict[str, list[str]]:
        """Each piece some ref of the team remote holds, with those refs."""
        return {piece: list(where["team"]) for piece, where in (self.state.get("pieces") or {}).items()
                if where.get("team")}

    def put(self) -> dict[str, list[str]]:
        """Each piece a team ref this call changed holds, with those refs: what the call put on the team remote."""
        return {piece: sorted(set(refs) & self.changed) for piece, refs in self.team().items()
                if set(refs) & self.changed}


@dataclass(frozen=True)
class RunSteps:
    """Every line of a faithful replay, in the run's order."""
    lines: tuple[Line, ...]

    def of(self, agent: str) -> list[Line]:
        """The agent's lines, by k."""
        return [line for line in self.lines if line.agent == agent]

    def position(self, agent: str, k: int) -> int:
        """n of the agent's call k."""
        return next(line.n for line in self.lines if line.agent == agent and line.k == k)

    def accepted_submit(self, submission: dict | None, unsubmits: dict | None = None) -> Line | None:
        """The submit that made the team's hand-in (run.json team_submission): the submitter's submit calls in order,
        past those refused before the hand-in's round and those of its hand-ins an unsubmit took back (run.json
        unsubmits; the standing hand-in is the last). None without a hand-in or a matching call."""
        if not submission or not submission.get("agent"):
            return None
        agent, round_number = submission["agent"], submission.get("round")
        submits = [line for line in self.of(agent) if line.tool == "submit"]
        refused = sum(1 for r in submission.get("refused") or []
                      if r.get("agent") == agent and round_number is not None and (r.get("round") or 0) < round_number)
        taken = sum(1 for u in (unsubmits or {}).get("unsubmits") or [] if u["submit"]["agent"] == agent)
        before = refused + taken
        return submits[before] if before < len(submits) else None


def lines_of(rows: list[dict]) -> tuple[Line, ...]:
    """The decoded rows of steps.jsonl as Lines, each with the team refs its call changed."""
    out, before = [], None
    for row in rows:
        team = row["state"].get("team_refs") or {}
        changed = frozenset() if before is None else \
            frozenset(ref for ref in set(team) | set(before) if team.get(ref) != before.get(ref))
        out.append(Line(row["n"], row["agent"], row["k"], row["call_id"], row["tool"], row["state"], changed))
        before = team
    return tuple(out)


def mismatch(lines: tuple[Line, ...], call_ids: dict[str, list[str]]) -> str | None:
    """Why the lines are not the run's calls (`call_ids`: each agent's call ids by k, from its transcript), or None."""
    for agent, ids in call_ids.items():
        mine = [(line.k, line.call_id) for line in lines if line.agent == agent]
        if mine != list(enumerate(ids)):
            return f"{agent}'s lines are not its {len(ids)} calls in order"
    strangers = {line.agent for line in lines} - set(call_ids)
    return f"lines of agents the run does not have: {', '.join(sorted(strangers))}" if strangers else None


def load_steps(run_dir: Path, call_ids: dict[str, list[str]]) -> tuple[RunSteps | None, list[str]]:
    """The run's steps and the notes on them. (None, []) for a run with neither steps.jsonl nor replay.json; (None,
    [why]) for a replay that is not faithful or whose lines are not the run's calls."""
    replay_dir = RunDir(run_dir).replay_dir
    if not (replay_dir / STEPS).is_file() and not (replay_dir / REPORT).is_file():
        return None, []
    try:
        lines = lines_of(faithful_steps(replay_dir))
    except (NotFaithful, FileNotFoundError) as refused:
        return None, [f"steps.jsonl not read: {refused}"]
    why = mismatch(lines, call_ids)
    if why:
        return None, [f"steps.jsonl not read: {why}"]
    return RunSteps(lines), []
