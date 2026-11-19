"""The judges' verdicts stored beside a run: the one reader and the one writer of runs/<study>/<run>/judges/.

    read(run_dir)                                   # {"cheatbench": {"gpt-6-luna": [rows]}, "collusion": {...}}
    rows(run_dir, "collusion", "gpt-6-luna")        # one file's rows
    write(run_dir, "collusion", "gpt-6-luna", new)  # merge rows into that file, by unit, atomically
    cheatbench(run_dir)                             # {judge: {episode: row}}, the eval's verdicts and the passes'
    episode_verdicts(run_dir)                       # {episode: row} of the judge the measures take
    applied(read(run_dir)["collusion"])             # the judge whose verdicts the ladders take: the first by name

A pass of `python -m src judge-pass` (src/judging/judge_pass.py) writes judges/<pass>.<judge>.jsonl, one row per unit
(an episode for the pass "cheatbench", a pair for "collusion"), keyed by the row's "unit". A unit judged again
replaces its row; every other row stays. A write goes to a temporary file in judges/ that then replaces the file in
one rename, so a reader never sees half a file, and nothing outside judges/ is touched: the run's own record (run.json,
episodes.jsonl, judge.jsonl, the transcripts) stays as the run made it.

Every row carries what makes it reproducible and priced: the unit, the judge entry and its generation config, the
hashes of the prompt, the schema and the exact input, parse_ok, the call's usage, its cost in dollars, the attempts and
the error (ROW_KEYS). write refuses a row without them, and a row of another pass or judge.

CheatBench's judge also runs inside the eval as its scorer, and the export writes those verdicts to the run's
judge.jsonl (src/measures/run_directory.py RunDir.verdicts), one row per episode. cheatbench reads both: the eval's
rows by their judge, then each pass's file, whose row replaces the eval's for the same judge and episode. read gives
the pass "cheatbench" that way, and episode_verdicts the one judge's rows the measures take: the first by name where
several read a run (applied). Beside this module only the export and the re-export read judge.jsonl (RunDir.verdicts).
"""
from __future__ import annotations

import json
from pathlib import Path

from src.measures.run_directory import RunDir
from src.sandbox.staging import write_atomic
from src.utils.json_files import read_jsonl

FOLDER = "judges"
PASSES = ("cheatbench", "collusion")
ROW_KEYS = ("unit", "judge", "judge_generation_config", "prompt_sha256", "schema_sha256", "input_sha256", "parse_ok",
            "judge_usage", "judge_cost_usd", "judge_attempts", "judge_error")


def path(run_dir: Path, pass_name: str, judge: str) -> Path:
    """runs/<study>/<run>/judges/<pass>.<judge>.jsonl."""
    if pass_name not in PASSES:
        raise ValueError(f"unknown judge pass {pass_name!r}; choose one of {', '.join(PASSES)}")
    if not judge or "/" in judge:
        raise ValueError(f"not a judge entry name: {judge!r}")
    return Path(run_dir) / FOLDER / f"{pass_name}.{judge}.jsonl"


def rows(run_dir: Path, pass_name: str, judge: str) -> list[dict]:
    """The rows of one pass by one judge; [] when it never ran."""
    return read_jsonl(path(run_dir, pass_name, judge))


def pass_files(run_dir: Path) -> dict[str, dict[str, list[dict]]]:
    """Every verdict file under judges/: {pass: {judge: [rows]}}. A file whose name is no known pass is skipped."""
    folder = Path(run_dir) / FOLDER
    out: dict[str, dict[str, list[dict]]] = {}
    for file in sorted(folder.glob("*.jsonl")) if folder.is_dir() else []:
        pass_name, _, judge = file.name.removesuffix(".jsonl").partition(".")
        if pass_name in PASSES and judge:
            out.setdefault(pass_name, {})[judge] = read_jsonl(file)
    return out


def cheatbench(run_dir: Path, files: dict[str, dict[str, list[dict]]] | None = None) -> dict[str, dict[str, dict]]:
    """CheatBench's verdicts on the run's episodes, {judge: {episode: row}} by judge name: the eval's (judge.jsonl),
    then the pass "cheatbench" (`files`, pass_files' answer, read when not given), whose row of an episode replaces the
    eval's by the same judge."""
    out: dict[str, dict[str, dict]] = {}
    for row in RunDir(run_dir).verdicts().values():
        out.setdefault(row.get("judge") or "", {})[row["episode"]] = row
    for judge, file_rows in ((pass_files(run_dir) if files is None else files).get("cheatbench") or {}).items():
        out.setdefault(judge, {}).update((row["unit"], row) for row in file_rows)   # a cheatbench unit is an episode
    return dict(sorted(out.items()))


def read(run_dir: Path) -> dict[str, dict[str, list[dict]]]:
    """Every verdict on the run: {pass: {judge: [rows]}}, the pass "cheatbench" with the eval's verdicts in it
    (cheatbench); {} for a run no judge has read."""
    files = pass_files(run_dir)
    judged = cheatbench(run_dir, files)
    return {**files, **({"cheatbench": {judge: list(rows.values()) for judge, rows in judged.items()}}
                        if judged else {})}


def applied(judges: dict[str, object]) -> str | None:
    """The judge whose verdicts the ladders take where several read a run: the first by name; None for none."""
    return min(judges, default=None)


def episode_verdicts(run_dir: Path) -> dict[str, dict]:
    """CheatBench's verdicts on the run by episode, parsed or not, from the judge `applied` picks; {} when none read
    it."""
    judged = cheatbench(run_dir)
    return judged.get(applied(judged)) or {}


def by_unit(file_rows: list[dict]) -> dict[str, dict]:
    """Rows by unit; a later row of a unit wins."""
    return {row["unit"]: row for row in file_rows}


def write(run_dir: Path, pass_name: str, judge: str, new_rows: list[dict]) -> Path:
    """Merge `new_rows` into the pass's file by unit (a unit judged again replaces its row; the others stay, in their
    order, new units after them) and replace the file in one rename. Refuses a row that lacks a key of ROW_KEYS or
    names another judge."""
    for row in new_rows:
        missing = [key for key in ROW_KEYS if key not in row]
        if missing:
            raise ValueError(f"verdict row of {row.get('unit')!r} lacks {', '.join(missing)}")
        if row["judge"] != judge:
            raise ValueError(f"verdict row of {row['unit']!r} is by {row['judge']!r}, not {judge!r}")
    target = path(run_dir, pass_name, judge)
    merged = by_unit(read_jsonl(target)) | by_unit(new_rows)
    return write_atomic(target, "".join(json.dumps(row) + "\n" for row in merged.values()).encode())
