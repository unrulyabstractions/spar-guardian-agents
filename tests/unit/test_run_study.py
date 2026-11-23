"""scripts/run_study.py: a plan's cells, their commands in the pilot's order, and a launch that needs --yes. Nothing
launches: no Docker, no model call."""
from __future__ import annotations

import subprocess

import pytest

from scripts import run_study as seed_study
from src.configuration import settings

PLANS = settings.ROOT / "studies" / "plans"
PACK = "swe/exp1_called_cheating"
PLAN = f"""study: study_x
seeds: [1, 2]
targets:
  swe_deepseek:
    module: examples.swe.flipt_minimal
    model: deepseek-flash-openrouter
    packs: [none, {PACK}]
  swe_luna:
    module: studies.cells.swe.ian0flipt_minimal
    model: gpt-6-luna-openrouter
    packs: [none, {PACK}]
limits: {{max_runs: 10, max_per_task: {{swe: 4}}, spacing_s: 60}}
"""
"""A small plan: two targets, each with the packs none and swe/exp1_called_cheating, and two seeds. 8 cells."""


@pytest.fixture
def plan_file(tmp_path):
    path = tmp_path / "plan.yaml"
    path.write_text(PLAN)
    return path


def test_a_plans_cells_go_seed_by_seed_target_by_target_pack_by_pack(plan_file):
    names = [c.name for c in seed_study.cells(seed_study.load_plan(plan_file))]
    assert names == [f"{target}_{pack}_s{seed}" for seed in (1, 2) for target in ("swe_deepseek", "swe_luna")
                     for pack in ("none", "exp1_called_cheating")]
    assert len(set(names)) == 8


def test_a_cell_runs_the_pilots_command(plan_file):
    """launch.sh of the pilot: the model, no judge, the name, the seed, the five settings, the salt, then the pack."""
    plan = seed_study.load_plan(plan_file)
    cell = next(c for c in seed_study.cells(plan) if c.name == "swe_deepseek_exp1_called_cheating_s1")
    assert seed_study.command(cell, "study_x", [], False) == (
        "STUDY=study_x exec uv run python -m examples.swe.flipt_minimal --model deepseek-flash-openrouter --no-judge "
        "--name swe_deepseek_exp1_called_cheating_s1 --set seed=1 --set rounds=30 --set calls_per_round=10 "
        "--set round_time_s=600 --set time_limit_s=18000 --set cost_limit_usd=5 --set id_salt=swe_deepseek_s1 "
        "--set seed_pack=swe/exp1_called_cheating")
    control = next(c for c in seed_study.cells(plan) if c.name == "swe_deepseek_none_s1")
    assert "seed_pack" not in seed_study.command(control, "s", [], False)
    assert "--replace" in control.argv([], True)


def test_a_bad_plan_is_refused(tmp_path):
    bad = tmp_path / "plan.yaml"
    bad.write_text("study: s\nseeds: [1]\ntargets:\n  t:\n    module: examples.nothing\n    model: m\n"
                   "    packs: [none]\n")
    with pytest.raises(ValueError, match="no script"):
        seed_study.load_plan(bad)
    bad.write_text("study: s\nseeds: [1]\ntargets:\n  t:\n    module: examples.swe.flipt_minimal\n    model: m\n"
                   "    packs: [swe/exp1_called_cheating, protein_design/exp1_called_cheating]\n")
    with pytest.raises(ValueError, match="several tasks"):
        seed_study.load_plan(bad)
    bad.write_text("study: s\nseeds: [1]\ntargets:\n  t:\n    module: examples.swe.flipt_minimal\n    model: m\n"
                   "    packs: [swe/nothing]\n")
    with pytest.raises(ValueError, match="unknown seed pack"):
        seed_study.load_plan(bad)


def no_launch(*args, **kwargs):
    raise AssertionError("nothing may launch")


def test_a_dry_run_and_a_run_without_yes_launch_nothing(plan_file, monkeypatch, capsys):
    """A dry run lists every cell and launches nothing; a run without --yes refuses, and so does a closed plan, even
    with --yes."""
    monkeypatch.setattr(subprocess, "Popen", no_launch)
    monkeypatch.setattr(seed_study, "free_memory_pct", lambda: 70)
    monkeypatch.setattr(seed_study, "free_disk_gb", lambda: 20)
    assert seed_study.main([str(plan_file), "--dry-run", "--study", "no_such_study"]) == 0
    out = capsys.readouterr().out
    assert "8 to run; at most $40.00" in out and out.count("  plan   ") == 8
    assert seed_study.main([str(plan_file), "--study", "no_such_study"]) == 2
    assert "add --yes" in capsys.readouterr().err
    plan_file.write_text(PLAN + "closed: true\n")
    assert seed_study.main([str(plan_file), "--study", "no_such_study", "--yes"]) == 2
    assert "this study is closed" in capsys.readouterr().err


def test_the_scheduler_waits_for_memory_disk_and_the_limits(plan_file, monkeypatch):
    cell = next(c for c in seed_study.cells(seed_study.load_plan(plan_file)) if c.task == "swe")
    limits = {"max_runs": 10, "max_per_task": {"swe": 1}, "spacing_s": 0}
    monkeypatch.setattr(seed_study, "free_disk_gb", lambda: 20)
    monkeypatch.setattr(seed_study, "free_memory_pct", lambda: 39)
    assert seed_study.room({}, cell, limits, 0.0) == "memory 39% free"
    monkeypatch.setattr(seed_study, "free_memory_pct", lambda: 40)
    assert seed_study.room({}, cell, limits, 0.0) is None
    assert seed_study.room({"x": (None, cell)}, cell, limits, 0.0) == "1 swe runs running"
    monkeypatch.setattr(seed_study, "free_disk_gb", lambda: 9)
    assert seed_study.room({}, cell, limits, 0.0) == "docker disk 9 GB free"


def test_a_cell_without_a_pack_counts_toward_its_task_limit():
    """ian_seed_prototyping_2's baselines name no pack; each still counts as a run of its module's task."""
    plan = seed_study.load_plan(PLANS / "ian_seed_prototyping_2.yaml")
    by_name = {c.name: c for c in seed_study.cells(plan)}
    assert by_name["ian0flipt_luna_baseline_none_s1"].task == "swe"
    assert by_name["ian0poem_haiku_baseline_none_s1"].task == "creative_writing"
    assert by_name["ian0binder_deepseek_baseline_none_s1"].task == "protein_design"
    assert by_name["ian0flipt_luna_seeded_exp1_called_cheating_s1"].task == "swe"
    assert {c.module.split(".")[0] for c in seed_study.cells(plan)} == {"studies"}
    assert seed_study.module_task("some.other.module.path") is None


@pytest.mark.parametrize("module,task", [
    ("examples.swe.flipt_minimal", "swe"),
    ("studies.cells.swe.ian0flipt_minimal", "swe"),
    ("studies.cells.creative_writing.ian0poem_minimal_cheat", "creative_writing"),
    ("studies.cells.protein_design.ian0binder_minimal", "protein_design"),
    ("studies.swe.ian0flipt_minimal", None),
    ("studies.cells.swe", None),
])
def test_a_modules_task_is_its_folder_under_examples_or_studies_cells(module, task):
    assert seed_study.module_task(module) == task


def test_every_plan_in_studies_loads_and_names_scripts_of_this_checkout():
    """Every plan under studies/plans/ loads: its modules are scripts of this checkout and its packs exist."""
    plans = sorted(PLANS.glob("*.yaml"))
    assert plans
    for plan in plans:
        cells = seed_study.cells(seed_study.load_plan(plan))
        assert cells and all(c.task for c in cells), plan.name
