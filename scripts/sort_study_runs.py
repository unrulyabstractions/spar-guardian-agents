"""Sort a study's runs into its groups, each with its eval log and its figures, and prove every file arrived.

    uv run python -m scripts.sort_study_runs runs/study_exploration_1            # dry run: what would move
    uv run python -m scripts.sort_study_runs runs/study_exploration_1 --apply    # move, record and verify

A run at the study's top level goes into a group by its index record (src/measures/run_index.py, joined with the study
table for the study the table states), which carries run_validity's verdict as the study applies it:
- mock: a mock rehearsal;
- incomplete: its eval did not complete;
- invalid: any other reason it is not valid;
- a valid run, or one its study gives no verdict, stays at the top level.
A run already in a group stays there, and set_aside is filled by hand alone. A hand-made earlier attempt,
runs/<study>/failed_attempts/<name>.attempt<k>/, goes to attempts/<name>.<n>, n the first number free under runs/,
logs/ and out/, with the eval log its run.json names.

A run's eval log folder goes to the same place under logs/, and its figures to out/<study>/[<group>/]<run>/. Figures
from before out/ mirrored runs/ sit at out/<run>/. They move when no other study holds a run of that name, or when
every study that does keeps its own out/<study>/<run>/. Otherwise their owner is unknown, and they stay.

It refuses to touch a run whose box is still live (runs/boxes.json, by the study table's box or the runner's plans),
a run whose run.json changed in the last 10 minutes, a run whose name two folders hold, and a move whose target exists.
It refuses the whole sort while a live box has no box name. Without --apply it writes nothing. With it, it first
writes a move record to logs/moves/<UTC stamp>_sort_<study>.jsonl: every file with its source, its target, its byte
size and its sha256. It then moves each folder or file with one rename, checks that every file arrived with the same
size and sha256 and that no source file is left, appends the result to the record, and exits 1 on any mismatch.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from scripts.box.study_index import join_for
from src.measures.run_directory import ATTEMPTS, GROUPS, RUN_JSON, attempt_places, run_places
from src.measures.run_index import build
from src.utils.json_files import read_json

YOUNG_S = 600
"""A run.json changed this recently may still be arriving from a box: its run is left alone."""
HAND_MADE = "failed_attempts"
"""The folder where earlier attempts were set aside by hand before the attempts group existed."""
HAND_MADE_ATTEMPT = re.compile(r"^(?P<name>.+)\.attempt(?P<k>[0-9]+)$")


@dataclass(frozen=True)
class Move:
    """One rename: a run's folder, its eval log folder or file, or its figures folder."""

    run: str
    group: str | None
    kind: str
    source: Path
    target: Path


def group_of(rec: dict) -> str | None:
    """The group a run at the top level goes to by its index record, or None to stay."""
    if rec["mock"]:
        return "mock"
    if rec["completed"] is False:
        return "incomplete"
    if rec["valid"] is False:
        return "invalid"
    return None


def live_runs(root: Path, study: str, join) -> tuple[set[str], list[int]]:
    """(the run names a live box holds, by the study table's box or the runner's plans; the live instances with no
    box name)."""
    entries = (read_json(root / "runs" / "boxes.json") or {}).get("boxes", [])
    live = [entry for entry in entries if not entry.get("destroyed")]
    boxes = {entry["box"] for entry in live if entry.get("box")}
    names = {name for name, arm in (join.arms if join else {}).items() if arm["box"] in boxes}
    screen = root / "logs" / study / "screen"
    for path in [*screen.glob("box_*.json"), *screen.glob("attempts/box_*.json")]:
        plan = read_json(path) or {}
        if plan.get("box") in boxes:
            names |= set(plan.get("runs", {}))
    return names, [entry["id"] for entry in live if not entry.get("box")]


def figures_of(root: Path, study: str, name: str, place: Path) -> tuple[Path | None, str | None]:
    """(the run's figures folder to move to out/<place>, or None; why figures that may be the run's stay, or None).
    place is the run's folder under runs/ after the sort, such as study_exploration_1/invalid/<name>. The run's own
    out/<study>/<name>/ comes first; the flat out/<name>/ counts only when this study owns it."""
    target = root / "out" / place
    mirrored = root / "out" / study / name
    flat = root / "out" / name
    others = [path.name for path in (root / "runs").iterdir() if path.is_dir() and path.name != study
              and run_places(path, name)]
    owned = flat.is_dir() and all((root / "out" / other / name).is_dir() for other in others)
    sources = [path for path in (mirrored if mirrored != target else None, flat if owned else None)
               if path is not None and path.is_dir()]
    if len(sources) > 1:
        return None, f"both out/{study}/{name} and out/{name} hold figures"
    if flat.is_dir() and not owned and not sources and not target.is_dir():
        return None, f"out/{name} may be another study's ({', '.join(others)})"
    return (sources[0] if sources else None), None


def files_of(path: Path) -> list[Path]:
    """Every file under path, or the path itself when it is a file, in a fixed order."""
    if path.is_file():
        return [path]
    return sorted(Path(dirpath) / name for dirpath, _, names in os.walk(path) for name in names)


def plan_moves(study_dir: Path, now: float) -> tuple[list[Move], dict[str, list]]:
    """Every move of the sort, attempts first, and what the sort refuses or leaves, by reason."""
    root, study = study_dir.parent.parent, study_dir.name
    join = join_for(study_dir)
    records, _ = build(study_dir, join)
    live, nameless = live_runs(root, study, join)
    left: dict[str, list] = {"live box": [], "young run.json": [], "two folders": [], "target exists": [],
                             "figures stay": [], "attempt's eval log not found": [], "live instances with no box":
                             nameless}
    places = Counter(rec["run"] for rec in records if rec["group"] != ATTEMPTS)
    moves: list[Move] = []
    claimed: set[Path] = set()

    def refused(name: str, folder: Path) -> str | None:
        if name in live:
            return "live box"
        if (folder / RUN_JSON).is_file() and now - (folder / RUN_JSON).stat().st_mtime < YOUNG_S:
            return "young run.json"
        if places[name] > 1:
            return "two folders"
        return None

    def add(move: Move) -> bool:
        if move.target.exists() or move.target in claimed:
            left["target exists"].append(str(move.target.relative_to(root)))
            return False
        claimed.add(move.target)
        moves.append(move)
        return True

    hand_made = study_dir / HAND_MADE
    attempts = sorted((folder for folder in hand_made.iterdir() if HAND_MADE_ATTEMPT.match(folder.name)),
                      key=lambda folder: int(HAND_MADE_ATTEMPT.match(folder.name)["k"])) if hand_made.is_dir() else []
    for folder in attempts:
        name = HAND_MADE_ATTEMPT.match(folder.name)["name"]
        why = refused(name, folder)
        if why:
            left[why].append(f"{HAND_MADE}/{folder.name}")
            continue
        n = 1
        while any(path.exists() or path in claimed for path in attempt_places(study_dir, name, n)):
            n += 1
        runs, logs, out = attempt_places(study_dir, name, n)
        label = f"{HAND_MADE}/{folder.name}"
        add(Move(label, ATTEMPTS, "runs", folder, runs))
        recorded = Path((read_json(folder / RUN_JSON) or {}).get("eval_log") or "")
        log_folder = root / "logs" / study / HAND_MADE / folder.name
        log_file = root / "logs" / study / name / recorded.name
        if log_folder.is_dir():
            add(Move(label, ATTEMPTS, "logs", log_folder, logs))
        elif recorded.name and log_file.is_file():
            add(Move(label, ATTEMPTS, "logs", log_file, logs / recorded.name))
        else:
            left["attempt's eval log not found"].append(label)
        if (root / "out" / study / HAND_MADE / folder.name).is_dir():
            add(Move(label, ATTEMPTS, "out", root / "out" / study / HAND_MADE / folder.name, out))

    for rec in records:
        folder = study_dir / rec["path"]
        name, group = rec["run"], rec["group"]
        if group == ATTEMPTS:
            continue
        why = refused(name, folder)
        if why:
            left[why].append(rec["path"])
            continue
        goes = group_of(rec) if group is None else None
        place = Path(study) / (goes or group or "") / name
        if goes:
            add(Move(rec["path"], goes, "runs", folder, root / "runs" / place))
        logs = root / "logs" / study / name
        if (goes or group) and logs.is_dir() and not (root / "logs" / place).exists():
            add(Move(rec["path"], goes or group, "logs", logs, root / "logs" / place))
        figures, stays = figures_of(root, study, name, place)
        if stays:
            left["figures stay"].append(f"{rec['path']}: {stays}")
        elif figures is not None:
            add(Move(rec["path"], goes or group, "out", figures, root / "out" / place))
    for move in moves:
        for path in (move.source, move.target):
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError(f"{path} lies outside {root}: the sort never moves anything there")
    return moves, left


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest_of(moves: list[Move], root: Path) -> list[dict]:
    """One row per file a move carries: its run, group, kind, source, target, byte size and sha256. A file an earlier
    move takes, such as an attempt's eval log in its run's log folder, is not the later folder's."""
    rows, taken = [], set()
    for move in moves:
        for path in files_of(move.source):
            if path in taken:
                continue
            taken.add(path)
            target = move.target / path.relative_to(move.source) if move.source.is_dir() else move.target
            rows.append({"run": move.run, "group": move.group, "kind": move.kind,
                         "source": path.relative_to(root).as_posix(), "target": target.relative_to(root).as_posix(),
                         "size": path.stat().st_size, "sha256": sha256_of(path)})
    return rows


def apply(moves: list[Move], root: Path, record: Path) -> list[str]:
    """Record every file, move each source with one rename, then check every file: the mismatches."""
    rows = manifest_of(moves, root)
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text("".join(json.dumps(row) + "\n" for row in rows))
    for move in moves:
        move.target.parent.mkdir(parents=True, exist_ok=True)
        move.source.rename(move.target)
    mismatches = []
    for row in rows:
        source, target = root / row["source"], root / row["target"]
        if source.exists():
            mismatches.append(f"{row['source']}: still at its source")
        if not target.is_file():
            mismatches.append(f"{row['target']}: missing")
        elif target.stat().st_size != row["size"] or sha256_of(target) != row["sha256"]:
            mismatches.append(f"{row['target']}: size or sha256 differs")
    for folder in {move.source.parent for move in moves if move.source.parent.name == HAND_MADE}:
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    with record.open("a") as out:
        out.write(json.dumps({"verified": not mismatches, "files": len(rows), "bytes": sum(r["size"] for r in rows),
                              "mismatches": mismatches}) + "\n")
    return mismatches


def report(moves: list[Move], left: dict[str, list]) -> str:
    """What the sort moves, by group and kind, and what it leaves, by reason."""
    runs = Counter(move.group for move in moves if move.kind == "runs")
    kinds = Counter(move.kind for move in moves)
    files = len({path for move in moves for path in files_of(move.source)})
    lines = [f"== runs to move: {sum(runs.values())} ("
             + ", ".join(f"{group} {n}" for group, n in sorted(runs.items())) + ")",
             f"== moves: {len(moves)} ({', '.join(f'{kind} {n}' for kind, n in sorted(kinds.items()))}), {files} files"]
    stay = sum(1 for move in moves if move.kind == "out" and move.group is None)
    lines.append(f"== figures to out/<study>/ for runs that stay at the top level: {stay}")
    lines += [f"== left, {reason}: {len(items)} {items if items else ''}".rstrip() for reason, items in left.items()]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path, help="a study's runs folder, such as runs/study_exploration_1")
    parser.add_argument("--apply", action="store_true", help="move, record and verify; without it nothing is written")
    args = parser.parse_args(argv)
    study_dir = args.folder.resolve()
    if not study_dir.is_dir() or study_dir.parent.name != "runs" or study_dir.name in GROUPS:
        parser.error(f"{args.folder} is not a study's folder under a runs/ folder")
    moves, left = plan_moves(study_dir, time.time())
    print(report(moves, left))
    if not args.apply:
        print("== dry run: nothing moved; --apply moves, records and verifies")
        return 0
    if left["live instances with no box"]:
        print(f"REFUSED: live instances with no box name {left['live instances with no box']}; nothing moved")
        return 2
    root = study_dir.parent.parent
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    record = root / "logs" / "moves" / f"{stamp}_sort_{study_dir.name}.jsonl"
    mismatches = apply(moves, root, record)
    print(f"== moved {len(moves)}; record {record}")
    if mismatches:
        print("MISMATCH: " + "; ".join(mismatches[:20]))
        return 1
    print("== verified: every file arrived with its size and sha256, and no source file is left")
    return 0


if __name__ == "__main__":
    sys.exit(main())
