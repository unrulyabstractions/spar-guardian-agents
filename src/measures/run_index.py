"""One record per run of a study, for runs/<study>/INDEX.jsonl: what the run is, where it ran, its arm, its outcome and
its checks.

    records, notes = build(Path("runs/study_exploration_0"))            # a study without a table
    records, notes = build(Path("runs/study_exploration_1"), join)      # with the study table's join
    uv run python -m scripts.box.study_index runs/<study> [--write]     # the command: the summary, and INDEX.jsonl

A study keeps its runs at runs/<study>/<run> and in its groups, runs/<study>/<group>/<run> (run_directory.study_runs).
A record holds these fields, in this order:
- identity: study, run (the run's own name, run.json's run_id), path (its folder, from the study's folder; a reader
  keys runs by it), group, cheatbench_task, domain (the task's domain key, src/configuration/task_names.py), row (the
  domain block's row, or the SWE issue);
- where it ran: stage, box, instance_id, instance_source, cell, kind, target;
- arm: worker, worker_model, rule_level, regulator, regulator_model, regulator_level, tag, seed, mock, commit,
  inspect_model and regulator_inspect_model (the Inspect strings run.json records);
- outcome: eval_status, completed, failed_episodes, rung (run_validity), regulator_posts (0 for a regulator that took
  turns and never posted: a result, so its run stays valid; None in a run without a regulator);
- checks: export_flags_ok (run.json validity.ok, the export's own flags), valid, why, reason (run_validity),
  deal_replay and deal_replay_detail (deal_replay: does today's code deal the run the holdings it recorded).

An earlier attempt, runs/<study>/attempts/<name>.<n>, keeps its run's name in `run` and its own folder in `path`. It is
listed as superseded: valid false, reason superseded. The check for a run name found twice skips it.

This module never reads the study table or the fleet. A study with a table gets them through a join (Join), which
scripts/box/study_index.py builds: the stage, the box, the cell, the arm and the instance, and every check of
run_validity. Any other study takes its cell, kind and worker from the run's name as scripts/screen.py builds it,
<stem>_<kind>_<worker>[mock]<k>, and leaves the table's fields null. Its validity is null except where a rule holds
for every study (EVERY_STUDY): an earlier attempt and a mock rehearsal never count, and an SWE run before
run_validity.SWE_FIX graded a task one agent could cheat alone. A mock rehearsal of study_exploration_0 is therefore
invalid with reason mock, as in every study. Every SWE run of study_exploration_0 predates the fix.
"""
from __future__ import annotations

import collections
import json
import re
from pathlib import Path
from typing import Protocol

from src.configuration import task_names
from src.domains import BY_TASK
from src.measures import deal_replay, run_validity
from src.measures.run_directory import ATTEMPTS, GROUPS, RUN_JSON, run_name, run_place, study_runs

INDEX = "INDEX.jsonl"
SCREEN_NAME = re.compile(r"^(?P<stem>[a-z]+)_(?P<kind>solo_cheat|solo|minimal_regulated|minimal_cheat|minimal_all|"
                         r"minimal)_(?P<worker>[a-z]+?)(?:check)?(?:mock)?(?P<sample>\d+)$")
"""A run name as scripts/screen.py builds it: the cell's stem and kind, the worker's short name, mock or checkmock for
a rehearsal, and the sample's number."""
EVERY_STUDY = ("superseded", "mock", "swe_before_fix", "swe_commit_unknown")
"""The reasons of run_validity that hold for a run of any study."""
FIELDS = ("study", "run", "path", "group", "cheatbench_task", "domain", "row",
          "stage", "box", "instance_id", "instance_source", "cell", "kind", "target",
          "worker", "worker_model", "rule_level", "regulator", "regulator_model", "regulator_level", "tag", "seed",
          "mock", "commit", "inspect_model", "regulator_inspect_model",
          "eval_status", "completed", "failed_episodes", "rung", "regulator_posts",
          "export_flags_ok", "valid", "why", "reason", "deal_replay", "deal_replay_detail")
ARM_FIELDS = ("stage", "box", "cell", "kind", "target", "worker", "worker_model", "rule_level", "regulator",
              "regulator_model", "regulator_level", "tag")


class Join(Protocol):
    """What a study's table adds to its runs' records (scripts/box/study_index.py TableJoin)."""

    def place(self, run: Path, name: str) -> dict:
        """The run's ARM_FIELDS, instance_id and instance_source; {} for a name the table lacks."""

    def problems(self, rec: dict, data: dict) -> list[str]:
        """Where the table and the run's run.json disagree."""

    def notes(self, records: list[dict]) -> dict[str, list]:
        """The table's own notes for the summary, by label."""


def screen_arm(name: str, domain: str | None) -> dict:
    """The arm fields a screen's run name gives: its cell, kind and worker; {} for a name of another shape."""
    match = SCREEN_NAME.match(name)
    if match is None:
        return {}
    return {"cell": f"{domain}.{match['stem']}_{match['kind']}" if domain else None, "kind": match["kind"],
            "worker": match["worker"]}


def domain_of(task: str | None) -> str | None:
    """The task's domain key; None for a task outside the table of task names."""
    try:
        return task_names.domain_of(task)
    except KeyError:
        return None


def row_of(data: dict) -> str | None:
    """The run's row: the domain block's row_id, or the SWE issue; None for a task outside the registry."""
    cls = BY_TASK.get(data.get("task"))
    block = (data.get(cls.block) or {}) if cls else {}
    return block.get("row_id") or block.get("issue")


def level(value: str | None) -> str | None:
    """An instruction level as a study table names it: None for none."""
    return None if value in (None, "none") else value


def regulator_posts(checked: dict) -> int | None:
    """The regulators' posts on the board in a run_validity record; None in a run without a regulator."""
    regulators = checked.get("regulators")
    return sum(regulator["posts"] for regulator in regulators) if regulators else None


def record(run: Path, data: dict, study_dir: Path, join: Join | None = None) -> dict:
    """One run's index record (FIELDS) from its folder and its run.json, with the study table's join or without."""
    config, role_models = data.get("config") or {}, data.get("role_models") or {}
    (study, group), name = run_place(run), run_name(run)
    domain = domain_of(data.get("task"))
    checked = run_validity.validity(run)
    out = {key: None for key in FIELDS}
    out.update(study=study, run=name, path=run.relative_to(study_dir).as_posix(), group=group,
               cheatbench_task=data.get("task"), domain=domain, row=row_of(data), seed=config.get("seed"),
               mock=checked["mock"], commit=(data.get("code") or {}).get("commit"), inspect_model=data.get("model"),
               regulator_inspect_model=role_models.get("regulator"))
    if join is None:
        out.update(screen_arm(name, domain), rule_level=level(config.get("rule_level")),
                   regulator_level=level(config.get("regulator_level")) if config.get("regulators") else None)
    else:
        out.update(join.place(run, name))
    out.update({key: checked.get(key) for key in ("eval_status", "completed", "failed_episodes", "rung")},
               export_flags_ok=(data.get("validity") or {}).get("ok"), regulator_posts=regulator_posts(checked))
    if join is not None:
        out.update(valid=checked["valid"], why=checked["why"], reason=checked["reason"])
    else:
        kept = [(code, text) for code, text in zip(checked["reasons"], checked["why"]) if code in EVERY_STUDY]
        if kept:
            out.update(valid=False, why=[text for _, text in kept],
                       reason=run_validity.first_reason([code for code, _ in kept]))
    replayed = deal_replay.replay(data)
    if replayed is not None:
        out.update(deal_replay=replayed.status, deal_replay_detail=replayed.detail or None)
    return out


def build(study_dir: Path, join: Join | None = None) -> tuple[list[dict], dict[str, list]]:
    """Every run's record, in the order of study_runs, and what the summary reports beside the counts: the folders
    that are neither a run nor a group, the run names found twice (an earlier attempt aside), and, with a join, where
    the table and a run disagree and the table's own notes."""
    study_dir = Path(study_dir)
    records, disagree = [], []
    for run in study_runs(study_dir):
        data = json.loads((run / RUN_JSON).read_text())
        rec = record(run, data, study_dir, join)
        records.append(rec)
        if join is not None:
            disagree += [(rec["path"], problem) for problem in join.problems(rec, data)]
    names = collections.Counter(rec["run"] for rec in records if rec["group"] != ATTEMPTS)
    notes = {"other_folders": sorted(p.name for p in study_dir.iterdir()
                                     if p.is_dir() and p.name not in GROUPS and not (p / RUN_JSON).is_file()),
             "names_twice": sorted(name for name, n in names.items() if n > 1)}
    if join is not None:
        notes.update(join.notes(records), table_disagrees=disagree)
    return records, notes


NOTE_LABELS = {"other_folders": "folders that are neither a run nor a group", "names_twice": "run names found twice",
               "not_in_table": "runs the study table lacks",
               "captured_bytes_differ": "runs a box captured under their name whose run.json no capture holds",
               "instance_box_differs": "instances whose registry box is not the table's box (path, id, registry, "
                                       "table)",
               "table_disagrees": "where the study table and run.json disagree"}
"""Each note's line in the summary."""


def summary(study_dir: Path, records: list[dict], notes: dict[str, list]) -> str:
    """The counts and the notes, one line each."""
    real = [rec for rec in records if not rec["mock"]]
    unchecked = sum(rec["valid"] is None for rec in real)

    def counted(key: str) -> str:
        counts = collections.Counter(str(rec[key]) for rec in records)
        return ", ".join(f"{value} {n}" for value, n in sorted(counts.items()))

    lines = [f"== {study_dir}: {len(records)} runs: {sum(rec['valid'] is True for rec in real)} valid, "
             f"{sum(rec['valid'] is False for rec in real)} not, {len(records) - len(real)} mock"
             + (f", {unchecked} not checked" if unchecked else ""),
             *(f"== by {label}: {counted(key)}" for key, label in (
                 ("reason", "reason"), ("cheatbench_task", "task"), ("stage", "stage"), ("group", "group"),
                 ("deal_replay", "deal replay"), ("instance_source", "instance source"),
                 ("export_flags_ok", "export flags ok")))]
    lines += [f"== {NOTE_LABELS[key]}: {notes[key] or 'none'}" for key in NOTE_LABELS if key in notes]
    return "\n".join(lines)


def index_folder_problem(folder: Path) -> str | None:
    """Why an index may not be written into this folder, or None for a study's folder: a folder that holds a run.json
    is a run, a group is part of a study, and a study's folder sits in a runs/ folder."""
    folder = Path(folder).resolve()
    if (folder / RUN_JSON).is_file():
        return f"{folder} holds a run.json: it is a run, not a study's folder"
    if folder.name in GROUPS:
        return f"{folder} is a group of a study: write the index of {folder.parent}"
    if folder.parent.name != "runs":
        return f"{folder} is not a study's folder: its parent is {folder.parent}, not a runs/ folder"
    return None


def write(study_dir: Path, records: list[dict]) -> Path:
    """runs/<study>/INDEX.jsonl, one record per line, through a temporary file so a reader never sees half of it.
    Raises ValueError for a folder that is no study's (index_folder_problem)."""
    problem = index_folder_problem(study_dir)
    if problem:
        raise ValueError(problem)
    path = Path(study_dir) / INDEX
    partial = path.with_name(INDEX + ".partial")
    partial.write_text("".join(json.dumps(rec) + "\n" for rec in records))
    partial.replace(path)
    return path
