"""Reprocess a copy of each study's stored runs with the checked-out code, and list every label that changes.

    uv run python -m scripts.study.reprocess_compare [study ...]                 # every study under runs/ by default
    uv run python -m scripts.study.reprocess_compare <study> --expect <file>     # the label changes a commit means
    uv run python -m scripts.study.reprocess_compare <study> --runs ../../runs    # from a worktree: main's runs
    make reprocess-check [STUDY="<study> ..."]

For each study it copies runs/<study> and logs/<study> into a scratch folder outside the repository (cp -Rc, which
clones the files and takes no room). It leaves out each run that has no analysis/labels/cheating.json and each run a
live cell script is writing (run_study.live_names). It processes the copy as `python -m src process` would, every
computed part forced (FORCE), and compares every file of the copy with the stored one by sha256. It always deletes
the scratch folder, and it never writes under runs/ or logs/.

It prints, study by study, how many files differ at each path inside a run, then every label that changes, one line
each: the team's labels first (the Cheating rung, the pass, the alarm, the Coordination level, the Collusion rung),
then each agent's rungs with its role, the disregulators' role labels, the regulators' alert counts and the refused
writes and submits. A line reads `<study>/<run> <label>: <stored> -> <now>`.

The exit code: 0 when only versions.json files differ (each records when its parts were made), 1 when a label or any
other file differs, 2 when processing failed. With --expect <file>, a list of label lines in the printed form (blank
lines and lines that start with # are skipped), 0 when the label changes are exactly the file's, else 1.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from scripts.study.run_study import live_names
from src.configuration.settings import ROOT
from src.measures.run_listing import list_collection

RUNS = ROOT / "runs"
"""Where the stored studies are by default. Their eval logs sit in the logs/ beside it."""
FORCE = ("signals", "cheating", "collusion", "topology", "disregulator_roles", "row")
"""The parts processing is forced to redo, with every part after each of them (process_runs.process)."""
STAMPS = "versions.json"
"""The one file that differs on every reprocess: it records when each part was made."""
CHEATING = Path("analysis/labels/cheating.json")


@dataclass(frozen=True)
class Change:
    """One label of one run that the reprocess changes."""

    run: str            # <study>/<run>, the run's folder under runs/
    label: str
    old: object
    new: object

    def __str__(self) -> str:
        return f"{self.run} {self.label}: {shown(self.old)} -> {shown(self.new)}"


def shown(value) -> str:
    """A label's value on one line: a string as it is, anything else as JSON."""
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True)


# ------------------------------------------------------------------------------------------------ the copy
def skipped_runs(study_dir: Path, live: set[str]) -> list[Path]:
    """Each run of the study, in any group, that has no cheating.json or that a live cell script is writing."""
    return [item.path for item in list_collection(study_dir)
            if not (item.path / CHEATING).is_file() or item.path.name in live]


def copy_study(study: str, scratch: Path, live: set[str], runs: Path = RUNS) -> list[str]:
    """Clone runs/<study> and logs/<study> under scratch/runs and scratch/logs, without the skipped runs. Returns the
    skipped runs' folders, relative to the study."""
    source = runs / study
    for top, folder in (("runs", source), ("logs", runs.parent / "logs" / study)):
        if folder.is_dir():
            (scratch / top).mkdir(parents=True, exist_ok=True)
            subprocess.run(["cp", "-Rc", str(folder), str(scratch / top / study)], check=True)
    skipped = [path.relative_to(source) for path in skipped_runs(source, live)]
    for relative in skipped:
        for top in ("runs", "logs"):
            shutil.rmtree(scratch / top / study / relative, ignore_errors=True)
    return [p.as_posix() for p in skipped]


def process_copy(study: str, copy: Path) -> subprocess.CompletedProcess:
    """`python -m src process <copy> --force ...` with the checked-out code, STUDY set to the study."""
    command = [sys.executable, "-m", "src", "process", str(copy), *(f"--force={part}" for part in FORCE)]
    return subprocess.run(command, cwd=ROOT, env={**os.environ, "STUDY": study}, capture_output=True, text=True)


# ------------------------------------------------------------------------------------------------ the comparison
def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_differences(stored: Path, copy: Path) -> Counter:
    """How many files differ at each path inside a run folder (the run's own name left out), between the stored
    study folder and its reprocessed copy. A path only in the copy counts as `new <path>`, one only in the stored
    folder's kept runs as `gone <path>`."""
    found = Counter()
    kept = {p.relative_to(copy) for p in copy.rglob("*") if p.is_file()}
    runs = {item.path.relative_to(copy) for item in list_collection(copy)}
    for relative in sorted(kept):
        old = stored / relative
        if not old.is_file():
            found[f"new {inside_run(relative, runs)}"] += 1
        elif sha256(old) != sha256(copy / relative):
            found[inside_run(relative, runs)] += 1
    for run in runs:
        for old in (stored / run).rglob("*"):
            if old.is_file() and old.relative_to(stored) not in kept:
                found[f"gone {inside_run(old.relative_to(stored), runs)}"] += 1
    return found


def inside_run(relative: Path, runs: set[Path]) -> str:
    """A file's path inside its run folder, or its path in the study for a file of no run."""
    run = next((r for r in runs if relative.is_relative_to(r)), None)
    return (relative.relative_to(run) if run else relative).as_posix()


def read(path: Path) -> dict:
    return json.loads(path.read_text()) if path.is_file() else {}


def team_signals(run: Path) -> dict:
    """run.json's team record (signals.team)."""
    return (read(run / "processed" / "run.json").get("signals") or {}).get("team") or {}


def labels(run: Path) -> dict[str, object]:
    """Every label of one run folder, by name, in the order they print: the team's, then each agent's, the roles', the
    alerts' and the refused acts'."""
    folder = run / "analysis" / "labels"
    cheating, collusion = read(run / CHEATING), read(folder / "collusion.json")
    coordination, roles = read(folder / "coordination.json"), read(folder / "disregulator_roles.json")
    team = team_signals(run)
    out: dict[str, object] = {f"cheating.{key}": cheating.get(key)
                              for key in ("team", "passed", "alarm", "team_cheated", "exploit_used", "passed_how")}
    out["coordination.team"] = coordination.get("team")
    out["collusion.team"] = collusion.get("team")
    for ladder, record, key in (("cheating", cheating, "rung"), ("coordination", coordination, "level"),
                                ("collusion", collusion, "rung")):
        for agent, row in sorted((record.get("agents") or {}).items()):
            role = row.get("role") or (coordination.get("roles") or {}).get(agent) or "worker"
            out[f"{ladder} {agent} ({role})"] = row.get(key)
    for agent, row in sorted((roles.get("agents") or {}).items()):
        out[f"role {agent}"] = [row.get("label") or row.get("outcome"), row.get("mode")]
    for key in ("alerts", "true_alarms", "false_alarms", "unknown_alarms"):
        out[f"signals.{key}"] = team.get(key)
    outcomes = Counter(str(a.get("outcome") if isinstance(a, dict) else a) for a in team.get("alert_outcomes") or [])
    out["signals.alert_outcomes"] = dict(sorted(outcomes.items()))
    for key in ("refused_writes", "refused_submits"):     # a list of the refused acts, or their count
        value = team.get(key) or []
        out[f"signals.{key}"] = value if isinstance(value, int) else len(value)
    return out


def label_changes(study: str, stored: Path, copy: Path) -> list[Change]:
    """Every label that differs between each reprocessed run and its stored folder, run by run: the team's labels of
    every run first, then the rest."""
    team, rest = [], []
    for item in sorted(list_collection(copy), key=lambda item: item.path):
        run = item.path.relative_to(copy)
        if not (copy / run / CHEATING).is_file():
            continue
        old, new = labels(stored / run), labels(copy / run)
        for name in dict.fromkeys([*new, *old]):
            if old.get(name) != new.get(name):
                change = Change(f"{study}/{run.as_posix()}", name, old.get(name), new.get(name))
                (team if name.startswith(("cheating.", "coordination.team", "collusion.team")) else rest).append(change)
    return team + rest


# ------------------------------------------------------------------------------------------------ one study
@dataclass
class Result:
    study: str
    files: Counter
    changes: list[Change]
    skipped: list[str]
    seconds: float
    failed: str | None = None
    processed: str = ""        # the last line processing printed: how many runs and parts it redid

    @property
    def only_stamps(self) -> bool:
        return not self.changes and all(path.split()[-1].endswith(STAMPS) for path in self.files)


def compare_study(study: str, live: set[str], runs: Path = RUNS,
                  process: Callable[[str, Path], subprocess.CompletedProcess] = process_copy) -> Result:
    """Copy, reprocess and compare one study, in a scratch folder deleted at the end whatever happens."""
    start = time.monotonic()
    scratch = Path(tempfile.mkdtemp(prefix=f"reprocess_compare_{study}_"))
    if scratch.resolve().is_relative_to(ROOT.resolve()):
        raise RuntimeError(f"the scratch folder {scratch} lies inside the repository")
    try:
        skipped = copy_study(study, scratch, live, runs)
        copy = scratch / "runs" / study
        done = process(study, copy)
        if done.returncode != 0:
            tail = "\n".join((done.stdout + done.stderr).strip().splitlines()[-15:])
            return Result(study, Counter(), [], skipped, time.monotonic() - start, failed=tail)
        summary = next((line.strip() for line in reversed(done.stdout.splitlines()) if line.strip()), "")
        return Result(study, file_differences(runs / study, copy), label_changes(study, runs / study, copy),
                      skipped, time.monotonic() - start, processed=summary)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def report(result: Result) -> list[str]:
    """The lines printed for one study."""
    lines = [f"== {result.study} ({result.seconds:.0f} s)"]
    if result.skipped:
        lines.append(f"  left out: {len(result.skipped)} runs ({', '.join(result.skipped)})")
    if result.failed:
        return [*lines, "  processing failed:", *(f"    {line}" for line in result.failed.splitlines())]
    if result.processed:
        lines.append(f"  processing: {result.processed}")
    files = ", ".join(f"{path} {n}" for path, n in sorted(result.files.items())) or "none"
    lines.append(f"  files that differ, by path inside a run: {files}")
    lines += [f"  {change}" for change in result.changes]
    verdict = ("only versions.json differs" if result.only_stamps
               else f"{len(result.changes)} label changes" if result.changes
               else "no label changes, but files other than versions.json differ")
    return [*lines, f"  {result.study}: {verdict}"]


def expected_lines(path: Path) -> set[str]:
    """The label lines of an --expect file: every line but blanks and comments, stripped."""
    return {line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.study.reprocess_compare", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("studies", nargs="*", help="study names under runs/ (default: every study there)")
    parser.add_argument("--expect", type=Path, help="the label lines this change means to make, one per line")
    parser.add_argument("--runs", type=Path, default=RUNS,
                        help="the folder of the stored studies, logs/ beside it (default: this checkout's runs/; "
                             "from a worktree, the main checkout's)")
    args = parser.parse_args(argv)
    runs = args.runs.resolve()
    studies = args.studies or sorted(p.name for p in runs.iterdir() if p.is_dir() and list_collection(p))
    missing = [s for s in studies if not (runs / s).is_dir()]
    if missing:
        parser.error(f"no such study under runs/: {', '.join(missing)}")
    live = live_names()
    results = []
    for study in studies:
        results.append(compare_study(study, live, runs))
        print("\n".join(report(results[-1])), flush=True)
    if any(r.failed for r in results):
        return 2
    changes = {str(change) for r in results for change in r.changes}
    if args.expect is not None:
        wanted = expected_lines(args.expect)
        for line in sorted(wanted - changes):
            print(f"expected, not found: {line}")
        for line in sorted(changes - wanted):
            print(f"not expected: {line}")
        print("the label changes are exactly the expected ones" if changes == wanted
              else "the label changes differ from the expected ones")
        return 0 if changes == wanted else 1
    return 0 if all(r.only_stamps for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
