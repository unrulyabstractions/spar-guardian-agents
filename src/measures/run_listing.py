"""The runs of a collection under runs/ and whether each counts: the one reading of a run's validity, which the
transcript viewer (src/ui/transcript_viewer.py) and the judge passes (src/judging/judge_pass.py) share.

    [r.path for r in list_collection(Path("runs/ian_baseline_explorations_1")) if r.status == "valid"]

A study (ian_baseline_explorations_0, ian_baseline_explorations_1) keeps its runs at its top level and in its groups
(src/measures/run_directory.py study_runs). Where the study has an INDEX.jsonl, its valid flag says which runs are
valid. Without one, a run at the top level is valid and a run in a group carries the group's name. A manifest
collection (apart_controls) lists its runs in manifest.jsonl: the canonical copy of each at
<model>/<task>/<cell>/s<seed>/, the extra copies at extra_duplicate_samples/<model>/<task>/<cell>/s<seed>__<host>/.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.measures.run_directory import GROUPS, RunDir, study_runs
from src.utils.json_files import read_jsonl

GROUP_STATUS = {"attempts": "attempt", "set_aside": "set aside"}
"""The status of a run in a study's group, where it differs from the group's name."""
EXTRA = "extra_duplicate_samples"
"""A manifest collection's folder of the extra copies of duplicated runs."""


@dataclass
class Listed:
    """One run of a collection: its key (its path under the collection, which the run's id carries), its folder, its
    status (valid, or why not) and the INDEX.jsonl or manifest.jsonl row that names it."""

    key: str
    path: Path
    status: str
    why: str = ""
    meta: dict = field(default_factory=dict)
    source: str = "study"


def manifest_runs(folder: Path) -> list[Listed]:
    """A manifest collection's runs: the canonical copy is valid, an extra copy is an extra copy."""
    out = []
    for row in read_jsonl(folder / "manifest.jsonl"):
        rel = Path(row["model"], row["task"], row["cell"])
        if row.get("canonical"):
            path, status = folder / rel / f"s{row['seed']}", "valid" if row.get("valid") else "invalid"
        else:
            path, status = folder / EXTRA / rel / f"s{row['seed']}__{row['host']}", "extra copy"
        why = "" if row.get("canonical") else f"one of {row.get('n_valid_copies')} valid copies of {row['run_name']}"
        out.append(Listed(path.relative_to(folder).as_posix(), path, status, why, row, "manifest"))
    return out


def study_listing(folder: Path) -> list[Listed]:
    """A study's runs, with INDEX.jsonl's verdict where the study has one, else the group's name."""
    index = {row["path"]: row for row in read_jsonl(folder / "INDEX.jsonl")}
    out = []
    for path in study_runs(folder):
        key = path.relative_to(folder).as_posix()
        group = path.parent.name if path.parent != folder and path.parent.name in GROUPS else None
        group_status = GROUP_STATUS.get(group, group)
        meta = index.get(key)
        if index and meta is None:
            status, why = "not indexed", "no INDEX.jsonl row names this run"
        elif meta is not None:
            status = "valid" if meta["valid"] else (group_status or "invalid")
            why = "; ".join(meta.get("why") or []) or meta.get("reason") or ""
        else:
            status, why = ("valid", "") if group is None else (group_status, f"in the study's {group}/ folder")
        out.append(Listed(key, path, status, why, meta or {}))
    return out


def list_collection(folder: Path) -> list[Listed]:
    """A collection's runs that hold a run.json: a manifest collection's, else a study's."""
    runs = manifest_runs(folder) if (folder / "manifest.jsonl").is_file() else study_listing(folder)
    return [r for r in runs if RunDir(r.path).is_run()]
