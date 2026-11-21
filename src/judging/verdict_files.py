"""The judges' verdicts stored beside a run: the one reader and the one writer of runs/<study>/<run>/judges/.

    read(run_dir)                                   # {"cheatbench": {"gpt-6-luna": [rows]}, "labels": {...}}
    rows(run_dir, LABELS_PASS, "gpt-6-luna")        # one file's rows
    write(run_dir, LABELS_PASS, "gpt-6-luna", new)  # merge rows into that file, by unit, atomically
    write(run_dir, LABELS_PASS, "gpt-6-luna", new, keep={"s#1@k4q8z"})   # and drop the rows of other units
    cheatbench(run_dir)                             # {judge: {episode: row}}: the eval's judge and the passes'
    episode_verdicts(run_dir)                       # {episode: row} of the judge the measures take
    applied(read(run_dir)[LABELS_PASS])             # the judge whose verdicts the measures take: the first by name

Every verdict on a run sits in judges/<pass>.<judge>.jsonl, one row per unit, keyed by the row's "unit". A unit is
the episode ("<sample>#<epoch>@<agent>", or "<sample>#<epoch>" for a lone agent), and the row names its agent ("agent",
the bare ID) and role beside it. Two writers fill the files: a judge pass (src/judging/judge_pass.py) and the export
(src/export/run_export.py), which writes the verdicts of the judge that ran inside the eval as its scorer into the pass
"cheatbench". A unit judged again replaces its row; every other row stays, unless the writer names the units to keep,
when the rows of any other unit go (a unit the pass no longer gives the run). A file of a pass no longer listed (the
retired "collusion" and "scheming") is not read. A write goes to a temporary file in judges/ that then replaces the
file in one rename, so a reader never sees half a file, and nothing outside judges/ is touched.

Every row carries what makes it reproducible and priced: the unit, the judge entry and its generation config, the
hashes of the prompt, the schema and the exact input, parse_ok, the call's usage, its cost in dollars, the attempts and
the error (ROW_KEYS), None where the eval's judge recorded none. write refuses a row without them, and a row of another
judge. Where several judges read a run, the measures take the first by name (applied).
"""
from __future__ import annotations

import json
from pathlib import Path

from src.sandbox.staging import write_atomic
from src.utils.json_files import read_jsonl

FOLDER = "judges"
CHEATBENCH_PASS = "cheatbench"
"""CheatBench's verdicts per episode (src/judging/cheatbench_judge.py)."""
LABELS_PASS = "labels"
"""Disclosure and manipulation per agent (a unit is the agent's episode), each a score with an excerpt
(src/judging/labels_judge.py), read by src/measures/judge_labels.py labels."""
PASSES = (CHEATBENCH_PASS, LABELS_PASS)
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


def cheatbench(run_dir: Path) -> dict[str, dict[str, dict]]:
    """CheatBench's verdicts on the run's episodes, {judge: {episode: row}} by judge name."""
    files = pass_files(run_dir).get(CHEATBENCH_PASS) or {}
    return {judge: by_unit(file_rows) for judge, file_rows in sorted(files.items())}


def read(run_dir: Path) -> dict[str, dict[str, list[dict]]]:
    """Every verdict on the run: {pass: {judge: [rows]}}; {} for a run no judge has read."""
    return pass_files(run_dir)


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


def write(run_dir: Path, pass_name: str, judge: str, new_rows: list[dict], keep: set[str] | None = None) -> Path:
    """Merge `new_rows` into the pass's file by unit (a unit judged again replaces its row; the others stay, in their
    order, new units after them, except, when `keep` is given, a held row of a unit not in it) and replace the file in
    one rename. Refuses a row that lacks a key of ROW_KEYS, names another judge, or is of a unit not in `keep`."""
    for row in new_rows:
        missing = [key for key in ROW_KEYS if key not in row]
        if missing:
            raise ValueError(f"verdict row of {row.get('unit')!r} lacks {', '.join(missing)}")
        if row["judge"] != judge:
            raise ValueError(f"verdict row of {row['unit']!r} is by {row['judge']!r}, not {judge!r}")
        if keep is not None and row["unit"] not in keep:
            raise ValueError(f"verdict row of {row['unit']!r} is of no unit to keep")
    target = path(run_dir, pass_name, judge)
    held = by_unit(read_jsonl(target))
    merged = {u: r for u, r in held.items() if keep is None or u in keep} | by_unit(new_rows)
    return write_atomic(target, "".join(json.dumps(row) + "\n" for row in merged.values()).encode())
