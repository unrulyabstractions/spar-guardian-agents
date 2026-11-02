"""Whether a run measures what its cell says, read from what the run left on disk.

    uv run python -m src.measures.run_validity runs/study_exploration_1     # one line per run, and a count

It reads every run of a study: at its top level and in its groups (run_directory.study_runs). A run's eval log is the
one run.json names, found through RunDir.eval_log. src/measures/run_index.py builds each run's record with these
checks, and scripts/box/study_index.py writes the records to the study's INDEX.jsonl.

A run is valid when all of these hold. `why` names each failure, and `reason` the first in the order of REASONS:
- it is no earlier attempt: a run in runs/<study>/attempts/ was superseded by a later run of its name;
- it is no mock rehearsal: a name ending in mock<k> never counts;
- it completed: the eval log's header says success, and no episode died in the harness or at the provider
  (settings.INCOMPLETE_FAILURES);
- the task met the collaboration rule when it ran: an SWE run's code commit descends from SWE_FIX;
- a regulator, when the cell has one, acted: it made at least one turn that was not the provider's refusal notice.
  On 2026-11-01 every turn of the Opus regulator in 7 protein runs was Anthropic's refusal notice;
- a disregulator, when the cell has one, acted the same way and posted at least once. It holds nothing, so the board is
  its only way to reach the team, and a run where it never posted gave the workers no disregulator to hear. A regulator
  that never posts may have seen nothing to report, so its silence on the board is a result (run_index regulator_posts);
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
from src.measures.run_directory import ATTEMPTS, RunDir, run_name, run_place, study_runs

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
REASONS = ("no_run_json", "superseded", "mock", "incomplete", "swe_before_fix", "swe_commit_unknown",
           "regulator_silent", "regulator_refused", "disregulator_silent", "disregulator_refused",
           "disregulator_no_post", "no_team_record", "failed_episodes")
"""Why a run is not valid, the first that applies naming it: no run.json; an earlier attempt; a mock rehearsal; an eval
that did not complete; an SWE run before SWE_FIX, or on a commit git does not know; a regulator or a disregulator that
made no turn, or only refusal notices; a disregulator that never posted; no team row in signals.jsonl; an episode that
failed otherwise."""
CHECKED_ROLES = ("regulator", "disregulator")
"""The hidden roles a cell's meaning rests on."""


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


def agent_turns(run: Path, agent: str) -> tuple[int, int]:
    """(the agent's turns, how many were a refusal notice), from its trajectory."""
    turns = refusals = 0
    for transcript in run.glob(f"trajectories/*@{agent}/transcript.json"):
        steps = json.loads(transcript.read_text())["trajectory"]["steps"]
        for step in steps:
            if step.get("source") == "agent":
                turns += 1
                refusals += str(step.get("message") or "").startswith(REFUSAL_NOTICE)
    return turns, refusals


def role_agents(data: dict, role: str) -> list[str]:
    """The agents of a hidden role, from the roles run.json's swarm block records."""
    roles = (data.get("swarm") or {}).get("roles") or {}
    return [agent for agent, held in roles.items() if held == role]


def role_check(run: Path, data: dict, role: str) -> tuple[list[dict], list[tuple[str, str]]]:
    """One record per agent of the role, and each way one of them keeps the run from being valid."""
    posts = (data.get("rules") or {}).get("posts") or {}
    model = (data.get("role_models") or {}).get(role) or data.get("model")
    records, why = [], []
    for agent in role_agents(data, role):
        turns, refusals = agent_turns(run, agent)
        acted = turns > refusals
        records.append({"agent": agent, "model": model, "turns": turns, "refusals": refusals,
                        "posts": posts.get(agent, 0), "acted": acted})
        if not turns:
            why.append((f"{role}_silent", f"{role} {agent} made no turn"))
        elif not acted:
            why.append((f"{role}_refused",
                        f"{role} {agent} never acted: {refusals} of {turns} turns were refusal notices"))
        elif role == "disregulator" and not posts.get(agent, 0):
            why.append(("disregulator_no_post", f"disregulator {agent} never posted, so no teammate heard it"))
    return records, why


def validity(run: Path) -> dict:
    """One run's record: its facts, whether it completed, whether it is valid, every reason it is not (`why`, with
    their codes in `reasons`) and the first of them as a code (`reason`, one of REASONS, or "valid"). `run` is the run's
    own name, which an earlier attempt's folder extends with .<n>."""
    name = run_name(run)
    record: dict = {"run": name, "mock": bool(MOCK.search(name))}
    data = json.loads((run / "run.json").read_text()) if (run / "run.json").is_file() else None
    why: list[tuple[str, str]] = []
    if run_place(run)[1] == ATTEMPTS:
        why.append(("superseded", "an earlier attempt, superseded by a later run of its name"))
    if data is None:
        return finish({**record, "whole": False}, [*why, ("no_run_json", "no run.json")])
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
    for role in CHECKED_ROLES:
        agents, problems = role_check(run, data, role)
        if agents:
            record[f"{role}s"] = agents
        why += problems
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
