"""Archive a study: move runs/<study> and logs/<study> into OLD/, and prove every file arrived.

    uv run python -m scripts.study.archive_study ian_seed_prototyping_0            # dry run: what would move
    uv run python -m scripts.study.archive_study ian_seed_prototyping_0 --apply    # move, record and verify

A study goes to OLD/ only on the user's word (CLAUDE.md, section 5). Its runs go to OLD/runs/<study> and its eval logs
and console logs to OLD/logs/<study>, where a run still finds its log (RunDir.eval_log reads the logs/ beside its
runs/). The study's plan is closed by hand afterwards (`closed: true` in studies/plans/<study>.yaml).

It refuses a study with no runs folder, a target that exists, and a study one of whose run.json changed in the last
10 minutes (a run may still be writing). Without --apply it writes nothing. With it, it records every file under
logs/moves/<UTC stamp>_archive_<study>.jsonl (source, target, byte size, sha256), moves each folder with one rename,
checks that every file arrived with the same size and sha256 and that no source file is left, and exits 1 on any
mismatch (sort_study_runs.apply).
"""
from __future__ import annotations

import argparse
import datetime as dt
import time
from pathlib import Path

from scripts.study.sort_study_runs import YOUNG_S, Move, apply, files_of
from src.configuration.settings import ROOT

ARCHIVE = "OLD"
KINDS = ("runs", "logs")


def moves_of(study: str, root: Path, now: float) -> list[Move]:
    """The study's two renames, runs then logs (a study without logs has one); ValueError for what it refuses."""
    runs = root / "runs" / study
    if not runs.is_dir():
        raise ValueError(f"{study}: no folder runs/{study}")
    young = [p for p in runs.rglob("run.json") if now - p.stat().st_mtime < YOUNG_S]
    if young:
        raise ValueError(f"{study}: {young[0].relative_to(root)} changed in the last {YOUNG_S // 60} minutes")
    moves = [Move(study, None, kind, root / kind / study, root / ARCHIVE / kind / study)
             for kind in KINDS if (root / kind / study).is_dir()]
    taken = [move.target for move in moves if move.target.exists()]
    if taken:
        raise ValueError(f"{study}: {taken[0].relative_to(root)} exists")
    return moves


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("study")
    parser.add_argument("--apply", action="store_true", help="move, record and verify (default: list what would move)")
    args = parser.parse_args(argv)
    try:
        moves = moves_of(args.study, root, time.time())
    except ValueError as refused:
        print(f"refused: {refused}")
        return 2
    for move in moves:
        files = files_of(move.source)
        print(f"{move.source.relative_to(root)} -> {move.target.relative_to(root)}: {len(files)} files, "
              f"{sum(p.stat().st_size for p in files) / 1e9:.2f} GB")
    if not args.apply:
        print("dry run: nothing moved")
        return 0
    stamp = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H%M%SZ")
    record = root / "logs" / "moves" / f"{stamp}_archive_{args.study}.jsonl"
    mismatches = apply(moves, root, record)
    print(f"record: {record.relative_to(root)}")
    for line in mismatches[:20]:
        print(f"MISMATCH {line}")
    print("verified: every file arrived with the same size and sha256" if not mismatches
          else f"{len(mismatches)} mismatches")
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
