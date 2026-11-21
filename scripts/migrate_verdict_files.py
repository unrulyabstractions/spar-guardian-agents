"""Move a study's stored verdicts into the one layout the readers know, judges/<pass>.<judge>.jsonl keyed by episode.

    uv run python -m scripts.migrate_verdict_files runs/study_exploration_0            # the plan, nothing written
    uv run python -m scripts.migrate_verdict_files runs/study_exploration_0 --write    # the moves

Two older shapes exist, and neither can be made again from an eval log without a paid judge:
- judge.jsonl beside run.json (study_exploration_0): the eval's or the re-judge's CheatBench verdicts. Its rows go to
  judges/cheatbench.<judge>.jsonl, by the judge each row names, with unit (the episode), the agent's bare ID and role
  from episodes.jsonl, the keys verdict_files.ROW_KEYS asks for (None where the row has none) and transcript_path from
  the run's folder. A row of an episode the judge's file already holds is left out: that file's row is newer.
- judges/labels.<judge>.jsonl keyed by agent (study_exploration_1): each row's unit becomes the agent's episode.

Nothing is deleted. Before a file changes or moves, its bytes are copied to
.backups/verdicts-before-migration/<study>/[<group>/]<run>/ in this checkout (git ignores .backups/), and judge.jsonl
leaves the run's folder only after its rows are written and read back. A run is migrated once: a second pass finds
nothing to do. Delete this script once every study is migrated.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from src.configuration import settings
from src.export.run_export import VERDICT_IDENTITY
from src.judging import verdict_files
from src.measures.run_directory import RunDir, study_runs
from src.utils.json_files import read_jsonl

OLD_JUDGE = "judge.jsonl"
BACKUP = settings.ROOT / ".backups" / "verdicts-before-migration"


def backup_dir(run: Path, study: Path) -> Path:
    """Where a run's verdict files are copied before they change: BACKUP/<study>/<the run's path in the study>."""
    return BACKUP / study.name / run.relative_to(study)


def bare_agent(record: dict) -> str | None:
    """An episode record's agent ID in either export: the bare "agent", or the older "orbit_agent"."""
    return record.get("orbit_agent") or (record.get("agent") or "").split("/")[-1] or None


def cheatbench_rows(run: RunDir) -> dict[str, list[dict]]:
    """judge.jsonl's rows in the judges/ shape, by judge, without the episodes that judge's file already holds."""
    episodes = {ep["episode"]: ep for ep in run.episodes()}
    held = verdict_files.cheatbench(run.path)
    out: dict[str, list[dict]] = {}
    for old in read_jsonl(run.path / OLD_JUDGE):
        judge, episode = old.get("judge"), old["episode"]
        if not judge:
            raise ValueError(f"{run.path / OLD_JUDGE}: the row of {episode} names no judge")
        if episode in held.get(judge, {}):
            continue
        record = {**episodes.get(episode, {}), "agent": bare_agent(episodes.get(episode, {}))}
        row = {"unit": episode, **{key: old.get(key, record.get(key)) for key in VERDICT_IDENTITY}}
        row.update({key: value for key, value in old.items() if key != "transcript_path"})
        row.update({key: row.get(key) for key in verdict_files.ROW_KEYS})
        row["transcript_path"] = run.transcript_path(episode).relative_to(run.path).as_posix()
        out.setdefault(judge, []).append(row)
    return out


def labels_rows(run: RunDir) -> dict[str, list[dict]]:
    """Each labels file whose rows are keyed by agent, with every unit made the agent's episode, by judge."""
    episode_of = {bare_agent(ep): ep["episode"] for ep in run.episodes()}
    out = {}
    for judge, rows in (verdict_files.read(run.path).get(verdict_files.LABELS_PASS) or {}).items():
        if any(row["unit"] != row.get("episode") for row in rows):
            out[judge] = [{**row, "unit": row.get("episode") or episode_of[row["agent"]]} for row in rows]
    return out


def plan(study: Path) -> list[tuple[RunDir, dict, dict]]:
    """(run, cheatbench rows by judge, labels rows by judge) for every run with something to move."""
    found = []
    for path in study_runs(study):
        run = RunDir(path)
        cheat = cheatbench_rows(run) if (path / OLD_JUDGE).is_file() else {}
        labels = labels_rows(run)
        if cheat or labels or (path / OLD_JUDGE).is_file():
            found.append((run, cheat, labels))
    return found


def migrate(run: RunDir, study: Path, cheat: dict, labels: dict) -> None:
    """Copy the run's verdict files aside, write the new rows, read them back, then move judge.jsonl aside."""
    keep = backup_dir(run.path, study)
    keep.mkdir(parents=True, exist_ok=True)
    folder = run.path / verdict_files.FOLDER
    for path in [run.path / OLD_JUDGE, *(sorted(folder.glob("*.jsonl")) if folder.is_dir() else [])]:
        if path.is_file():
            target = keep / path.relative_to(run.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    for judge, rows in cheat.items():
        verdict_files.write(run.path, verdict_files.CHEATBENCH_PASS, judge, rows)
    for judge, rows in labels.items():
        verdict_files.write(run.path, verdict_files.LABELS_PASS, judge, rows,
                            keep={row["unit"] for row in rows})
    stored = verdict_files.cheatbench(run.path)
    missing = [row["episode"] for row in read_jsonl(run.path / OLD_JUDGE)
               if row["episode"] not in stored.get(row.get("judge"), {})]
    if missing:
        raise RuntimeError(f"{run.path}: judges/ lacks {missing} after the write; judge.jsonl stays")
    if (run.path / OLD_JUDGE).is_file():
        (run.path / OLD_JUDGE).rename(keep / f"{OLD_JUDGE}.moved")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("study", type=Path, help="a study's folder, e.g. runs/study_exploration_0")
    parser.add_argument("--write", action="store_true", help="make the moves; without it, print the plan only")
    args = parser.parse_args(argv)
    study = args.study.resolve()
    found = plan(study)
    for run, cheat, labels in found:
        parts = [f"cheatbench.{judge}: {len(rows)} rows from judge.jsonl" for judge, rows in cheat.items()]
        parts += [f"labels.{judge}: {len(rows)} units made episodes" for judge, rows in labels.items()]
        print(f"{run.path.relative_to(study)}  {'; '.join(parts) or 'judge.jsonl already in judges/'}")
    print(f"== {len(found)} runs to migrate in {study}" + ("" if args.write else " (plan only; --write moves)"))
    if not args.write:
        return 0
    for run, cheat, labels in found:
        migrate(run, study, cheat, labels)
    left = plan(study)
    print(f"== migrated {len(found)} runs; {len(left)} left; the old files are in {BACKUP / study.name}")
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
