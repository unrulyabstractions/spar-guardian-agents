"""One record per run of a study, written to runs/<study>/INDEX.jsonl: what the run is, where it ran, its arm, its
outcome and its checks.

    uv run python -m src.measures.run_index runs/study_exploration_1            # a summary; writes nothing
    uv run python -m src.measures.run_index runs/study_exploration_1 --write    # also writes runs/<study>/INDEX.jsonl

A study keeps its runs at runs/<study>/<run> and in its groups, runs/<study>/<group>/<run> (run_directory.study_runs).
A run's folder name is its run.json run_id and the salt its agents' IDs are drawn from, so a run keeps its name
wherever it sits. A record holds these fields, in this order:
- identity: study, run, path (from the study's folder), group, cheatbench_task, domain (the task's domain key,
  src/configuration/task_names.py), row (the domain block's row, or the SWE issue);
- where it ran: stage, box, instance_id, instance_source, cell, kind, target;
- arm: worker, worker_model, rule_level, regulator, regulator_model, regulator_level, tag, seed, mock, commit,
  inspect_model and regulator_inspect_model (the Inspect strings run.json records);
- outcome: eval_status, completed, failed_episodes, rung (run_validity);
- checks: export_flags_ok (run.json validity.ok, the export's own flags), valid, why, reason (run_validity),
  deal_replay and deal_replay_detail (deal_replay: does today's code deal the run the holdings it recorded).

study_exploration_1 (STUDY_TABLE) takes its stage, box, cell and arm from scripts/box/study_table.py, which names every
run of that study. Its instance is the box whose capture manifest (logs/boxes/<id>/manifest.jsonl) lists the run's
run.json, the capture with the local file's sha256 where two boxes list it. A run no box has captured yet takes the one
instance the box registry (runs/boxes.json) holds for its box. Every check of run_validity applies.

Another study takes its cell, kind and worker from the run's name as scripts/screen.py builds it,
<stem>_<kind>_<worker>[mock]<k>, and leaves the study table's fields null. Its validity is null except where a rule
holds for every study: a mock rehearsal never counts, and an SWE run before run_validity.SWE_FIX graded a task one
agent could cheat alone. Every SWE run of study_exploration_0 predates the fix.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath

from src.configuration import task_names
from src.domains import BY_TASK
from src.measures import deal_replay, run_validity
from src.measures.run_directory import GROUPS, RUN_JSON, run_place, study_runs
from src.models.model_registry import resolve

INDEX = "INDEX.jsonl"
STUDY_TABLE = "study_exploration_1"
"""The study scripts/box/study_table.py describes."""
REMOTE_RUNS = PurePosixPath("/root/work/runs")
"""Where a box keeps its runs (scripts/box/fleet_layout.py REMOTE_REPO), as the capture manifests name them."""
SCREEN_NAME = re.compile(r"^(?P<stem>[a-z]+)_(?P<kind>solo_cheat|solo|minimal_regulated|minimal_cheat|minimal_all|"
                         r"minimal)_(?P<worker>[a-z]+?)(?:check)?(?:mock)?(?P<sample>\d+)$")
"""A run name as scripts/screen.py builds it: the cell's stem and kind, the worker's short name, mock or checkmock for
a rehearsal, and the sample's number."""
EVERY_STUDY = ("mock", "swe_before_fix", "swe_commit_unknown")
"""The reasons of run_validity that hold for a run of any study."""
FIELDS = ("study", "run", "path", "group", "cheatbench_task", "domain", "row",
          "stage", "box", "instance_id", "instance_source", "cell", "kind", "target",
          "worker", "worker_model", "rule_level", "regulator", "regulator_model", "regulator_level", "tag", "seed",
          "mock", "commit", "inspect_model", "regulator_inspect_model",
          "eval_status", "completed", "failed_episodes", "rung",
          "export_flags_ok", "valid", "why", "reason", "deal_replay", "deal_replay_detail")
ARM_FIELDS = ("stage", "box", "cell", "kind", "target", "worker", "worker_model", "rule_level", "regulator",
              "regulator_model", "regulator_level", "tag")


def study_table_arms() -> dict[str, dict]:
    """Each run name of the study table, real and mock, with its stage, box, cell, arm fields and the table's own
    seed and mock flag. Raises ValueError for a name the table gives two different places."""
    from scripts.box.fleet_layout import MODELS, REGULATORS  # noqa: PLC0415 - the table lives with the fleet
    from scripts.box.study_table import STAGES, Box, box_jobs, stage_boxes  # noqa: PLC0415

    arms: dict[str, dict] = {}
    for stage in STAGES:
        for box in stage_boxes(stage):
            for arm in box.arms:
                for mock in (False, True):
                    for job in box_jobs(Box(box.name, box.stage, (arm,)), mock=mock):
                        entry = {"stage": stage, "box": box.name, "cell": job.cell, "kind": arm.kind,
                                 "target": arm.target.name, "worker": MODELS[arm.target.model],
                                 "worker_model": arm.target.model, "rule_level": arm.rule_level,
                                 "regulator": REGULATORS[arm.regulator] if arm.regulator else None,
                                 "regulator_model": arm.regulator, "regulator_level": arm.regulator_level,
                                 "tag": arm.tag, "table_seed": job.seed, "table_mock": mock}
                        if arms.setdefault(job.name, entry) != entry:
                            raise ValueError(f"the study table gives {job.name} two places: {arms[job.name]} and "
                                             f"{entry}")
    return arms


def screen_arm(name: str, domain: str | None) -> dict:
    """The arm fields a screen's run name gives: its cell, kind and worker; {} for a name of another shape."""
    match = SCREEN_NAME.match(name)
    if match is None:
        return {}
    return {"cell": f"{domain}.{match['stem']}_{match['kind']}" if domain else None, "kind": match["kind"],
            "worker": match["worker"]}


def box_captures(runs_root: Path) -> dict[tuple[str, str], list[tuple[int, str]]]:
    """Each (study, run) whose run.json a box capture holds, with every (instance, sha256) that captured it, from
    logs/boxes/<id>/manifest.jsonl beside the runs folder."""
    captures: dict[tuple[str, str], list[tuple[int, str]]] = collections.defaultdict(list)
    for manifest in sorted((runs_root.parent / "logs" / "boxes").glob("*/manifest.jsonl")):
        if not manifest.parent.name.isdigit():
            continue
        for line in manifest.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            remote = PurePosixPath(row.get("box") or "/")
            if remote.is_relative_to(REMOTE_RUNS) and remote.name == RUN_JSON:
                parts = remote.relative_to(REMOTE_RUNS).parts
                if len(parts) == 3:
                    captures[parts[0], parts[1]].append((int(manifest.parent.name), row.get("sha256")))
    return captures


def registry_boxes(runs_root: Path) -> dict[str, list[int]]:
    """Each study table box name with the instances the box registry (runs/boxes.json) created for it."""
    path = runs_root / "boxes.json"
    boxes = json.loads(path.read_text()).get("boxes", []) if path.is_file() else []
    instances: dict[str, list[int]] = collections.defaultdict(list)
    for entry in boxes:
        if entry.get("box"):
            instances[entry["box"]].append(entry["id"])
    return instances


def instance_of(run: Path, study: str, box: str | None, captures: dict,
                registry: dict) -> tuple[int | None, str | None]:
    """(the instance the run ran on, where that came from: "manifest" or "registry"), or (None, None) when neither
    names one instance."""
    captured = captures.get((study, run.name), [])
    ids = sorted({instance for instance, _ in captured})
    if len(ids) > 1:
        local = hashlib.sha256((run / RUN_JSON).read_bytes()).hexdigest()
        ids = sorted({instance for instance, sha in captured if sha == local})
    if len(ids) == 1:
        return ids[0], "manifest"
    if not captured and box and len(registry.get(box, [])) == 1:
        return registry[box][0], "registry"
    return None, None


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
    """An instruction level as the study table names it: None for none."""
    return None if value in (None, "none") else value


def record(run: Path, data: dict, study_dir: Path, arms: dict[str, dict] | None, captures: dict,
           registry: dict) -> dict:
    """One run's index record (FIELDS) from its folder and its run.json. arms is the study table's, or None for a
    study without one."""
    config, role_models = data.get("config") or {}, data.get("role_models") or {}
    study, group = run_place(run)
    domain = domain_of(data.get("task"))
    checked = run_validity.validity(run)
    out = {key: None for key in FIELDS}
    out.update(study=study, run=run.name, path=run.relative_to(study_dir).as_posix(), group=group,
               cheatbench_task=data.get("task"), domain=domain, row=row_of(data), seed=config.get("seed"),
               mock=checked["mock"], commit=(data.get("code") or {}).get("commit"), inspect_model=data.get("model"),
               regulator_inspect_model=role_models.get("regulator"))
    if arms is None:
        out.update(screen_arm(run.name, domain), rule_level=level(config.get("rule_level")),
                   regulator_level=level(config.get("regulator_level")) if config.get("regulators") else None)
    else:
        out.update({key: arms[run.name][key] for key in ARM_FIELDS} if run.name in arms else {})
        out["instance_id"], out["instance_source"] = instance_of(run, study, out["box"], captures, registry)
    out.update({key: checked.get(key) for key in ("eval_status", "completed", "failed_episodes", "rung")},
               export_flags_ok=(data.get("validity") or {}).get("ok"))
    if arms is not None:
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


def disagreements(rec: dict, arm: dict, data: dict) -> list[str]:
    """Where the study table and the run's own run.json disagree: the seed, the mock flag, the regulator, the
    instruction levels and, for a real run, the models."""
    config, problems = data.get("config") or {}, []
    if arm["table_seed"] != rec["seed"]:
        problems.append(f"seed: table {arm['table_seed']}, run.json {rec['seed']}")
    if arm["table_mock"] != rec["mock"]:
        problems.append(f"mock: table {arm['table_mock']}, name {rec['mock']}")
    if bool(arm["regulator_model"]) != bool(config.get("regulators")):
        problems.append(f"regulators: table {arm['regulator_model']}, run.json {config.get('regulators')}")
    if arm["rule_level"] != level(config.get("rule_level")):
        problems.append(f"rule_level: table {arm['rule_level']}, run.json {config.get('rule_level')}")
    if arm["regulator_level"] and arm["regulator_level"] != config.get("regulator_level"):
        problems.append(f"regulator_level: table {arm['regulator_level']}, run.json {config.get('regulator_level')}")
    if not rec["mock"]:
        if resolve(arm["worker_model"]) != rec["inspect_model"]:
            problems.append(f"model: table {arm['worker_model']}, run.json {rec['inspect_model']}")
        wanted = resolve(arm["regulator_model"]) if arm["regulator_model"] else None
        if wanted != rec["regulator_inspect_model"]:
            problems.append(f"regulator model: table {arm['regulator_model']}, run.json "
                            f"{rec['regulator_inspect_model']}")
    return problems


def build(study_dir: Path) -> tuple[list[dict], dict[str, list]]:
    """Every run's record, in the order of study_runs, and what the summary reports beside the counts: the folders
    that are neither a run nor a group, the run names found twice, the runs the study table lacks, and where the table
    and a run disagree."""
    study_dir = Path(study_dir)
    runs_root = study_dir.resolve().parent
    arms = study_table_arms() if study_dir.resolve().name == STUDY_TABLE else None
    captures, registry = box_captures(runs_root), registry_boxes(runs_root)
    records, disagree = [], []
    for run in study_runs(study_dir):
        data = json.loads((run / RUN_JSON).read_text())
        rec = record(run, data, study_dir, arms, captures, registry)
        records.append(rec)
        if arms is not None and run.name in arms:
            disagree += [(rec["path"], problem) for problem in disagreements(rec, arms[run.name], data)]
    names = collections.Counter(rec["run"] for rec in records)
    notes = {"other_folders": sorted(p.name for p in study_dir.iterdir()
                                     if p.is_dir() and p.name not in GROUPS and not (p / RUN_JSON).is_file()),
             "names_twice": sorted(name for name, n in names.items() if n > 1),
             "not_in_table": [rec["path"] for rec in records if arms is not None and rec["run"] not in arms],
             "table_disagrees": disagree}
    return records, notes


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
    lines += [f"== {label}: {notes[key] or 'none'}" for key, label in (
        ("other_folders", "folders that are neither a run nor a group"), ("names_twice", "run names found twice"),
        ("not_in_table", "runs the study table lacks"),
        ("table_disagrees", "where the study table and run.json disagree"))]
    return "\n".join(lines)


def write(study_dir: Path, records: list[dict]) -> Path:
    """runs/<study>/INDEX.jsonl, one record per line, through a temporary file so a reader never sees half of it."""
    path = Path(study_dir) / INDEX
    partial = path.with_name(INDEX + ".partial")
    partial.write_text("".join(json.dumps(rec) + "\n" for rec in records))
    partial.replace(path)
    return path


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path, help="a study's runs folder, such as runs/study_exploration_1")
    parser.add_argument("--write", action="store_true", help=f"write <folder>/{INDEX}; without it nothing is written")
    args = parser.parse_args(argv)
    if not args.folder.is_dir():
        parser.error(f"no folder {args.folder}")
    records, notes = build(args.folder)
    print(summary(args.folder, records, notes))
    if args.write:
        print(f"== wrote {write(args.folder, records)} ({len(records)} records)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
