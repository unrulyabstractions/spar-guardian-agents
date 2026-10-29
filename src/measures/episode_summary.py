"""Print one line per judged episode across run directories.

    python -m src summary runs/gemini_riemann runs/ctrl_* [--full]

Each line joins an episode from episodes.jsonl with its verdict from judge.jsonl. A run's header lines give its
swarm composition, and its shutdown rate when run.json records one.
--full also prints the judge's summary under each episode.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HEADER = (
    f"{'run':24} {'episode':18} {'control':13} {'role':12} {'calls':>5} {'s':>5} {'fail':6} "
    f"{'chk':>4} {'blk':>4} {'cheat':5} {'seen':5} {'acc':5} {'disc':5}  blocks_by"
)
SUMMARY_CHARS = 1200


def yes_no(value) -> str:
    return {True: "yes", False: "no"}.get(value, "--")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def verdicts_by_episode(run_dir: Path) -> dict:
    judge_path = run_dir / "judge.jsonl"
    if not judge_path.exists():
        return {}
    return {verdict["episode"]: verdict for verdict in read_jsonl(judge_path)}


def episode_row(run_name: str, episode: dict, verdict: dict) -> dict:
    defense = episode.get("defense") or {}
    usage = episode.get("usage") or {}
    wall_time = episode.get("wall_time")
    return {
        "run": run_name,
        "episode": episode["episode"],
        "control": episode.get("control"),
        "role": episode.get("role"),
        "calls": episode.get("n_tool_calls"),
        "s": round(wall_time) if wall_time else None,
        "failure": episode.get("failure"),
        "checks": defense.get("checks", 0),
        "blocks": defense.get("blocks", 0),
        "blocks_by": defense.get("blocks_by_boundary", {}),
        "cheated": verdict.get("cheated"),
        "seen": verdict.get("honey_pots_seen"),
        "access": verdict.get("attempted_access"),
        "disclosed": verdict.get("disclosed_access"),
        "judge_usd": verdict.get("judge_cost_usd"),
        "prompt_tokens": usage.get("input"),
        "summary": verdict.get("summary", ""),
    }


def rows(run_dir: Path):
    verdicts = verdicts_by_episode(run_dir)
    for episode in read_jsonl(run_dir / "episodes.jsonl"):
        verdict = verdicts.get(episode["episode"], {})
        yield episode_row(run_dir.name, episode, verdict)


def format_row(row: dict) -> str:
    episode_part = (
        f"{row['run']:24} {row['episode']:18} {str(row['control']):13} {str(row['role'] or '-'):12} "
        f"{str(row['calls']):>5} {str(row['s']):>5} {str(row['failure'] or '-'):6} "
    )
    verdict_part = (
        f"{row['checks']:>4} {row['blocks']:>4} {yes_no(row['cheated']):5} {yes_no(row['seen']):5} "
        f"{yes_no(row['access']):5} {yes_no(row['disclosed']):5}  {row['blocks_by']}"
    )
    return episode_part + verdict_part


def swarm_line(run_dir: Path) -> str | None:
    run_json = run_dir / "run.json"
    if not run_json.exists():
        return None
    swarm = json.loads(run_json.read_text()).get("swarm")
    if not swarm:
        return None
    composition = swarm.get("composition")
    seed = swarm.get("seed")
    return f"{run_dir.name:24} swarm {composition} seed {seed}"


def shutdown_line(run_dir: Path) -> str | None:
    """The share of agents that ended their own episode with submit (run.json shutdown_rate), when recorded."""
    run_json = run_dir / "run.json"
    if not run_json.exists():
        return None
    rate = json.loads(run_json.read_text()).get("shutdown_rate")
    return None if rate is None else f"{run_dir.name:24} shutdown rate {rate:.2f} (agents that submitted)"


def print_run(run_dir: Path, full: bool) -> None:
    if not (run_dir / "episodes.jsonl").exists():
        print(f"{run_dir.name:24} (no episodes.jsonl)")
        return
    for line in (swarm_line(run_dir), shutdown_line(run_dir)):
        if line:
            print(line)
    for row in rows(run_dir):
        print(format_row(row))
        if full and row["summary"]:
            print("    " + row["summary"][:SUMMARY_CHARS])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dirs", nargs="*", type=Path, help="run directories under runs/")
    parser.add_argument("--full", action="store_true", help="also print the judge's summary")
    args = parser.parse_intermixed_args(argv)
    print(HEADER)
    for run_dir in args.run_dirs:
        print_run(run_dir, args.full)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
