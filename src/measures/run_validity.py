"""Whether a run measures what its cell says, read from what the run left on disk.

    uv run python -m src.measures.run_validity runs/study_exploration_1     # one line per run, and a count

It reads every run of a study: at its top level and in its groups (run_directory.study_runs). A run's eval log is the
one run.json names, found through RunDir.eval_log. src/measures/run_index.py writes each run's record, with these
checks, to the study's INDEX.jsonl.

A run is valid when all of these hold. `why` names each failure, and `reason` the first in the order of REASONS:
- it is no mock rehearsal: a name ending in mock<k> never counts;
- it completed: the eval log's header says success, and no episode died in the harness or at the provider
  (settings.INCOMPLETE_FAILURES);
- the task met the collaboration rule when it ran: an SWE run's code commit descends from SWE_FIX;
- a regulator, when the cell has one, acted: it made at least one turn that was not the provider's refusal notice.
  On 2026-11-01 every turn of the Opus regulator in 7 protein runs was Anthropic's refusal notice;
- the code ladder placed the team: signals.jsonl holds a team row;
- no other episode failed, as by a timeout.
Each record also says whether the run is whole on its own, whatever the other conditions find: its eval log says
success, no episode failed and signals.jsonl holds a team row.
"""
from __future__ import annotations

import argparse
import collections
import functools
import json
import re
import subprocess
import sys
from pathlib import Path

from src.configuration import settings
from src.measures.run_directory import RunDir, run_place, study_runs

SWE_FIX = "f0d1c34"
"""The first commit where the SWE task meets the collaboration rule in full (CLAUDE.md section 1): every planted piece
together passes, and no agent's own holdings pass. 7331882 put the two texts the hidden tests need in two pieces, so
no agent could pass alone. It planted redis-ca-invalid.yml nowhere, so the pieces together did not pass without the
team's own work. f0d1c34 plants that file in piece A with neutral values. No SWE run recorded a commit between the two:
on 2026-11-01, every SWE run under runs/study_exploration_0 and runs/study_exploration_1 descends from both or from
neither."""
REFUSAL_NOTICE = "API integrators: you can reduce refusals"
"""The text Anthropic's API returns in place of a turn it refuses."""
MOCK = re.compile(r"mock\d+$")
REASONS = ("no_run_json", "mock", "incomplete", "swe_before_fix", "swe_commit_unknown", "regulator_silent",
           "regulator_refused", "no_team_record", "failed_episodes")
"""Why a run is not valid, the first that applies naming it: no run.json; a mock rehearsal; an eval that did not
complete; an SWE run before SWE_FIX, or on a commit git does not know; a regulator that made no turn, or only refusal
notices; no team row in signals.jsonl; an episode that failed otherwise."""


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


def eval_status(run: Path) -> str | None:
    """The status in the header of the run's eval log (RunDir.eval_log); None when the log is missing."""
    log = RunDir(run).eval_log
    if log is None:
        return None
    from inspect_ai.log import read_eval_log  # noqa: PLC0415 - heavy import, only when a log exists
    return read_eval_log(str(log), header_only=True).status


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


def validity(run: Path) -> dict:
    """One run's record: its facts, whether it completed, whether it is valid, every reason it is not (`why`, with
    their codes in `reasons`) and the first of them as a code (`reason`, one of REASONS, or "valid")."""
    name = run.name
    record: dict = {"run": name, "mock": bool(MOCK.search(name))}
    data = json.loads((run / "run.json").read_text()) if (run / "run.json").is_file() else None
    why: list[tuple[str, str]] = []
    if data is None:
        return finish({**record, "whole": False}, [("no_run_json", "no run.json")])
    record.update(task=data.get("task"), commit=(data.get("code") or {}).get("commit"))
    status = eval_status(run)
    failed = sorted({e["failure"] for e in _lines(run / "episodes.jsonl") if e.get("failure")})
    died = [failure for failure in failed if failure in settings.INCOMPLETE_FAILURES]
    team = next((r for r in _lines(run / "signals.jsonl") if r.get("row") == "team"), None)
    record.update(eval_status=status, completed=status == "success" and not died, failed_episodes=failed,
                  rung=team.get("rung") if team else None)
    if record["mock"]:
        why.append(("mock", "mock rehearsal"))
    if status != "success":
        why.append(("incomplete", f"eval {status or 'log missing'}"))
    if died:
        why.append(("incomplete", "failed episodes: " + ", ".join(died)))
    record["whole"] = status == "success" and not failed and team is not None
    if data.get("task") == "software_engineering":
        fixed = descends_from_fix(record["commit"]) if record["commit"] else None
        record["swe_fixed"] = fixed
        if fixed is False:
            why.append(("swe_before_fix", "SWE task before the fix: one agent could cheat alone"))
        elif fixed is None:
            why.append(("swe_commit_unknown", f"SWE commit {record['commit']} unknown to git"))
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
                why.append(("regulator_silent", f"regulator {agent} made no turn"))
            elif not acted:
                why.append(("regulator_refused",
                            f"regulator {agent} never acted: {refusals} of {turns} turns were refusal notices"))
    if team is None:
        why.append(("no_team_record", "no team row in signals.jsonl"))
    others = [failure for failure in failed if failure not in died]
    if others:
        why.append(("failed_episodes", "failed episodes: " + ", ".join(others)))
    return finish(record, why)


def finish(record: dict, why: list[tuple[str, str]]) -> dict:
    """The record with valid, why (each failure's text, in the order found), reasons (each failure's code, in the
    same order) and reason (the first code of REASONS that applies, or "valid")."""
    return {**record, "valid": not why, "why": [text for _, text in why], "reasons": [code for code, _ in why],
            "reason": first_reason([code for code, _ in why])}


def first_reason(codes: list[str]) -> str:
    """The first code of REASONS among these codes, or "valid" for none."""
    return next((code for code in REASONS if code in codes), "valid")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path, help="a study's runs folder, e.g. runs/study_exploration_1")
    args = parser.parse_args(argv)
    runs = study_runs(args.folder)
    records = [validity(run) for run in runs]
    for run, r in zip(runs, records):
        _, group = run_place(run)
        path = f"{group}/{run.name}" if group else run.name
        print(f"{'valid  ' if r['valid'] else 'INVALID'} {path:66s} {'; '.join(r['why'])}")
    real = [r for r in records if not r["mock"]]
    print(f"== {len(real)} runs: {sum(r['valid'] for r in real)} valid, {sum(not r['valid'] for r in real)} not; "
          f"{len(records) - len(real)} mock rehearsals")
    reasons = collections.Counter(r["reason"] for r in records)
    print("== by reason: " + ", ".join(f"{code} {reasons[code]}" for code in ("valid", *REASONS) if reasons[code]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
