"""run_index and its command scripts/box/study_index.py: one record per run of a study, from its folder, its eval log,
and for the table's study the study table, the box captures and the box registry. Every case builds a copy of the
layout under tmp_path: runs/<study>/[<group>/]<run>, the eval log at the same path under logs/, runs/boxes.json and
logs/boxes/<id>/manifest.jsonl. The runs carry real names of scripts/box/study_table.py, and their run.json comes from
today's plan, as a run of today's code records it."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from scripts.box import study_index
from scripts.box.study_index import join_for, main
from src import tasks
from src.configuration import settings
from src.configuration.presets import build_config
from src.domains import BY_TASK
from src.measures import run_index, run_validity
from src.measures.run_index import FIELDS, INDEX, build
from src.models.model_registry import resolve
from tests.support.run_builder import write_status_log

STUDY = "study_exploration_1"
LOG = "2026-11-01T03-31-41-00-00_run_abc.eval"
LUNA, OPUS, DSFLASH = "gpt-6-luna-openrouter", "claude-opus-5-5-openrouter", "deepseek-flash-openrouter"
REFUSAL = "API integrators: you can reduce refusals by configuring a fallback model"
ROWS = {"protein_design": ("protein", {"row_id": "alfalfa_chi"}),
        "creative_writings": ("creative", {"row_id": "museum_poem"}),
        "software_engineering": ("swe", {"issue": "flipt-02e2", "honeypot": "jigsaw"})}


def make_run(root: Path, name: str, task: str, *, study: str = STUDY, group: str | None = None, seed: int = 1,
             model: str = LUNA, regulator: str | None = None, regulator_level: str = "ambiguous",
             rule_level: str = "none", commit: str = "0dda147", status: str = "success",
             regulator_says: str = "I read the board.", flags_ok: bool = True, folder_name: str | None = None) -> Path:
    """A run folder and its eval log, run.json as today's plan deals it. folder_name is an earlier attempt's folder,
    <name>.<n>."""
    block, row = ROWS[task]
    pairs = [f"seed={seed}", f"id_salt={name}", f"rule_level={rule_level}", f"regulator_level={regulator_level}",
             *(["n_agents=4", "regulators=1"] if regulator else [])]
    config = build_config("default", "small", pairs)
    roles = tasks.hidden_roles(config) or {}
    plan = BY_TASK[task](row.get("row_id") or row["issue"]).plan(config, roles or None)
    mock = bool(run_validity.MOCK.search(name))
    data = {"run_id": name, "task": task, block: row, "config": config.model_dump(mode="json"), "agents": plan.agents,
            "access": {"policy": plan.access, "agents": {a: {"holds": items} for a, items in plan.holds.items()}},
            "code": {"commit": commit}, "model": "mockllm/model" if mock else resolve(model),
            "role_models": {"worker": None, "regulator": resolve(regulator) if regulator and not mock else None,
                            "disregulator": None},
            "rules": {"workers": [a for a in plan.agents if roles.get(a, "worker") == "worker"], "posts": {}},
            "validity": {"ok": flags_ok}, "eval_log": f"/root/work/logs/{study}/{name}/{LOG}"}
    folder = root / "runs" / study / (group or "") / (folder_name or name)
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(json.dumps(data))
    (folder / "signals.jsonl").write_text(json.dumps({"row": "team", "rung": "exposed"}) + "\n")
    (folder / "episodes.jsonl").write_text(json.dumps({"failure": None}) + "\n")
    for agent in (a for a, role in roles.items() if role == "regulator"):
        transcript = folder / "trajectories" / f"row#1@{agent}" / "transcript.json"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(json.dumps({"trajectory": {"steps": [{"source": "agent", "message": regulator_says}]}}))
    write_status_log(root / "logs" / study / (group or "") / (folder_name or name) / LOG, status)
    return folder


def register(root: Path, boxes: dict[int, str]) -> None:
    """runs/boxes.json with these instances, each created for a box of the study table."""
    entries = [{"id": instance, "box": box} for instance, box in boxes.items()]
    (root / "runs" / "boxes.json").write_text(json.dumps({"boxes": entries}))


def capture(root: Path, instance: int, runs: list[Path], *, stale: bool = False) -> None:
    """logs/boxes/<instance>/manifest.jsonl listing each run's run.json as the box held it, at the same path under the
    study as here."""
    study = root / "runs" / STUDY
    rows = [{"box": f"/root/work/runs/{STUDY}/{run.relative_to(study).as_posix()}/run.json", "kind": "file",
             "local": str(run / "run.json"),
             "sha256": "0" * 64 if stale else hashlib.sha256((run / "run.json").read_bytes()).hexdigest()}
            for run in runs]
    folder = root / "logs" / "boxes" / str(instance)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "manifest.jsonl").open("a") as out:
        out.writelines(json.dumps(row) + "\n" for row in rows)


def records_by_run(root: Path, study: str = STUDY) -> tuple[dict[str, dict], dict]:
    records, notes = build(root / "runs" / study, join_for(root / "runs" / study))
    return {rec["path"]: rec for rec in records}, notes


def test_a_run_of_study_one_takes_its_place_and_arm_from_the_study_table(tmp_path):
    make_run(tmp_path, "binder_minimal_regulated_luna_opus_ambiguous1", "protein_design", regulator=OPUS,
             regulator_says=REFUSAL)
    rec = records_by_run(tmp_path)[0]["binder_minimal_regulated_luna_opus_ambiguous1"]
    assert list(rec) == list(FIELDS)
    assert {key: rec[key] for key in ("study", "path", "group", "cheatbench_task", "domain", "row")} == {
        "study": STUDY, "path": "binder_minimal_regulated_luna_opus_ambiguous1", "group": None,
        "cheatbench_task": "protein_design", "domain": "protein_design", "row": "alfalfa_chi"}
    assert {key: rec[key] for key in run_index.ARM_FIELDS} == {
        "stage": "phase2-protein-luna", "box": "phase2-protein-luna-4",
        "cell": "protein_design.binder_minimal_regulated", "kind": "minimal_regulated", "target": "protein-luna",
        "worker": "luna", "worker_model": LUNA,
        "rule_level": None, "regulator": "opus", "regulator_model": OPUS, "regulator_level": "ambiguous", "tag": None}
    assert (rec["seed"], rec["mock"], rec["commit"], rec["inspect_model"], rec["regulator_inspect_model"]) == (
        1, False, "0dda147", resolve(LUNA), resolve(OPUS))
    assert (rec["eval_status"], rec["completed"], rec["failed_episodes"], rec["rung"], rec["export_flags_ok"]) == (
        "success", True, [], "exposed", True)
    assert (rec["valid"], rec["reason"], rec["deal_replay"], rec["deal_replay_detail"]) == (
        False, "regulator_refused", "same", None)


def test_the_instance_is_the_box_that_captured_the_run_else_the_one_the_registry_holds(tmp_path):
    protein = make_run(tmp_path, "binder_minimal_luna1", "protein_design")
    retried = make_run(tmp_path, "flipt_minimal_luna1", "software_engineering")
    make_run(tmp_path, "flipt_minimal_luna2", "software_engineering", seed=2)
    make_run(tmp_path, "flipt_minimal_luna_fixed1", "software_engineering", commit="94940bc")
    register(tmp_path, {1001: "gate-protein-luna-1", 1002: "gate-flipt-luna-1", 1003: "gate-flipt-luna-1",
                        1004: "fixed-gate-flipt-luna-1"})
    capture(tmp_path, 1001, [protein])
    capture(tmp_path, 1002, [retried], stale=True)          # a first box, whose copy the second box's replaced
    capture(tmp_path, 1003, [retried])
    recs = records_by_run(tmp_path)[0]
    assert (recs["binder_minimal_luna1"]["instance_id"], recs["binder_minimal_luna1"]["instance_source"]) == (
        1001, "manifest")
    assert (recs["flipt_minimal_luna1"]["instance_id"], recs["flipt_minimal_luna1"]["instance_source"]) == (
        1003, "manifest")
    assert recs["flipt_minimal_luna2"]["instance_id"] is None          # two boxes of that name, and no capture
    assert (recs["flipt_minimal_luna_fixed1"]["instance_id"], recs["flipt_minimal_luna_fixed1"]["instance_source"]) == (
        1004, "registry")


def test_an_instance_is_credited_only_for_the_bytes_its_capture_holds(tmp_path):
    """A rerun of a name on a re-rented box, pulled here and not captured yet: the first box's capture holds other
    bytes, so the run goes to the one instance of its box that captured no run of its name, and the summary says so."""
    rerun = make_run(tmp_path, "flipt_minimal_luna1", "software_engineering")
    register(tmp_path, {2001: "gate-flipt-luna-1", 2002: "gate-flipt-luna-1"})
    capture(tmp_path, 2001, [rerun], stale=True)
    recs, notes = records_by_run(tmp_path)
    assert (recs["flipt_minimal_luna1"]["instance_id"], recs["flipt_minimal_luna1"]["instance_source"]) == (
        2002, "registry")
    assert notes["captured_bytes_differ"] == ["flipt_minimal_luna1"]
    capture(tmp_path, 2002, [rerun])
    recs, notes = records_by_run(tmp_path)
    assert recs["flipt_minimal_luna1"]["instance_source"] == "manifest" and notes["captured_bytes_differ"] == []


def test_the_summary_names_an_instance_whose_registry_box_is_not_the_tables(tmp_path, capsys):
    run = make_run(tmp_path, "binder_minimal_luna1", "protein_design")
    register(tmp_path, {3001: "gate-creative-luna-1"})
    capture(tmp_path, 3001, [run])
    recs, notes = records_by_run(tmp_path)
    assert recs["binder_minimal_luna1"]["instance_id"] == 3001
    assert notes["instance_box_differs"] == [("binder_minimal_luna1", 3001, "gate-creative-luna-1",
                                              "gate-protein-luna-1")]
    assert main([str(tmp_path / "runs" / STUDY)]) == 0
    assert "registry box is not the table's box" in capsys.readouterr().out


ARM = "binder_minimal_regulated_luna_opus_ambiguous1"


@pytest.mark.parametrize(("change", "problem"), [
    ({"seed": 2}, "seed: table 1, run.json 2"),
    ({"mock": True}, "mock: table False, name True"),
    ({"regulators": 0}, f"regulators: table {OPUS}, run.json 0"),
    ({"rule_level": "generic"}, "rule_level: table None, run.json generic"),
    ({"regulator_level": "enforce"}, "regulator_level: table ambiguous, run.json enforce"),
    ({"inspect_model": resolve(DSFLASH)}, f"model: table {LUNA}, run.json {resolve(DSFLASH)}"),
    ({"regulator_inspect_model": resolve(LUNA)}, f"regulator model: table {OPUS}, run.json {resolve(LUNA)}"),
])
def test_each_disagreement_of_the_table_and_run_json_is_named(change, problem):
    """Each of the seven checks finds its own field and nothing else."""
    arm = study_index.study_table_arms()[ARM]
    rec = {"seed": 1, "mock": False, "inspect_model": resolve(LUNA), "regulator_inspect_model": resolve(OPUS)}
    config = {"regulators": 1, "rule_level": "none", "regulator_level": "ambiguous"}
    assert study_index.disagreements(rec, arm, {"config": config}) == []
    rec.update({key: value for key, value in change.items() if key in rec})
    config.update({key: value for key, value in change.items() if key in config})
    assert study_index.disagreements(rec, arm, {"config": config}) == [problem]


def test_the_record_keeps_the_exports_own_flags_and_an_swe_runs_issue_as_its_row(tmp_path):
    make_run(tmp_path, "binder_minimal_luna1", "protein_design", flags_ok=False)
    make_run(tmp_path, "flipt_minimal_luna1", "software_engineering")
    recs = records_by_run(tmp_path)[0]
    assert recs["binder_minimal_luna1"]["export_flags_ok"] is False and recs["flipt_minimal_luna1"]["export_flags_ok"]
    assert (recs["flipt_minimal_luna1"]["row"], recs["binder_minimal_luna1"]["row"]) == ("flipt-02e2", "alfalfa_chi")


def test_an_earlier_attempt_is_listed_as_superseded_and_never_counts_as_a_second_run(tmp_path):
    """attempts/<name>.<n> takes its run's name and arm, is invalid with reason superseded in every study, and the
    check for a name found twice skips it. Its capture under attempts/ credits its instance."""
    name = "flipt_minimal_luna_fixed1"
    current = make_run(tmp_path, name, "software_engineering", commit="94940bc")
    attempt = make_run(tmp_path, name, "software_engineering", commit="94940bc", group="attempts",
                       folder_name=f"{name}.1", status="error")
    register(tmp_path, {4001: "fixed-gate-flipt-luna-1"})
    capture(tmp_path, 4001, [current, attempt])
    recs, notes = records_by_run(tmp_path)
    earlier = recs[f"attempts/{name}.1"]
    assert (earlier["run"], earlier["group"], earlier["valid"], earlier["reason"], earlier["stage"]) == (
        name, "attempts", False, "superseded", "fixed-gate")
    assert (earlier["instance_id"], earlier["instance_source"]) == (4001, "manifest")
    assert recs[name]["valid"] and notes["names_twice"] == []
    other = tmp_path / "other"
    make_run(other, "binder_solo_luna1", "protein_design", study="study_exploration_0", group="attempts",
             folder_name="binder_solo_luna1.2")
    [rec] = build(other / "runs" / "study_exploration_0", join_for(other / "runs" / "study_exploration_0"))[0]
    assert (rec["run"], rec["valid"], rec["reason"]) == ("binder_solo_luna1", False, "superseded")


def test_write_refuses_a_run_a_group_and_a_folder_outside_runs(tmp_path, capsys):
    """A mistyped folder never gets an INDEX.jsonl: one that holds a run.json, a group, and one whose parent is not a
    runs/ folder. The summary alone reads any folder."""
    run = make_run(tmp_path, "binder_minimal_luna1", "protein_design")
    make_run(tmp_path, "binder_minimal_lunamock1", "protein_design", group="mock")
    elsewhere = tmp_path / "copy" / STUDY
    elsewhere.mkdir(parents=True)
    for folder, refusal in ((run, "holds a run.json"), (run.parent / "mock", "is a group of a study"),
                            (elsewhere, "not a study's folder")):
        with pytest.raises(SystemExit) as refused:
            main([str(folder), "--write"])
        assert refused.value.code == 2 and refusal in capsys.readouterr().err
        assert not (folder / INDEX).exists()
        assert main([str(folder)]) == 0
    with pytest.raises(ValueError, match="holds a run.json"):
        run_index.write(run, [])


def test_no_module_under_src_imports_scripts():
    """src/ is the run code and scripts/ drives it: a module of src never imports one of scripts."""
    importers = []
    for path in sorted((settings.ROOT / "src").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            modules = [alias.name for alias in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            importers += [f"{path.relative_to(settings.ROOT)}: {module}" for module in modules
                          if module == "scripts" or module.startswith("scripts.")]
    assert importers == []


def test_an_swe_run_counts_only_on_the_fixed_task(tmp_path):
    make_run(tmp_path, "flipt_minimal_luna1", "software_engineering", commit="0dda147")
    make_run(tmp_path, "flipt_minimal_luna_fixed1", "software_engineering", commit="94940bc")
    recs = records_by_run(tmp_path)[0]
    assert (recs["flipt_minimal_luna1"]["valid"], recs["flipt_minimal_luna1"]["reason"]) == (False, "swe_before_fix")
    assert (recs["flipt_minimal_luna_fixed1"]["valid"], recs["flipt_minimal_luna_fixed1"]["tag"]) == (True, "fixed")


def test_a_run_in_a_group_is_indexed_with_its_group_and_its_mirrored_log(tmp_path):
    make_run(tmp_path, "binder_minimal_cheat_lunamock1", "protein_design", group="mock")
    make_run(tmp_path, "binder_minimal_luna2", "protein_design", seed=2, group="incomplete", status="error")
    recs = records_by_run(tmp_path)[0]
    mock, broken = recs["mock/binder_minimal_cheat_lunamock1"], recs["incomplete/binder_minimal_luna2"]
    assert (mock["path"], mock["group"], mock["eval_status"], mock["mock"], mock["reason"]) == (
        "mock/binder_minimal_cheat_lunamock1", "mock", "success", True, "mock")
    assert mock["stage"] == "gate" and mock["inspect_model"] == "mockllm/model"
    assert (broken["path"], broken["eval_status"], broken["completed"], broken["reason"]) == (
        "incomplete/binder_minimal_luna2", "error", False, "incomplete")


def test_the_notes_name_what_the_table_lacks_where_it_disagrees_and_every_odd_folder(tmp_path):
    make_run(tmp_path, "binder_minimal_luna9", "protein_design", seed=9)              # the table's seeds stop at 5
    make_run(tmp_path, "binder_minimal_luna2", "protein_design", seed=3)              # the table says seed 2
    make_run(tmp_path, "binder_minimal_luna3", "protein_design", seed=3, model=DSFLASH)
    make_run(tmp_path, "binder_minimal_luna4", "protein_design", seed=4)
    make_run(tmp_path, "binder_minimal_luna4", "protein_design", seed=4, group="set_aside")
    (tmp_path / "runs" / STUDY / "smoke" / "scenario").mkdir(parents=True)
    recs, notes = records_by_run(tmp_path)
    assert notes["not_in_table"] == ["binder_minimal_luna9"] and recs["binder_minimal_luna9"]["stage"] is None
    assert notes["table_disagrees"] == [("binder_minimal_luna2", "seed: table 2, run.json 3"),
                                        ("binder_minimal_luna3", f"model: table {LUNA}, run.json {resolve(DSFLASH)}")]
    assert notes["names_twice"] == ["binder_minimal_luna4"] and notes["other_folders"] == ["smoke"]


def test_another_study_takes_its_cell_from_the_name_and_only_the_rules_of_every_study(tmp_path):
    study = "study_exploration_0"
    make_run(tmp_path, "binder_minimal_dsflash1", "protein_design", study=study, model=DSFLASH, status="error")
    make_run(tmp_path, "flipt_solo_luna2", "software_engineering", study=study, seed=2, commit="4c04d18")
    make_run(tmp_path, "poem_solo_dsflashcheckmock1", "creative_writings", study=study, model=DSFLASH)
    recs = records_by_run(tmp_path, study)[0]
    binder, flipt, poem = recs["binder_minimal_dsflash1"], recs["flipt_solo_luna2"], recs["poem_solo_dsflashcheckmock1"]
    assert (binder["cell"], binder["kind"], binder["worker"], binder["stage"], binder["instance_id"]) == (
        "protein_design.binder_minimal", "minimal", "dsflash", None, None)
    assert (binder["valid"], binder["why"], binder["reason"], binder["eval_status"]) == (None, None, None, "error")
    assert (flipt["cell"], flipt["valid"], flipt["reason"]) == ("swe.flipt_solo", False, "swe_before_fix")
    assert flipt["why"] == ["SWE task before the fix: one agent could cheat alone"]
    assert (poem["cell"], poem["worker"], poem["mock"], poem["valid"], poem["reason"]) == (
        "creative_writing.poem_solo", "dsflash", True, False, "mock")


def test_the_command_writes_nothing_without_write_and_one_record_per_run_with_it(tmp_path, capsys):
    make_run(tmp_path, "binder_minimal_luna1", "protein_design")
    make_run(tmp_path, "flipt_minimal_luna1", "software_engineering")
    make_run(tmp_path, "binder_minimal_cheat_lunamock1", "protein_design", group="mock")
    study = tmp_path / "runs" / STUDY
    assert main([str(study)]) == 0
    out = capsys.readouterr().out
    assert f"== {study}: 3 runs: 1 valid, 1 not, 1 mock\n" in out and "== by stage: gate 3" in out
    assert not (study / INDEX).exists() and sorted(p.name for p in study.iterdir()) == [
        "binder_minimal_luna1", "flipt_minimal_luna1", "mock"]
    assert main([str(study), "--write"]) == 0
    lines = (study / INDEX).read_text().splitlines()
    assert [json.loads(line) for line in lines] == build(study, join_for(study))[0]
    assert all(list(json.loads(line)) == list(FIELDS) for line in lines) and len(lines) == 3
    assert not (study / (INDEX + ".partial")).exists()


def test_the_command_finds_the_box_registry_from_a_relative_folder(tmp_path, monkeypatch, capsys):
    make_run(tmp_path, "flipt_minimal_luna_fixed1", "software_engineering", commit="94940bc")
    register(tmp_path, {1004: "fixed-gate-flipt-luna-1"})
    monkeypatch.chdir(tmp_path / "runs" / STUDY)
    assert main(["."]) == 0
    assert "== by instance source: registry 1\n" in capsys.readouterr().out


def test_a_name_the_table_gives_two_places_is_refused(monkeypatch):
    from scripts.box import study_table

    gate = study_table.STAGES["gate"]
    stem = next(iter(gate))
    monkeypatch.setitem(study_table.STAGES, "copy", {stem.replace("gate", "copy"): gate[stem]})
    with pytest.raises(ValueError, match="two places"):
        study_index.study_table_arms()
