"""The process command: every computed part of the pipeline (src/configuration/pipeline_parts.yaml) made and stored in
each run's own folder, redone only where it is behind. `make transcripts` then serves what is stored.

    python -m src process [study or run folders ...] [--force PART] [--dry-run] [--jobs N]
    make process-transcripts [STUDY=runs/<study>] [RUN=runs/<study>/<run>] [FORCE=<part>] [DRY_RUN=1]
    process_run(Path("runs/ian_seed_prototyping_0/swe_deepseek_pointer_s1"))    # one run, as src/run.py does

With no folder, every study under runs/ is processed; a study or run under OLD/runs/ only when named. A run is a
valid run of its collection (src/measures/run_listing.py list_collection), so a study's attempts and set-aside runs
are left alone.

For one run, in registry order: a recorded part (the export, the replay, the judges) whose files changed since its
stamp, or that has files and no stamp, is stamped again, with its version kept (0 for a first stamp: made before the
registry). A computed part is behind when it has no entry, its version is not the registry's, an input it recorded
differs from that input now, or its stored file changed (src/measures/stored_versions.py behind); `--force` makes a
part and everything after it behind. A behind part is computed by its `make` ("module:function", called with the
Job) and stored by stored_versions.write_output: "redone" when its bytes changed, "restamped" when they did not. A part
that raises keeps its old file and entry, and every part that needs it is left blocked. Judges are never run.

Runs share nothing and run in a pool of worker processes, as many as the cores and the available memory allow
(workers_for). The output goes through src/utils/progress.py: what was behind, a counter, what was redone and
restamped, the failures, and a one-line summary.
"""
from __future__ import annotations

import argparse
import importlib
import os
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from src.configuration.settings import ROOT
from src.measures import pipeline_parts, stored_versions
from src.measures.run_directory import GROUPS, RunDir
from src.measures.run_listing import Listed, list_collection
from src.utils import progress

RUNS = ROOT / "runs"
RUN_PEAK_MB = 300
"""A processing worker's peak memory, rounded up: at most 255 MB over the 405 runs measured on 2026-11-22, 126 MB of
it the imports. A worker processes runs until the pool ends: Python 3.11's pool, told to replace a process after some
tasks (max_tasks_per_child), stops replacing them and hangs once every process has exited."""
SAFETY = 2
"""The room each worker gets, in RUN_PEAK_MBs."""
MEMORY_SHARE = 0.5
"""The share of the machine's available memory the workers may take together."""


@dataclass
class Job:
    """One run to process: its folder, its collection's name and its listing (the row's id, key and seed)."""

    run: RunDir
    col: str
    listed: Listed


@dataclass
class Report:
    """What processing one run did: {part: why} behind, {part: "redone" | "restamped"} done, {part: reason} failed,
    {part: the part it waits on} blocked, and the recorded parts stamped again."""

    key: str
    behind: dict[str, str] = field(default_factory=dict)
    done: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    blocked: dict[str, str] = field(default_factory=dict)
    stamped: list[str] = field(default_factory=list)


# ------------------------------------------------------------------------------------------------------- the runs
def collection_of(path: Path) -> tuple[Path, Listed] | None:
    """The collection a run folder belongs to and its listing there: the nearest folder above it, a study's group
    folder skipped, whose listing names it. None for a folder no collection lists."""
    path = Path(path).resolve()
    for folder in path.parents:
        if folder.name in ("runs", "") or folder == ROOT:
            return None
        if folder.name in GROUPS:
            continue
        found = next((item for item in list_collection(folder) if item.path.resolve() == path), None)
        if found is not None:
            return folder, found
    return None


def jobs_of(folder: Path) -> list[Job]:
    """The valid runs of a collection folder, or the one run a run folder is, whatever its status (a folder no
    collection lists is its parent's, under its own name)."""
    folder = Path(folder).resolve()
    if RunDir(folder).is_run():
        found = collection_of(folder)
        if found is None:
            return [Job(RunDir(folder), folder.parent.name, Listed(folder.name, folder, "unlisted"))]
        col, listed = found
        return [Job(RunDir(listed.path), col.name, listed)]
    return [Job(RunDir(item.path), folder.name, item) for item in list_collection(folder) if item.status == "valid"]


def default_targets(runs: Path = RUNS) -> list[Path]:
    """Every study folder under runs/."""
    return sorted(p for p in runs.iterdir() if p.is_dir() and not p.name.startswith(".")) if runs.is_dir() else []


# -------------------------------------------------------------------------------------------------------- one run
def make_of(part: pipeline_parts.Part):
    """A computed part's make, imported."""
    module, _, function = part.make.partition(":")
    return getattr(importlib.import_module(module), function)


def restamp_recorded(run: RunDir, registry: pipeline_parts.Registry) -> list[str]:
    """Stamp again every recorded part whose files changed since its stamp, or that has files and no stamp (version 0:
    made before the registry). Returns the parts stamped."""
    stamped = []
    entries = stored_versions.read(run)
    for name in pipeline_parts.order(registry):
        part = registry.parts[name]
        if not part.recorded:
            continue
        old = entries.get(name)
        now = stored_versions.reference(run, name, registry, deep=True, entries=entries)
        if now is None or (old is not None and [old["version"], old.get("sha256")] == now):
            continue
        stored_versions.stamp(run, name, registry=registry, version=0 if old is None else old["version"],
                              made=None if old is None else old.get("made"))
        stamped.append(name)
        entries = stored_versions.read(run)
    return stamped


def process_job(job: Job, registry: pipeline_parts.Registry | None = None, force: tuple[str, ...] = (),
                dry_run: bool = False) -> Report:
    """Process one run: restamp its recorded parts, then redo in registry order every computed part that is behind."""
    registry = registry or pipeline_parts.registry()
    report = Report(f"{job.col}/{job.listed.key}")
    forced = {name for f in force for name in {f, *pipeline_parts.downstream(registry, f)}}
    if dry_run:
        report.behind = {**stored_versions.behind(job.run, registry, deep=True),
                         **{name: "forced" for name in forced if not registry.parts[name].recorded}}
        return report
    report.stamped = restamp_recorded(job.run, registry)
    first = stored_versions.behind(job.run, registry, deep=True)
    for name in pipeline_parts.order(registry):
        part = registry.parts[name]
        if part.recorded:
            continue
        waits = next((need for need in part.needs if need in report.failed or need in report.blocked), None)
        if waits is not None:
            report.blocked[name] = waits
            continue
        why = "forced" if name in forced else first.get(name) or stored_versions.behind(job.run, registry).get(name)
        if not why:
            continue
        report.behind[name] = why
        try:
            report.done[name] = stored_versions.write_output(job.run, name, make_of(part)(job), registry=registry)
        except Exception as error:  # noqa: BLE001 - a part's failure is reported, its old output kept
            report.failed[name] = f"{type(error).__name__}: {error}"
    return report


def process_run(run_dir: Path, force: tuple[str, ...] = ()) -> Report:
    """Process one run folder (jobs_of), as src/run.py does right after the export."""
    [job] = jobs_of(run_dir)
    return process_job(job, force=force)


# ------------------------------------------------------------------------------------------------------- many runs
def available_memory() -> int:
    """The bytes the machine can give without swapping: psutil's available memory, on macOS its free plus its
    inactive pages."""
    return psutil.virtual_memory().available


def workers_for(available: int, cores: int | None) -> int:
    """How many processes work on runs: as many as MEMORY_SHARE of the available memory holds at SAFETY times
    RUN_PEAK_MB each, never more than the cores but one, and at least one."""
    by_memory = int(available * MEMORY_SHARE) // ((RUN_PEAK_MB * SAFETY) << 20)
    return max(1, min(by_memory, (cores or 1) - 1))


def counts(reports: list[Report], key: str) -> str:
    """"cheating 79, row 79" over the reports' `key` maps, in registry order; "none" when empty."""
    tally = Counter(name for report in reports for name in getattr(report, key))
    order = [name for name in pipeline_parts.order(pipeline_parts.registry()) if name in tally]
    return ", ".join(f"{name} {tally[name]}" for name in order) or "none"


def process(targets: list[Path], force: tuple[str, ...] = (), dry_run: bool = False, jobs: int | None = None) -> int:
    """Process every run of the targets; 1 when a part failed or a target lists no run."""
    progress.step("Process runs")
    registry = pipeline_parts.registry()
    for name in force:
        if registry.part(name).recorded:
            raise ValueError(f"{name} is recorded: processing never makes it")
    todo = [job for target in targets for job in jobs_of(target)]
    progress.detail("runs", f"{len(todo)} in {progress.plural(len(targets), 'folder')} "
                    f"({', '.join(progress.short(t) for t in targets)})")
    if not todo:
        progress.failed("no run to process")
        return 1
    available, cores = available_memory(), os.cpu_count()
    workers = min(jobs or workers_for(available, cores), len(todo))
    if not dry_run:
        progress.detail("workers", f"{workers}, from {progress.plural(cores or 1, 'core')} and "
                        f"{available / 2**30:.1f} GB available at {RUN_PEAK_MB * SAFETY} MB each")
    reports: list[Report] = []
    if workers == 1 or dry_run:
        with progress.Counter(len(todo), "checked" if dry_run else "processed", "runs") as count:
            for job in todo:
                reports.append(process_job(job, registry, force, dry_run))
                count.tick()
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool, \
                progress.Counter(len(todo), "processed", "runs") as count:
            futures = [pool.submit(process_job, job, None, force, dry_run) for job in todo]
            for future in as_completed(futures):
                reports.append(future.result())
                count.tick()
    behind = sum(bool(r.behind) for r in reports)
    progress.detail("behind", counts(reports, "behind"))
    if dry_run:
        progress.done(f"{behind} of {progress.plural(len(reports), 'run')} behind; nothing written")
        return 0
    done = Counter(state for r in reports for state in r.done.values())
    progress.detail("redone", counts([Report(r.key, done={k: v for k, v in r.done.items() if v == "redone"})
                                      for r in reports], "done"))
    progress.detail("restamped", done["restamped"])
    progress.detail("up to date", progress.plural(len(reports) - behind, "run"))
    stamped = counts(reports, "stamped")
    if stamped != "none":
        progress.detail("stamped", stamped)
    failures = [(r.key, name, why) for r in reports for name, why in r.failed.items()]
    progress.detail("failed", len(failures))
    for key, name, why in sorted(failures)[:10]:
        progress.detail("", f"{key}: {name}: {why}", err=True)
    blocked = counts(reports, "blocked")
    if blocked != "none":
        progress.detail("blocked", blocked)
    summary = (f"processed {progress.plural(len(reports), 'run')}, {behind} behind, "
               f"{done['redone']} parts redone, {done['restamped']} restamped")
    if failures:
        progress.failed(f"{summary}, {len(failures)} failed")
        return 1
    progress.done(summary, out=", ".join(progress.short(t) for t in targets))
    return 0


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser("process", help="compute and store every part of the pipeline that is behind")
    parser.add_argument("targets", nargs="*", type=Path, help="study or run folders (default: every study in runs/)")
    parser.add_argument("--force", action="append", default=[], metavar="PART",
                        help="redo this part and every part after it, behind or not")
    parser.add_argument("--dry-run", action="store_true", help="list what is behind and write nothing")
    parser.add_argument("--jobs", type=int, help="worker processes (default: from the cores and free memory)")
    parser.set_defaults(func=cmd_process)


def cmd_process(args: argparse.Namespace) -> int:
    targets = [Path(t) for t in args.targets] or default_targets()
    return process(targets, tuple(args.force), args.dry_run, args.jobs)
