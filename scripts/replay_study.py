"""Replay a study's runs from their recorded tool calls and put each faithful replay's steps beside its run.

    make replay STUDY=<study> [RUN="<run> ..."] [JOBS=2]

`python -m src replay` plays each run back in Docker into tmp/replay/<study>/<run>/, with no model call. A replay
whose end state equals the run's own is faithful: its steps.jsonl and replay.json are then copied into
runs/<study>/<run>/, where the viewer and the ladders read them. An unfaithful replay leaves the run as it was, read
at its end. Nothing else in the run folder changes. Runs that already hold a replay.json are skipped.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.measures.run_directory import RunDir  # noqa: E402
from src.measures.run_listing import list_collection  # noqa: E402
from src.replay.replay_report import is_faithful, read_report  # noqa: E402
from src.replay.replay_run import REPLAYERS  # noqa: E402

KEPT = ("steps.jsonl", "replay.json")
"""The files of a faithful replay that go into the run's folder."""


def place(out: Path, runs: Path) -> dict[str, list[str]]:
    """Copy KEPT of each faithful replay under `out` into the run of the same name under `runs`; a run folder that
    is missing or already holds a replay.json is left alone. {"placed", "unfaithful", "skipped"}: run names."""
    done: dict[str, list[str]] = {"placed": [], "unfaithful": [], "skipped": []}
    for replay in sorted(p for p in out.iterdir() if p.is_dir()):
        run = runs / replay.name
        if not is_faithful(read_report(replay)):
            done["unfaithful"].append(replay.name)
        elif not run.is_dir() or (run / "replay.json").exists() or not all((replay / f).is_file() for f in KEPT):
            done["skipped"].append(replay.name)
        else:
            for name in KEPT:
                shutil.copyfile(replay / name, run / name)
            done["placed"].append(replay.name)
    return done


def to_replay(runs: Path, names: list[str]) -> list[Path]:
    """The runs to play back: those named, else the study's valid runs of a task the replay plays back; either way
    only runs whose folder holds no replay.json yet."""
    valid = [r.path for r in list_collection(runs) if r.status == "valid"] if not names else []
    found = [runs / name for name in names] or [p for p in valid if RunDir(p).run_json.get("task") in REPLAYERS]
    return [path for path in found if not (path / "replay.json").exists()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("study", help="a folder of runs/, e.g. study_exploration_1")
    parser.add_argument("--run", action="append", default=[], help="one run of the study (repeat for several)")
    parser.add_argument("--jobs", type=int, default=2, help="runs replayed at once (default 2)")
    args = parser.parse_args(argv)
    runs = ROOT / "runs" / args.study
    if not runs.is_dir():
        parser.error(f"no folder {runs}")
    out = ROOT / "tmp" / "replay" / args.study
    out.mkdir(parents=True, exist_ok=True)
    todo = [str(path) for path in to_replay(runs, args.run)]
    if todo:
        subprocess.run([sys.executable, "-m", "src", "replay", *todo, "--out", str(out), "--only-missing",
                        "--jobs", str(args.jobs)], cwd=ROOT, check=False)
    done = place(out, runs)
    for key in ("placed", "unfaithful", "skipped"):
        print(f"{key}: {len(done[key])}" + (f"  {' '.join(done[key])}" if key == "unfaithful" and done[key] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
