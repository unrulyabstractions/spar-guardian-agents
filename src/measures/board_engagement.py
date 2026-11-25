"""The board engagement funnel of a run directory, per agent and per role.

    measures = run_measures(Path("runs/<study>/<run>"))

The funnel counts the agents of a role that were on a board, then those that looked at it (board_status or
board_read), read it, posted to it, and called submit (accepted or refused). Each agent row is the agent's
`engagement` block with the stages it reached. The numbers come from the `engagement` blocks src/export/run_export.py
writes into episodes.jsonl and run.json, and the team's submission from run.json `team_submission`.
"""
from __future__ import annotations

from pathlib import Path

from src.measures.run_directory import RunDir

STAGES = ("on_board", "looked", "read", "posted", "called_submit")


def stage_flags(engagement: dict) -> dict[str, bool]:
    """Which stages of the funnel one agent reached."""
    return {"on_board": True,
            "looked": bool(engagement.get("checks") or engagement.get("reads")),
            "read": bool(engagement.get("reads")),
            "posted": bool(engagement.get("posts")),
            "called_submit": bool(engagement.get("called_submit"))}


def agent_rows(episodes: list[dict]) -> list[dict]:
    """One row per episode that carries an engagement block."""
    rows = []
    for ep in episodes:
        engagement = ep.get("engagement")
        if engagement:
            rows.append({"agent": ep.get("agent"), "role": ep.get("role") or "worker", **engagement,
                         **{f"reached_{k}": v for k, v in stage_flags(engagement).items()}})
    return rows


def funnel(rows: list[dict]) -> dict[str, dict[str, int]]:
    """{role: {stage: agents}}, with "all" for every role together."""
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        for role in ("all", row["role"]):
            counts = out.setdefault(role, dict.fromkeys(STAGES, 0))
            for stage in STAGES:
                counts[stage] += int(row[f"reached_{stage}"])
    return out


def run_measures(run_dir: Path) -> dict:
    """The run's engagement: its run.json block, the team's submission, the funnel and the agent rows."""
    run = RunDir(run_dir).require()
    rows = agent_rows(run.episodes())
    keys = ("mode", "scale", "preset", "engagement", "team_submission")
    return {"run": run.name, **{key: run.run_json.get(key) for key in keys}, "funnel": funnel(rows), "agents": rows}

