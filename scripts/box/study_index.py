"""The index of a study's runs (src/measures/run_index.py), with study_table.py and the fleet joined in for the study
the table states.

    uv run python -m scripts.box.study_index runs/study_exploration_1            # a summary; writes nothing
    uv run python -m scripts.box.study_index runs/study_exploration_1 --write    # also writes runs/<study>/INDEX.jsonl
    uv run python -m scripts.box.study_index runs/study_exploration_0            # a study without a table

For study_exploration_1 (study_table.TABLE_STUDY) a run's stage, box, cell and arm come from the table, which names
every run of that study and each mock rehearsal. Its instance is the box whose capture manifest
(logs/boxes/<id>/manifest.jsonl) holds a run.json of the run's name with the local run.json's sha256. A run.json whose
bytes no capture holds takes the one instance the box registry (runs/boxes.json) created for its box that has captured
no run of its name: a run pulled from a live box and not captured yet. The summary names a run a box captured under its
name whose bytes no capture holds, and an instance whose registry box is not the table's box for the run. Any other
study gets the index without the table.

--write writes <folder>/INDEX.jsonl. It refuses a folder that holds a run.json, a group, and a folder whose parent is
not a runs/ folder, so a mistyped path never writes into the data folders. Without --write nothing is written.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from scripts.box.fleet_layout import MODELS, REGULATORS, REMOTE_REPO
from scripts.box.study_table import STAGES, TABLE_STUDY, Box, box_jobs, stage_boxes
from src.measures.run_directory import ATTEMPT_FOLDER, ATTEMPTS, GROUPS, RUN_JSON
from src.measures.run_index import ARM_FIELDS, build, index_folder_problem, level, summary, write
from src.models.model_registry import resolve

REMOTE_RUNS = PurePosixPath(REMOTE_REPO) / "runs"
"""Where a box keeps its runs, as the capture manifests name them."""


def study_table_arms() -> dict[str, dict]:
    """Each run name of the study table, real and mock, with its stage, box, cell, arm fields and the table's own
    seed and mock flag. Raises ValueError for a name the table gives two different places."""
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
                                 "disregulator_level": arm.disregulator_level, "tag": arm.tag,
                                 "table_seed": job.seed, "table_mock": mock}
                        if arms.setdefault(job.name, entry) != entry:
                            raise ValueError(f"the study table gives {job.name} two places: {arms[job.name]} and "
                                             f"{entry}")
    return arms


@dataclass(frozen=True)
class Capture:
    """One run.json a box's capture holds: the instance, the run's own name and the file's sha256."""

    instance: int
    name: str
    sha256: str | None


def captured_name(parts: tuple[str, ...]) -> str | None:
    """The run's own name of a box path under runs/<study>/, given as its parts after the study: <name>/run.json or
    <group>/<name>/run.json, an earlier attempt's folder without its .<n>. None for any other path."""
    if len(parts) == 2 and parts[1] == RUN_JSON:
        return parts[0]
    if len(parts) == 3 and parts[2] == RUN_JSON and parts[0] in GROUPS:
        match = ATTEMPT_FOLDER.match(parts[1]) if parts[0] == ATTEMPTS else None
        return match["name"] if match else parts[1]
    return None


def box_captures(root: Path, study: str) -> list[Capture]:
    """Every run.json of the study the box captures under root/logs/boxes/<id>/manifest.jsonl hold."""
    captures = []
    for manifest in sorted((root / "logs" / "boxes").glob("*/manifest.jsonl")):
        if not manifest.parent.name.isdigit():
            continue
        for line in manifest.read_text().splitlines():
            row = json.loads(line) if line.strip() else {}
            remote = PurePosixPath(row.get("box") or "/")
            if remote.is_relative_to(REMOTE_RUNS / study):
                name = captured_name(remote.relative_to(REMOTE_RUNS / study).parts)
                if name is not None:
                    captures.append(Capture(int(manifest.parent.name), name, row.get("sha256")))
    return captures


def registry_boxes(root: Path) -> dict[int, str | None]:
    """Each instance the box registry (root/runs/boxes.json) created, with the study table box it was created for."""
    path = root / "runs" / "boxes.json"
    entries = json.loads(path.read_text()).get("boxes", []) if path.is_file() else []
    return {entry["id"]: entry.get("box") for entry in entries}


def disagreements(rec: dict, arm: dict, data: dict) -> list[str]:
    """Where the study table and the run's own run.json disagree: the seed, the mock flag, the regulator, the
    disregulator, the instruction levels and, for a real run, the models. A disregulator runs the workers' model, so a
    model of its own is a disagreement too."""
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
    if bool(arm["disregulator_level"]) != bool(config.get("disregulators")):
        problems.append(f"disregulators: table {arm['disregulator_level']}, run.json {config.get('disregulators')}")
    if arm["disregulator_level"] and arm["disregulator_level"] != config.get("disregulator_level"):
        problems.append(f"disregulator_level: table {arm['disregulator_level']}, run.json "
                        f"{config.get('disregulator_level')}")
    if (data.get("role_models") or {}).get("disregulator"):
        problems.append(f"disregulator model: table the workers', run.json {data['role_models']['disregulator']}")
    if not rec["mock"]:
        if resolve(arm["worker_model"]) != rec["inspect_model"]:
            problems.append(f"model: table {arm['worker_model']}, run.json {rec['inspect_model']}")
        wanted = resolve(arm["regulator_model"]) if arm["regulator_model"] else None
        if wanted != rec["regulator_inspect_model"]:
            problems.append(f"regulator model: table {arm['regulator_model']}, run.json "
                            f"{rec['regulator_inspect_model']}")
    return problems


class TableJoin:
    """study_table.py and the fleet's captures and registry, joined to the runs of the study the table states
    (run_index.Join)."""

    def __init__(self, study_dir: Path) -> None:
        study_dir = Path(study_dir).resolve()
        root = study_dir.parent.parent
        self.arms = study_table_arms()
        self.captures = box_captures(root, study_dir.name)
        self.box_of = registry_boxes(root)
        self.instances: dict[str, list[int]] = collections.defaultdict(list)
        for instance, box in self.box_of.items():
            if box:
                self.instances[box].append(instance)

    def instance_of(self, run: Path, name: str, box: str | None) -> tuple[int | None, str | None]:
        """(the instance the run ran on, "manifest" or "registry"), or (None, None) when neither names one. A capture
        counts only when it holds this very run.json: a rerun of the name on another instance holds other bytes."""
        local = hashlib.sha256((run / RUN_JSON).read_bytes()).hexdigest()
        held = {capture.instance for capture in self.captures if capture.name == name and capture.sha256 == local}
        if len(held) == 1:
            return held.pop(), "manifest"
        if held:
            return None, None
        named = {capture.instance for capture in self.captures if capture.name == name}
        fresh = [instance for instance in self.instances.get(box or "", []) if instance not in named]
        return (fresh[0], "registry") if len(fresh) == 1 else (None, None)

    def place(self, run: Path, name: str) -> dict:
        arm = self.arms.get(name)
        if arm is None:
            return {}
        instance, source = self.instance_of(run, name, arm["box"])
        return {**{key: arm[key] for key in ARM_FIELDS}, "instance_id": instance, "instance_source": source}

    def problems(self, rec: dict, data: dict) -> list[str]:
        return disagreements(rec, self.arms[rec["run"]], data) if rec["run"] in self.arms else []

    def notes(self, records: list[dict]) -> dict[str, list]:
        named = {capture.name for capture in self.captures}
        return {"not_in_table": [rec["path"] for rec in records if rec["run"] not in self.arms],
                "captured_bytes_differ": [rec["path"] for rec in records
                                          if rec["instance_source"] != "manifest" and rec["run"] in named],
                "instance_box_differs": [(rec["path"], rec["instance_id"], self.box_of.get(rec["instance_id"]),
                                          rec["box"]) for rec in records if rec["instance_id"] is not None
                                         and self.box_of.get(rec["instance_id"]) != rec["box"]]}


def join_for(study_dir: Path) -> TableJoin | None:
    """The table's join for the study the table states, else None."""
    return TableJoin(study_dir) if Path(study_dir).resolve().name == TABLE_STUDY else None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path, help="a study's runs folder, such as runs/study_exploration_1")
    parser.add_argument("--write", action="store_true",
                        help="write <folder>/INDEX.jsonl; without it nothing is written")
    args = parser.parse_args(argv)
    if not args.folder.is_dir():
        parser.error(f"no folder {args.folder}")
    problem = index_folder_problem(args.folder) if args.write else None
    if problem:
        parser.error(f"--write refused: {problem}")
    records, notes = build(args.folder, join_for(args.folder))
    print(summary(args.folder, records, notes))
    if args.write:
        print(f"== wrote {write(args.folder, records)} ({len(records)} records)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
