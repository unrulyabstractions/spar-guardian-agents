"""The run's calls as the Collusion ladder's take rules read them, and the take they find.

    run = load_run(Path("runs/study_exploration_1/flipt_minimal_luna_fixed2"), times=True)
    run.calls["k7q2m"][4]        # Call(agent, k, turn, start, end, tool, id, command, arguments, result)
    run.ordered()                # every agent's calls by turn, then by the eval log's clock
    Take("k7q2m", "t4x9n", "git", 12, 3, "[12] git merge team/t4x9n")

The calls are numbered as the transcript scan numbers them, and the boards are read through RunDir.boards. The take
rules are src/measures/git_takes.py, text_takes.py and file_takes.py; each task's choice among them is
src/measures/collusion_takes.py.

Pure: no model call, nothing written.
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

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
    """tool call id -> (start, end) in seconds, from the eval log's tool events, read as the zip it is. {} without a
    log."""
    if path is None or not path.is_file():
        return {}
    out: dict[str, tuple[float, float | None]] = {}
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.startswith("samples/"):
                continue
            for event in json.loads(archive.read(name)).get("events") or []:
                if event.get("event") == "tool" and event.get("timestamp"):
                    end = event.get("completed")
                    out[event.get("id")] = (datetime.fromisoformat(event["timestamp"]).timestamp(),
                                            datetime.fromisoformat(end).timestamp() if end else None)
    return out


def load_run(run_dir: Path, times: bool = False) -> Run:
    """The run's records. Each agent step takes the turn of the agent's matching activation in rules.timeline; when
    the counts differ the agent's turns are None and a note says so. `times` reads the eval log's tool events."""
    run = RunDir(run_dir).require()
    rj = run.run_json
    roles = run.roles()
    timeline = (rj.get("rules") or {}).get("timeline") or []
    clock = eval_times(run.eval_log) if times else {}
    calls, prompts, notes = {}, {}, []
    for ep in run.with_transcripts():
        agent = ep.get("orbit_agent") or (ep.get("agent") or "").split("/")[-1]
        transcript = run.transcript(ep["episode"])
        steps = transcript["trajectory"]["steps"]
        prompts[agent] = "\n".join(str(s.get("message") or "") for s in steps if s.get("source") in ("system", "user"))
        turns = [e.get("turn") for e in timeline if e.get("agent") == agent]
        agent_steps = [s for s in steps if s.get("source") == "agent"]
        if len(turns) != len(agent_steps):
            notes.append(f"{agent}: {len(agent_steps)} steps but {len(turns)} timeline entries; no turns")
            turns = [None] * len(agent_steps)
        turn_of = {}
        for turn, step in zip(turns, agent_steps):
            for tc in step.get("tool_calls") or []:
                turn_of[tc.get("tool_call_id")] = turn
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

