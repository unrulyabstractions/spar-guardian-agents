"""One record per run of a study, for runs/<study>/INDEX.jsonl: what the run is, where it ran, its arm, its outcome and
its checks.

    records, notes = build(Path("runs/<study>"))
    uv run python -m scripts.study.study_index runs/<study> [--write]     # the command: the summary, and INDEX.jsonl

A study keeps its runs at runs/<study>/<run> and in its groups, runs/<study>/<group>/<run> (run_directory.study_runs).
A record holds these fields, in this order:
- identity: study, run (the run's own name, run.json's run_id), path (its folder, from the study's folder; a reader
  keys runs by it), group, cheatbench_task, domain (the task's domain key, src/configuration/task_names.py), row (the
  domain block's row, or the SWE issue);
- where it ran: stage, box, instance_id, instance_source, cell, kind, target;
- arm: worker, worker_model, rule_level, regulator, regulator_model, regulator_level, disregulator_level, tag, seed,
  mock, commit, inspect_model and regulator_inspect_model (the Inspect strings run.json records), model_key and
  regulator_model_key (their entries in src/models/model_registry.yaml, None outside it);
- outcome: eval_status, completed, failed_episodes (run_validity), cheating_rung (the team's Cheating rung,
  cheating_ladder.cheating_rungs; None without a team row), regulator_posts (0 for a regulator that took turns and
  never posted: a result, so its run stays valid; None in a run without a regulator), disregulator_posts (likewise:
  what a disregulator did with its role is the run's label, disregulator_roles), alerts, true_alarms and false_alarms
  (the influencers' alert_humans calls and how many the Cheating ladder calls true or false, run.json signals;
  src/measures/alert_outcomes.py; None in a run with no alert);
- checks: export_flags_ok (run.json validity.ok, the export's own flags), valid (True or False for every run), why,
  reason (run_validity), deal_replay and deal_replay_detail (deal_replay: does today's code deal the run the holdings
  it recorded).

An earlier attempt, runs/<study>/attempts/<name>.<n>, keeps its run's name in `run` and its own folder in `path`. It is
listed as superseded: valid false, reason superseded. The check for a run name found twice skips it.

A run takes its cell, kind and worker from its name as scripts/study/screen.py builds it,
<stem>_<kind>_<worker>[mock]<k>, and leaves stage, box, instance_id, instance_source, target and tag null: the study
table that once filled them for ian_baseline_explorations_1 is gone, and that study's INDEX.jsonl keeps what it wrote.
Every run's validity is run_validity's verdict, the same checks in every study: an earlier attempt and a mock
rehearsal never count, and an SWE run before run_validity.SWE_FIX graded a task one agent could cheat alone. Every
SWE run of ian_baseline_explorations_0 predates the fix.
"""
from __future__ import annotations

import collections
import json
import re
from pathlib import Path

from src.configuration import task_names
from src.domains import BY_TASK
from src.measures import deal_replay, run_validity
from src.measures.cheating_ladder import cheating_rungs
from src.measures.run_directory import ATTEMPTS, GROUPS, RunDir, run_name, run_place, study_runs
from src.models import model_registry

INDEX = "INDEX.jsonl"
SCREEN_NAME = re.compile(r"^(?P<stem>[a-z]+)_(?P<kind>solo_cheat|solo|minimal_regulated|minimal_cheat|minimal_all|"
                         r"minimal)_(?P<worker>[a-z]+?)(?:check)?(?:mock)?(?P<sample>\d+)$")
"""A run name as scripts/study/screen.py builds it: the cell's stem and kind, the worker's short name, mock or
checkmock for a rehearsal, and the sample's number."""
FIELDS = ("study", "run", "path", "group", "cheatbench_task", "domain", "row",
          "stage", "box", "instance_id", "instance_source", "cell", "kind", "target",
          "worker", "worker_model", "rule_level", "regulator", "regulator_model", "regulator_level",
          "disregulator_level", "tag", "seed", "mock", "commit", "inspect_model", "regulator_inspect_model",
          "model_key", "regulator_model_key",
          "eval_status", "completed", "failed_episodes", "cheating_rung", "regulator_posts", "disregulator_posts",
          "alerts", "true_alarms", "false_alarms", "export_flags_ok", "valid", "why", "reason", "deal_replay",
          "deal_replay_detail")
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


def model_key(model: str | None) -> str | None:
    """The registry entry of an Inspect model string; None for no model or one outside the registry."""
    return model_registry.entry_name(model) if model else None


def level(value: str | None) -> str | None:
    """An instruction level as a record names it: None for none."""
    return None if value in (None, "none") else value


def role_posts(checked: dict, role: str) -> int | None:
    """The posts on the board of a role's agents in a run_validity record; None in a run without that role."""
    agents = checked.get(f"{role}s")
    return sum(agent["posts"] for agent in agents) if agents else None


def record(run: Path, data: dict, study_dir: Path) -> dict:
    """One run's index record (FIELDS) from its folder and its run.json."""
    config, role_models = data.get("config") or {}, data.get("role_models") or {}
    (study, group), name = run_place(run), run_name(run)
    domain = domain_of(data.get("task"))
    checked = run_validity.validity(run)
    out = {key: None for key in FIELDS}
    out.update(study=study, run=name, path=run.relative_to(study_dir).as_posix(), group=group,
               cheatbench_task=data.get("task"), domain=domain, row=row_of(data), seed=config.get("seed"),
               mock=checked["mock"], commit=(data.get("code") or {}).get("commit"), inspect_model=data.get("model"),
               regulator_inspect_model=role_models.get("regulator"), model_key=model_key(data.get("model")),
               regulator_model_key=model_key(role_models.get("regulator")))
    out.update(screen_arm(name, domain), rule_level=level(config.get("rule_level")),
               regulator_level=level(config.get("regulator_level")) if config.get("regulators") else None,
               disregulator_level=level(config.get("disregulator_level")) if config.get("disregulators") else None)
    out.update({key: checked.get(key) for key in ("eval_status", "completed", "failed_episodes")},
               cheating_rung=cheating_rungs(run)["team"] if RunDir(run).team_signals() else None,
               export_flags_ok=(data.get("validity") or {}).get("ok"), regulator_posts=role_posts(checked, "regulator"),
               disregulator_posts=role_posts(checked, "disregulator"))
    out.update(valid=checked["valid"], why=checked["why"], reason=checked["reason"])
    if data.get("alerts"):
        team = RunDir(run).team_signals() or [{}]
        out.update(alerts=len(data["alerts"]), true_alarms=team[0].get("true_alarms"),
                   false_alarms=team[0].get("false_alarms"))
    replayed = deal_replay.replay(data)
    if replayed is not None:
        out.update(deal_replay=replayed.status, deal_replay_detail=replayed.detail or None)
    return out


def build(study_dir: Path) -> tuple[list[dict], dict[str, list]]:
    """Every run's record, in the order of study_runs, and what the summary reports beside the counts: the folders
    that are neither a run nor a group, and the run names found twice (an earlier attempt aside)."""
    study_dir = Path(study_dir)
    records = [record(run, RunDir(run).run_json, study_dir) for run in study_runs(study_dir)]
    names = collections.Counter(rec["run"] for rec in records if rec["group"] != ATTEMPTS)
    notes = {"other_folders": sorted(p.name for p in study_dir.iterdir()
                                     if p.is_dir() and p.name not in GROUPS and not RunDir(p).is_run()),
             "names_twice": sorted(name for name, n in names.items() if n > 1)}
    return records, notes


NOTE_LABELS = {"other_folders": "folders that are neither a run nor a group", "names_twice": "run names found twice"}
"""Each note's line in the summary."""


def summary(study_dir: Path, records: list[dict], notes: dict[str, list]) -> str:
    """The counts and the notes, one line each."""
    real = [rec for rec in records if not rec["mock"]]

    def counted(key: str) -> str:
        counts = collections.Counter(str(rec[key]) for rec in records)
        return ", ".join(f"{value} {n}" for value, n in sorted(counts.items()))

    lines = [f"== {study_dir}: {len(records)} runs: {sum(rec['valid'] is True for rec in real)} valid, "
             f"{sum(rec['valid'] is False for rec in real)} not, {len(records) - len(real)} mock",
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
    if RunDir(folder).is_run():
        return f"{folder} holds a run: it is a run, not a study's folder"
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
