"""The run's calls as the Collusion ladder's take rules read them, and the take they find.

    run = load_run(Path("OLD/runs/ian_baseline_explorations_1/flipt_minimal_luna_fixed2"), times=True)
    run.calls["k7q2m"][4]        # Call(agent, k, turn, start, end, tool, id, command, arguments, result)
    run.ordered()                # every agent's calls by turn, then by the eval log's clock
    Take("k7q2m", "t4x9n", "git", 12, 3, "[12] git merge team/t4x9n")

The calls are numbered as the transcript scan numbers them, and the boards are read through RunDir.boards. The take
rules are src/measures/git_takes.py, text_takes.py and file_takes.py; each task's choice among them is
src/measures/collusion_takes.py.

Pure: no model call, nothing written.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from src.measures.eval_log_events import tool_events
from src.measures.run_directory import RunDir
from src.measures.shell_commands import REFUSED, TOOLS, command_of, results_of, tool_calls_of


class Call(NamedTuple):
    """One tool call of one agent: its number as the transcript scan numbers it, its turn (rules.timeline), its start
    and end in the eval log (None without one), its tool, its command or code ("" for other tools) and its result."""
    agent: str
    k: int
    turn: int | None
    start: float | None
    end: float | None
    tool: str
    id: str
    command: str
    arguments: dict
    result: str

    @property
    def ran(self) -> bool:
        """Whether a shell or python call ran: the tool's schema did not refuse it."""
        return self.tool in TOOLS and REFUSED not in self.result


class Take(NamedTuple):
    """i (taker) took j's (giver's) work, by a rule, at a call (None for what a final record shows)."""
    taker: str
    giver: str
    rule: str
    call: int | None
    turn: int | None
    evidence: str

    def record(self) -> dict:
        return self._asdict()


@dataclass
class Run:
    """What the rules read of one run: run.json, the roles, every agent's calls, prompt and the boards."""
    path: Path
    run_json: dict
    roles: dict[str, str | None]
    calls: dict[str, list[Call]]
    prompts: dict[str, str]
    boards: list[dict]
    notes: list[str] = field(default_factory=list)
    cache: dict = field(default_factory=dict)

    @property
    def agents(self) -> list[str]:
        return list(self.roles)

    def ordered(self) -> list[Call]:
        """Every call of every agent: by turn, then by eval-log start, then by agent and number."""
        return sorted((c for rows in self.calls.values() for c in rows),
                      key=lambda c: (c.turn if c.turn is not None else -1, c.start or 0.0, c.agent, c.k))

    @property
    def messages(self) -> list[dict]:
        return [m for board in self.boards for m in board.get("messages") or []]

    @property
    def reads(self) -> list[dict]:
        return [e for board in self.boards for e in board.get("events") or [] if e.get("kind") == "read"]


def eval_times(path: Path | None) -> dict[str, tuple[float, float | None]]:
    """tool call id -> (start, end) in seconds, from the eval log's tool events, streamed (eval_log_events). {}
    without a log."""
    if path is None or not path.is_file():
        return {}
    return {e.id: (e.timestamp.timestamp(), e.completed.timestamp() if e.completed else None)
            for e in tool_events(path) if e.timestamp}


def steps_turns(agent: str, entries: list[dict], steps: list[dict], notes: list[str]) -> dict:
    """tool call id -> turn in a run before 2026-11-21, where a timeline entry is one model step: the agent's steps
    and its entries matched one to one. When the counts differ the turns are None and a note says so."""
    turns = [e.get("turn") for e in entries]
    agent_steps = [s for s in steps if s.get("source") == "agent"]
    if len(turns) != len(agent_steps):
        notes.append(f"{agent}: {len(agent_steps)} steps but {len(turns)} timeline entries; no turns")
        turns = [None] * len(agent_steps)
    return {tc.get("tool_call_id"): turn for turn, step in zip(turns, agent_steps)
            for tc in step.get("tool_calls") or []}


def load_run(run_dir: Path, times: bool = False) -> Run:
    """The run's records. Each call takes the turn of its round in rules.timeline, by its id (a round entry lists its
    calls). In a run before 2026-11-21 each agent step takes the turn of the agent's matching activation instead; when
    the counts differ the agent's turns are None and a note says so. `times` reads the eval log's tool events."""
    run = RunDir(run_dir).require()
    rj = run.run_json
    roles = run.roles()
    timeline = (rj.get("rules") or {}).get("timeline") or []
    clock = eval_times(run.eval_log) if times else {}
    calls, prompts, notes = {}, {}, []
    for ep in run.with_transcripts():
        agent = ep.get("agent")
        transcript = run.transcript(ep["episode"])
        steps = transcript["trajectory"]["steps"]
        prompts[agent] = "\n".join(str(s.get("message") or "") for s in steps if s.get("source") in ("system", "user"))
        mine = [e for e in timeline if e.get("agent") == agent]
        if any("calls" in e for e in mine):
            turn_of = {call.get("id"): e.get("turn") for e in mine for call in e.get("calls") or []}
        else:
            turn_of = steps_turns(agent, mine, steps, notes)
        results = results_of(transcript)
        rows = []
        for k, tc in tool_calls_of(transcript):
            cid = tc.get("tool_call_id")
            start, end = clock.get(cid, (None, None))
            name = tc.get("function_name")
            rows.append(Call(agent, k, turn_of.get(cid), start, end, name, cid,
                             command_of(tc) if name in TOOLS else "", tc.get("arguments") or {},
                             results.get(cid, "") or ""))
        calls[agent] = rows
    for agent in roles:
        calls.setdefault(agent, [])
    return Run(run.path, rj, roles, calls, prompts, run.boards(), notes)

