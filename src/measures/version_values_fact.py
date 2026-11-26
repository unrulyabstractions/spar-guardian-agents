"""Which of the task's planted values each version of the team's work held: every hand-in and every check_fix call, a
fact with no label. Stored as processed/facts/version_values.json by the pipeline's `version_values` part
(src/configuration/pipeline_parts.yaml); a label change never touches it.

    fact = version_values(RunDir(Path("runs/<study>/<run>")))
    fact["values"]          # {key: value} the run planted (swe.own_values, swe_inputs.owns); {} for a run with none
    fact["hand_ins"][0]     # {"seq", "agent", "round", "commit", "held", "copy", "final"}
    fact["checks"][0]       # {"n", "agent", "round", "commit", "held", "copy", "answer"}

The hand-ins are transcript_presence.hand_ins' submits in their order (run.json unsubmits.submits, or team_submission
alone in a run without the unsubmit option); final marks the one that stood at the end. The checks are
swe.check_fix_calls in their order; a call's round is the round of its check_fix call on rules.timeline, found by the
call's time (transcript_presence.call_at), None when no call holds it.

held is the keys of the values the version's diff adds, in the values' order: a value counts when it is on an added
line (swe_inputs.added_lines). held is None when no copy of the version was stored, and copy then None; else copy is
the stored file read, from processed/swe/. The copies, the first found:
  * a check's own tree, checks/check.<n>.diff, unless it is empty and the call recorded an error (no tree was read);
  * the commit's tree (_swe_check_fix.write_trees): hand_ins/main.<commit>.diff, a commit the team handed in, or
    checks/main.<commit>.diff, a commit of main a check_fix call's pushers name;
  * a team branch's diff, diffs/team.<branch>.diff, whose head swe.team.refs names by the commit's first characters;
  * the graded diff, diffs/team.diff, for swe.team.graded.
A diff is kept up to DIFF_KEEP_BYTES, so a value past that cut of a larger diff is not seen. Pure: no model call,
nothing written.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.domains.swe._swe_check_fix import CHECKS_FOLDER, HAND_INS_FOLDER, check_file, main_file
from src.domains.swe._swe_scoring import branch_file
from src.domains.swe.swe_inputs import added_lines, owns
from src.domains.swe.swe_settings import CHECK_TOOL
from src.measures.run_directory import RunDir
from src.ui.transcript_presence import call_at, hand_ins

TEAM_REF = "grade/team/"
"""The prefix of a team branch in swe.team.refs (_swe_scoring._team_target: `<ref> <short commit>` per line)."""


def held(diff: str, values: dict[str, str]) -> list[str]:
    """The keys of the values on an added line of the diff, in the values' order."""
    lines = added_lines(diff)
    return [key for key, value in values.items() if any(value in line for line in lines)]


class Copies:
    """The stored copies of a run's versions under processed/swe/ (the module's docstring)."""

    def __init__(self, swe_dir: Path, team: dict) -> None:
        self.dir = swe_dir
        self.graded = team.get("graded")
        self.branches = [line.split() for line in str(team.get("refs") or "").splitlines() if len(line.split()) == 2]

    def read(self, rel: str) -> tuple[str, str] | None:
        """(rel, the file's text) of a file under swe/, None when it is not stored."""
        path = self.dir / rel
        return (rel, path.read_text()) if path.is_file() else None

    def of_commit(self, commit: str | None) -> tuple[str, str] | None:
        """The first stored copy of the commit's tree, None for none (or no commit)."""
        if not commit:
            return None
        for rel in (f"{HAND_INS_FOLDER}/{main_file(commit)}", f"{CHECKS_FOLDER}/{main_file(commit)}"):
            if found := self.read(rel):
                return found
        for ref, short in self.branches:
            if ref.startswith(TEAM_REF) and commit.startswith(short):
                if found := self.read(f"diffs/{branch_file(ref.removeprefix(TEAM_REF))}"):
                    return found
        return self.read("diffs/team.diff") if commit == self.graded else None

    def of_check(self, call: dict) -> tuple[str, str] | None:
        """The call's own tree, or the first stored copy of the commit it graded."""
        own = self.read(f"{CHECKS_FOLDER}/{check_file(call['n'])}")
        if own and (own[1] or not call.get("error")):
            return own
        return self.of_commit(call.get("main"))


def reading(copy: tuple[str, str] | None, values: dict[str, str]) -> dict:
    """{"held", "copy"} of a version from its copy (rel, text), both None without one."""
    return {"held": held(copy[1], values), "copy": copy[0]} if copy else {"held": None, "copy": None}


def check_round(timeline: list[dict], call: dict) -> int | None:
    """The round of the timeline entry that holds the call's check_fix call, by the call's time; None for none."""
    when = call.get("time")
    if not isinstance(when, (int, float)):
        return None
    found = call_at(timeline, call.get("agent"), CHECK_TOOL, datetime.fromtimestamp(when, timezone.utc).isoformat())
    return next((e.get("round") for e in timeline for c in e.get("calls") or [] if found and c.get("id") == found),
                None)


def version_values(run: RunDir) -> dict:
    """The fact: the run's planted values, each hand-in and each check_fix call with what its version held."""
    run_json = run.run_json
    swe = run_json.get("swe") or {}
    values = dict(owns(swe))
    copies = Copies(run.file("swe"), swe.get("team") or {})
    timeline = (run_json.get("rules") or {}).get("timeline") or []
    hand_in_rows = [{"seq": e["seq"], "agent": e["agent"], "round": e["round"], "commit": e["snapshot"],
                     **reading(copies.of_commit(e["snapshot"]), values), "final": e["final"]}
                    for e in hand_ins(run_json)["events"] if e["kind"] == "submit"]
    checks = [{"n": call.get("n"), "agent": call.get("agent"), "round": check_round(timeline, call),
               "commit": call.get("main"), **reading(copies.of_check(call), values), "answer": call.get("answer")}
              for call in swe.get("check_fix_calls") or []]
    return {"values": values, "hand_ins": hand_in_rows, "checks": checks}


def version_values_part(job) -> dict:
    """The pipeline's `version_values` part: the fact of the job's run."""
    return version_values(job.run)
