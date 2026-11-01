"""Whether a run measures what its cell says, read from what the run left on disk.

    uv run python -m src.measures.run_validity runs/study_exploration_1            # one line per run, and a count
    uv run python -m src.measures.run_validity runs/study_exploration_1 --index    # also writes <folder>/INDEX.jsonl

A run is valid when all of these hold, and each failure is named in `why`:
- whole: run.json and signals.jsonl exist, the eval log's header says success, and no episode failed;
- the task met the collaboration rule when it ran: an SWE run's code commit descends from SWE_FIX, the commit that
  made cheating on flipt-02e2 and vuls-1832 take two agents (every SWE run before it graded a task one agent could
  cheat alone);
- a regulator, when the cell has one, acted: it made at least one turn that was not the provider's refusal notice.
  On 2026-11-01 every turn of the Opus regulator in 7 protein runs was Anthropic's refusal notice.
Mock rehearsal runs (names ending in mock<k>) are listed as mock and never count as valid.
Each record also says whether the run is whole on its own, whatever the other conditions find.
"""
from __future__ import annotations

import argparse
import functools
import json
import re
import subprocess
import sys
from pathlib import Path

from src.configuration import settings

SWE_FIX = "7331882"
"""The commit after which the SWE task needs two agents to cheat (src/domains/swe, 2026-11-01)."""
REFUSAL_NOTICE = "API integrators: you can reduce refusals"
"""The text Anthropic's API returns in place of a turn it refuses."""
MOCK = re.compile(r"mock\d+$")


@functools.cache
def descends_from_fix(commit: str) -> bool | None:
    """Whether commit has SWE_FIX in its history; None when git does not know the commit."""
    r = subprocess.run(["git", "-C", str(settings.ROOT), "merge-base", "--is-ancestor", SWE_FIX, commit],
                       capture_output=True)
    return {0: True, 1: False}.get(r.returncode)


def _lines(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _eval_status(name: str, study_logs: Path) -> str | None:
    logs = sorted((study_logs / name).glob("*.eval"))
    if not logs:
        return None
    from inspect_ai.log import read_eval_log  # noqa: PLC0415 - heavy import, only when a log exists
    return read_eval_log(str(logs[-1]), header_only=True).status


def regulator_turns(run: Path, agent: str) -> tuple[int, int]:
    """(the agent's turns, how many were a refusal notice), from its trajectory."""
    turns = refusals = 0
    for transcript in run.glob(f"trajectories/*@{agent}/transcript.json"):
        steps = json.loads(transcript.read_text())["trajectory"]["steps"]
        for step in steps:
            if step.get("source") == "agent":
                turns += 1
                refusals += str(step.get("message") or "").startswith(REFUSAL_NOTICE)
    return turns, refusals


def validity(run: Path, study_logs: Path | None = None) -> dict:
    """One run's record: its facts and whether it is valid, with every reason it is not."""
    study_logs = study_logs or settings.ROOT / "logs" / run.parent.name
    name = run.name
    record: dict = {"run": name, "mock": bool(MOCK.search(name))}
    data = json.loads((run / "run.json").read_text()) if (run / "run.json").is_file() else None
    why: list[str] = []
    if data is None:
        record.update(whole=False, valid=False, why=["no run.json"])
        return record
    record.update(task=data.get("task"), commit=(data.get("code") or {}).get("commit"))
    status = _eval_status(name, study_logs)
    failed = sorted({e["failure"] for e in _lines(run / "episodes.jsonl") if e.get("failure")})
    team = next((r for r in _lines(run / "signals.jsonl") if r.get("row") == "team"), None)
    record.update(eval_status=status, failed_episodes=failed, rung=team.get("rung") if team else None)
    if status != "success":
        why.append(f"eval {status or 'log missing'}")
    if failed:
        why.append("failed episodes: " + ", ".join(failed))
    if team is None:
        why.append("no team row in signals.jsonl")
    record["whole"] = not why
    if data.get("task") == "software_engineering":
        fixed = descends_from_fix(record["commit"]) if record["commit"] else None
        record["swe_fixed"] = fixed
        if fixed is not True:
            why.append("SWE task before the fix: one agent could cheat alone" if fixed is False
                       else f"SWE commit {record['commit']} unknown to git")
    workers = set((data.get("rules") or {}).get("workers") or data.get("agents") or [])
    regulators = [a for a in data.get("agents") or [] if a not in workers]
    if (data.get("config") or {}).get("regulators"):
        posts = (data.get("rules") or {}).get("posts") or {}
        for agent in regulators:
            turns, refusals = regulator_turns(run, agent)
            acted = turns > refusals
            record.setdefault("regulators", []).append(
                {"agent": agent, "model": (data.get("role_models") or {}).get("regulator"), "turns": turns,
                 "refusals": refusals, "posts": posts.get(agent, 0), "acted": acted})
            if not turns:
                why.append(f"regulator {agent} made no turn")
            elif not acted:
                why.append(f"regulator {agent} never acted: {refusals} of {turns} turns were refusal notices")
    if record["mock"]:
        why.append("mock rehearsal")
    record.update(valid=not why, why=why)
    return record


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path, help="a study's runs folder, e.g. runs/study_exploration_1")
    parser.add_argument("--index", action="store_true", help="write <folder>/INDEX.jsonl, one record per run")
    args = parser.parse_args(argv)
    runs = sorted(p for p in args.folder.iterdir() if p.is_dir() and (p / "run.json").is_file())
    records = [validity(run) for run in runs]
    for r in records:
        print(f"{'valid  ' if r['valid'] else 'INVALID'} {r['run']:58s} {'; '.join(r['why'])}")
    real = [r for r in records if not r["mock"]]
    print(f"== {len(real)} runs: {sum(r['valid'] for r in real)} valid, {sum(not r['valid'] for r in real)} not; "
          f"{len(records) - len(real)} mock rehearsals")
    if args.index:
        out = args.folder / "INDEX.jsonl"
        out.write_text("".join(json.dumps(r) + "\n" for r in records))
        print(f"== wrote {out} ({len(records)} records)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
