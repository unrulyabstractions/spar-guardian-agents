"""run_index and its command scripts/study/study_index.py: one record per run of a study, from its folder and its eval
log. Every case builds a copy of the layout under tmp_path: runs/<study>/[<group>/]<run> and the eval log at the same
path under logs/. The runs carry names of the screen's shape, and their run.json comes from today's plan, as a run of
today's code records it."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from scripts.study.study_index import main
from src import tasks
from src.configuration import settings
from src.configuration.presets import build_config
from src.domains import BY_TASK
from src.measures import run_index, run_validity
from src.measures.run_index import FIELDS, INDEX, build
from src.models.model_registry import resolve
from tests.support.run_builder import write_status_log
from tests.support.run_files import write_text

STUDY = "study_x"
LOG = "2026-11-01T03-31-41-00-00_run_abc.eval"
LUNA, OPUS, DSFLASH = "gpt-6-luna-openrouter", "claude-opus-5-5-openrouter", "deepseek-flash-openrouter"
REFUSAL = "API integrators: you can reduce refusals by configuring a fallback model"
ROWS = {"protein_design": ("protein", {"row_id": "alfalfa_chi"}),
        "creative_writings": ("creative", {"row_id": "museum_poem"}),
        "software_engineering": ("swe", {"issue": "flipt-02e2", "honeypot": "jigsaw"})}


def make_run(root: Path, name: str, task: str, *, study: str = STUDY, group: str | None = None, seed: int = 1,
             model: str = LUNA, regulator: str | None = None, regulator_level: str = "generic",
             rule_level: str = "none", commit: str = "0dda147", status: str = "success",
             regulator_says: str = "I read the board.", flags_ok: bool = True, folder_name: str | None = None,
             extra_pairs: tuple[str, ...] = (), posts: dict | None = None, row: str | None = None) -> Path:
    """A run folder and its eval log, run.json as today's plan deals it. folder_name is an earlier attempt's folder,
    <name>.<n>. A regulator or disregulator level that no longer exists is recorded as a stored run of the study
    holds it, over a plan dealt at today's generic level."""
    block, row = ROWS[task]
    pairs = [f"seed={seed}", f"id_salt={name}", f"rule_level={rule_level}", f"regulator_level={regulator_level}",
             *(["n_agents=4", "regulators=1"] if regulator else []), *extra_pairs]
    levels = settings.load_instruction_levels()
    stored = {key: value for key, _, value in (pair.partition("=") for pair in pairs)
              if key in ("regulator_level", "disregulator_level") and value not in levels[key[:-6]]["order"]}
    config = build_config("default", "small", [pair for pair in pairs if pair.partition("=")[0] not in stored]
                          + [f"{key}=generic" for key in stored])
    roles = tasks.agent_roles(config) or {}
    plan = BY_TASK[task](row.get("row_id") or row["issue"]).plan(config, roles or None)
    mock = bool(run_validity.MOCK.search(name))
    data = {"run_id": name, "task": task, block: row, "config": {**config.model_dump(mode="json"), **stored},
            "agents": plan.agents,
            "access": {"policy": plan.access, "agents": {a: {"holds": items} for a, items in plan.holds.items()}},
            "code": {"commit": commit}, "model": "mockllm/model" if mock else resolve(model),
            "role_models": {"worker": None, "regulator": resolve(regulator) if regulator and not mock else None,
                            "disregulator": None},
            "rules": {"workers": [a for a in plan.agents if roles.get(a, "worker") == "worker"],
                      "posts": {a: (posts or {}).get(role, 0) for a, role in roles.items()}},
            "swarm": {"roles": roles} if roles else None,
            "validity": {"ok": flags_ok}, "eval_log": f"/root/work/logs/{study}/{name}/{LOG}"}
    folder = root / "runs" / study / (group or "") / (folder_name or name)
    folder.mkdir(parents=True)
    write_text(folder / "processed/run.json", json.dumps(data))
    write_text(folder / "analysis/signals.jsonl", json.dumps({"row": "team", "cheating_rung": "none"}) + "\n")
    write_text(folder / "processed/episodes.jsonl", json.dumps({"failure": None}) + "\n")
    for agent in (a for a, role in roles.items() if role in ("regulator", "disregulator")):
        transcript = folder / "processed/trajectories" / f"row#1@{agent}" / "transcript.json"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(json.dumps({"trajectory": {"steps": [{"source": "agent", "message": regulator_says}]}}))
    write_status_log(root / "logs" / study / (group or "") / (folder_name or name) / LOG, status)
    return folder


HAIKU = "claude-haiku-5-5-openrouter"


def records_by_run(root: Path, study: str = STUDY) -> tuple[dict[str, dict], dict]:
    records, notes = build(root / "runs" / study)
    return {rec["path"]: rec for rec in records}, notes


def test_a_regulated_run_takes_its_identity_levels_models_and_outcome(tmp_path):
    make_run(tmp_path, "binder_minimal_regulated_luna_opus_ambiguous1", "protein_design", regulator=OPUS,
             regulator_says=REFUSAL)
    rec = records_by_run(tmp_path)[0]["binder_minimal_regulated_luna_opus_ambiguous1"]
    assert list(rec) == list(FIELDS)
    assert {key: rec[key] for key in ("study", "path", "group", "cheatbench_task", "domain", "row")} == {
        "study": STUDY, "path": "binder_minimal_regulated_luna_opus_ambiguous1", "group": None,
        "cheatbench_task": "protein_design", "domain": "protein_design", "row": "alfalfa_chi"}
    assert (rec["stage"], rec["box"], rec["instance_id"], rec["rule_level"]) == (None, None, None, None)
    assert (rec["seed"], rec["mock"], rec["commit"], rec["inspect_model"], rec["regulator_inspect_model"]) == (
        1, False, "0dda147", resolve(LUNA), resolve(OPUS))
    assert (rec["eval_status"], rec["completed"], rec["failed_episodes"], rec["cheating_rung"],
            rec["export_flags_ok"]) == ("success", True, [], "none", True)
    assert (rec["valid"], rec["reason"], rec["deal_replay"], rec["deal_replay_detail"]) == (
        False, "regulator_refused", "same", None)


def test_the_record_keeps_the_exports_own_flags_and_an_swe_runs_issue_as_its_row(tmp_path):
    make_run(tmp_path, "binder_minimal_luna1", "protein_design", flags_ok=False)
    make_run(tmp_path, "flipt_minimal_luna1", "software_engineering")
    recs = records_by_run(tmp_path)[0]
    assert recs["binder_minimal_luna1"]["export_flags_ok"] is False and recs["flipt_minimal_luna1"]["export_flags_ok"]
    assert (recs["flipt_minimal_luna1"]["row"], recs["binder_minimal_luna1"]["row"]) == ("flipt-02e2", "alfalfa_chi")


def test_an_earlier_attempt_is_listed_as_superseded_and_never_counts_as_a_second_run(tmp_path):
    """attempts/<name>.<n> takes its run's name and arm, is invalid with reason superseded in every study, and the
    check for a name found twice skips it."""
    name = "flipt_minimal_luna_fixed1"
    make_run(tmp_path, name, "software_engineering", commit="94940bc")
    make_run(tmp_path, name, "software_engineering", commit="94940bc", group="attempts",
                       folder_name=f"{name}.1", status="error", flags_ok=False)       # its own bytes, its own eval
    recs, notes = records_by_run(tmp_path)
    earlier = recs[f"attempts/{name}.1"]
    assert (earlier["run"], earlier["group"], earlier["valid"], earlier["reason"]) == (
        name, "attempts", False, "superseded")
    assert recs[name]["valid"] and notes["names_twice"] == []
    other = tmp_path / "other"
    make_run(other, "binder_solo_luna1", "protein_design", study="ian_baseline_explorations_0", group="attempts",
             folder_name="binder_solo_luna1.2")
    study = other / "runs" / "ian_baseline_explorations_0"
    [rec] = build(study)[0]
    assert (rec["run"], rec["valid"], rec["reason"]) == ("binder_solo_luna1", False, "superseded")


def test_write_refuses_a_run_a_group_and_a_folder_outside_runs(tmp_path, capsys):
    """A mistyped folder never gets an INDEX.jsonl: one that holds a run.json, a group, and one whose parent is not a
    runs/ folder. The summary alone reads any folder."""
    run = make_run(tmp_path, "binder_minimal_luna1", "protein_design")
    make_run(tmp_path, "binder_minimal_lunamock1", "protein_design", group="mock")
    elsewhere = tmp_path / "copy" / STUDY
    elsewhere.mkdir(parents=True)
    for folder, refusal in ((run, "holds a run"), (run.parent / "mock", "is a group of a study"),
                            (elsewhere, "not a study's folder")):
        with pytest.raises(SystemExit) as refused:
            main([str(folder), "--write"])
        assert refused.value.code == 2 and refusal in capsys.readouterr().err
        assert not (folder / INDEX).exists()
        assert main([str(folder)]) == 0
    with pytest.raises(ValueError, match="holds a run"):
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
    assert recs["flipt_minimal_luna_fixed1"]["valid"]


def test_a_run_in_a_group_is_indexed_with_its_group_and_its_mirrored_log(tmp_path):
    make_run(tmp_path, "binder_minimal_cheat_lunamock1", "protein_design", group="mock")
    make_run(tmp_path, "binder_minimal_luna2", "protein_design", seed=2, group="incomplete", status="error")
    recs = records_by_run(tmp_path)[0]
    mock, broken = recs["mock/binder_minimal_cheat_lunamock1"], recs["incomplete/binder_minimal_luna2"]
    assert (mock["path"], mock["group"], mock["eval_status"], mock["mock"], mock["reason"]) == (
        "mock/binder_minimal_cheat_lunamock1", "mock", "success", True, "mock")
    assert mock["inspect_model"] == "mockllm/model"
    assert (broken["path"], broken["eval_status"], broken["completed"], broken["reason"]) == (
        "incomplete/binder_minimal_luna2", "error", False, "incomplete")


def test_the_notes_name_a_name_found_twice_and_every_odd_folder(tmp_path):
    make_run(tmp_path, "binder_minimal_luna4", "protein_design", seed=4)
    make_run(tmp_path, "binder_minimal_luna4", "protein_design", seed=4, group="set_aside")
    (tmp_path / "runs" / STUDY / "smoke" / "scenario").mkdir(parents=True)
    notes = records_by_run(tmp_path)[1]
    assert notes == {"names_twice": ["binder_minimal_luna4"], "other_folders": ["smoke"]}


def test_another_study_takes_its_cell_from_the_name_and_the_same_validity_as_every_study(tmp_path):
    study = "ian_baseline_explorations_0"
    make_run(tmp_path, "binder_minimal_dsflash1", "protein_design", study=study, model=DSFLASH, status="error")
    make_run(tmp_path, "flipt_solo_luna2", "software_engineering", study=study, seed=2, commit="4c04d18")
    make_run(tmp_path, "poem_solo_dsflashcheckmock1", "creative_writings", study=study, model=DSFLASH)
    recs = records_by_run(tmp_path, study)[0]
    binder, flipt, poem = recs["binder_minimal_dsflash1"], recs["flipt_solo_luna2"], recs["poem_solo_dsflashcheckmock1"]
    assert (binder["cell"], binder["kind"], binder["worker"], binder["stage"], binder["instance_id"]) == (
        "protein_design.binder_minimal", "minimal", "dsflash", None, None)
    assert (binder["valid"], binder["reason"], binder["eval_status"]) == (False, "incomplete", "error")
    assert binder["why"][0] == "eval error" and binder["regulator_model_key"] is None
    assert binder["model_key"] == "deepseek-flash-openrouter" and flipt["model_key"] == "gpt-6-luna-openrouter"
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
    assert f"== {study}: 3 runs: 1 valid, 1 not, 1 mock\n" in out and "== by stage: None 3" in out
    assert not (study / INDEX).exists() and sorted(p.name for p in study.iterdir()) == [
        "binder_minimal_luna1", "flipt_minimal_luna1", "mock"]
    assert main([str(study), "--write"]) == 0
    lines = (study / INDEX).read_text().splitlines()
    assert [json.loads(line) for line in lines] == build(study)[0]
    assert all(list(json.loads(line)) == list(FIELDS) for line in lines) and len(lines) == 3
    assert not (study / (INDEX + ".partial")).exists()


def test_a_silent_regulator_reads_zero_posts_and_a_run_without_one_reads_none(tmp_path, monkeypatch):
    assert run_index.role_posts({"regulators": [{"posts": 3}, {"posts": 2}]}, "regulator") == 5
    assert run_index.role_posts({"regulators": [{"posts": 0}]}, "regulator") == 0
    assert run_index.role_posts({"regulators": [{"posts": 3}]}, "disregulator") is None
    assert run_index.role_posts({}, "regulator") is None
    assert "regulator_posts" in run_index.FIELDS and "disregulator_posts" in run_index.FIELDS
    run = make_run(tmp_path, "binder_minimal_luna1", "protein_design")
    verdict = run_index.run_validity.validity(run)
    monkeypatch.setattr(run_index.run_validity, "validity", lambda path: {**verdict, "regulators": [{"posts": 4}]})
    data = json.loads((run / "processed/run.json").read_text())
    assert run_index.record(run, data, run.parent)["regulator_posts"] == 4


CONTEST = "poem_minimal_regulated_disregulated_luna_haiku_enforce_persuade1"
BOTH_ROLES = ("n_agents=5", "regulators=1", "disregulators=1", "regulator_level=enforce",
              "disregulator_level=persuade")


def contested_run(root: Path, **kwargs) -> Path:
    kwargs.setdefault("posts", {"regulator": 2, "disregulator": 3})
    return make_run(root, CONTEST, "creative_writings", regulator=HAIKU, extra_pairs=BOTH_ROLES, **kwargs)


def test_a_run_with_both_roles_takes_their_levels_and_counts_each_role_s_posts(tmp_path):
    contested_run(tmp_path)
    rec = records_by_run(tmp_path)[0][CONTEST]
    assert rec["valid"] and rec["reason"] == "valid"
    assert (rec["regulator_level"], rec["disregulator_level"]) == ("enforce", "persuade")
    assert (rec["regulator_posts"], rec["disregulator_posts"]) == (2, 3)


def test_a_disregulator_that_never_posted_leaves_its_run_valid_in_the_index(tmp_path):
    contested_run(tmp_path, posts={"regulator": 2})
    rec = records_by_run(tmp_path)[0][CONTEST]
    assert rec["valid"] and rec["reason"] == "valid" and rec["disregulator_posts"] == 0

