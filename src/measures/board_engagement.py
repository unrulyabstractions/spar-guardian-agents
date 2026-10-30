"""The board engagement funnel of a run directory, per agent and per role.

    measures = run_measures(Path("runs/swarm_R3D3"))
    print(format_run(measures))

`python -m src engagement <run ...> [--json]` prints it. The funnel counts the agents of a role that were on a board,
then those that looked at it (board_status or board_read), read it, posted to it, and submitted for the team. Each
agent line gives its round of discovery, its checks, reads and posts, its working and free steps, the board_read and
board_post calls the per-turn budgets refused, its agent_actions queries (a regulator's oversight), and whom it read
and queried; "--" stands for a value the run did not record. The numbers come from the `engagement` blocks
src/export/run_export.py writes into episodes.jsonl and run.json. A run without a board says so in one line.
"""
from __future__ import annotations

from pathlib import Path

from src.measures.run_directory import RunDir

STAGES = ("on_board", "looked", "read", "posted", "team_submitted")
AGENT_HEADER = (f"{'agent':8} {'role':12} {'found':>5} {'chk':>4} {'read':>4} {'post':>4} {'work':>4} {'free':>4} "
                f"{'rref':>4} {'pref':>4} {'ovs':>4} {'team':4}  read_from; queried")


def stage_flags(engagement: dict) -> dict[str, bool]:
    """Which stages of the funnel one agent reached."""
    return {"on_board": True,
            "looked": bool(engagement.get("checks") or engagement.get("reads")),
            "read": bool(engagement.get("reads")),
            "posted": bool(engagement.get("posts")),
            "team_submitted": bool(engagement.get("team_submitted"))}


def agent_rows(episodes: list[dict]) -> list[dict]:
    """One row per episode that carries an engagement block."""
    rows = []
    for ep in episodes:
        engagement = ep.get("engagement")
        if engagement:
            rows.append({"agent": ep.get("orbit_agent"), "role": ep.get("role") or "worker", **engagement,
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
    """The run's engagement: its run.json block, the funnel and the agent rows."""
    run = RunDir(run_dir).require()
    rows = agent_rows(run.episodes())
    return {"run": run.name, **{key: run.run_json.get(key) for key in ("mode", "scale", "preset", "engagement")},
            "funnel": funnel(rows), "agents": rows}


def _cell(value) -> str:
    return "--" if value is None else str(value)


def format_run(measures: dict) -> str:
    """The run's funnel and agent table as text."""
    head = (f"{measures['run']}  mode {_cell(measures['mode'])}  scale {_cell(measures['scale'])}  "
            f"preset {_cell(measures['preset'])}")
    if not measures["agents"]:
        return f"{head}\n  no board engagement recorded (no board)"
    lines = [head, f"  {'role':12} " + " ".join(f"{s:>14}" for s in STAGES)]
    for role, counts in sorted(measures["funnel"].items(), key=lambda kv: (kv[0] != "all", kv[0])):
        lines.append(f"  {role:12} " + " ".join(f"{counts[s]:>14}" for s in STAGES))
    run_block = measures["engagement"] or {}
    lines.append(f"  median discovery round {_cell(run_block.get('median_discovery_round'))}  "
                 f"team submission {_cell((run_block.get('team_submission') or {}).get('agent'))}  "
                 f"reads refused {_cell(run_block.get('reads_refused'))}  "
                 f"posts refused {_cell(run_block.get('posts_refused'))}  "
                 f"oversight {_cell(run_block.get('oversight'))}")
    lines.append("  " + AGENT_HEADER)
    for row in measures["agents"]:
        read_from = ", ".join(f"{s} {n}" for s, n in sorted((row.get("read_senders") or {}).items()))
        queried = ", ".join(f"{s} {n}" for s, n in sorted((row.get("oversight_targets") or {}).items()))
        lines.append(f"  {_cell(row['agent']):8} {row['role']:12} {_cell(row.get('discovery_round')):>5} "
                     f"{_cell(row.get('checks')):>4} {_cell(row.get('reads')):>4} {_cell(row.get('posts')):>4} "
                     f"{_cell(row.get('work_steps')):>4} {_cell(row.get('free_steps')):>4} "
                     f"{_cell(row.get('reads_refused')):>4} {_cell(row.get('posts_refused')):>4} "
                     f"{_cell(row.get('oversight')):>4} "
                     f"{'yes' if row.get('team_submitted') else 'no':4}  {read_from}"
                     f"{'; ' + queried if queried else ''}")
    return "\n".join(lines)
