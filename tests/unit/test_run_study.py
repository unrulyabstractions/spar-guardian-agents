"""scripts/run_study.py: a plan's cells, their commands in the pilot's order, and a launch that needs --yes. Nothing
launches: no Docker, no model call."""
from __future__ import annotations

import subprocess

import pytest

from scripts import run_study as seed_study
from src.configuration import settings

PILOT = settings.ROOT / "studies" / "ian_seed_prototyping_0.yaml"


def test_the_pilots_stage_1_is_22_cells_in_its_order():
    plan = seed_study.load_plan(PILOT)
    names = [c.name for c in seed_study.cells(plan)]
    assert len(names) == 22 and names[0] == "protein_luna_none_s1"
    assert names[-1] == "creative_luna_exp0_pooled_and_passed_s1"
    assert len(set(names)) == 22


def test_a_cell_runs_the_pilots_command():
    """launch.sh of the pilot: the model, no judge, the name, the seed, the five settings, the salt, then the pack."""
    cell = next(c for c in seed_study.cells(seed_study.load_plan(PILOT)) if c.name == "swe_deepseek_exp0_pressure_s1")
    assert seed_study.command(cell, "study_x", [], False) == (
        "STUDY=study_x exec uv run python -m examples.swe.flipt_minimal --model deepseek-flash-openrouter --no-judge "
        "--name swe_deepseek_exp0_pressure_s1 --set seed=1 --set rounds=30 --set calls_per_round=10 "
        "--set round_time_s=600 --set time_limit_s=18000 --set cost_limit_usd=5 --set id_salt=swe_deepseek_s1 "
        "--set seed_pack=swe/exp0_pressure")
    control = next(c for c in seed_study.cells(seed_study.load_plan(PILOT)) if c.name == "swe_deepseek_none_s1")
    assert "seed_pack" not in seed_study.command(control, "s", [], False)
    assert "--replace" in control.argv([], True)


def test_a_bad_plan_is_refused(tmp_path):
    bad = tmp_path / "plan.yaml"
    bad.write_text("study: s\nseeds: [1]\ntargets:\n  t:\n    module: examples.nothing\n    model: m\n"
                   "    packs: [none]\n")
    with pytest.raises(ValueError, match="no script"):
        seed_study.load_plan(bad)
    bad.write_text("study: s\nseeds: [1]\ntargets:\n  t:\n    module: examples.swe.flipt_minimal\n    model: m\n"
                   "    packs: [swe/exp0_pressure, protein_design/exp0_pressure]\n")
    with pytest.raises(ValueError, match="several tasks"):
        seed_study.load_plan(bad)
    bad.write_text("study: s\nseeds: [1]\ntargets:\n  t:\n    module: examples.swe.flipt_minimal\n    model: m\n"
                   "    packs: [swe/nothing]\n")
    with pytest.raises(ValueError, match="unknown seed pack"):
        seed_study.load_plan(bad)


def no_launch(*args, **kwargs):
    raise AssertionError("nothing may launch")


def test_a_dry_run_and_a_run_without_yes_launch_nothing(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "Popen", no_launch)
    monkeypatch.setattr(seed_study, "free_memory_pct", lambda: 70)
    monkeypatch.setattr(seed_study, "free_disk_gb", lambda: 20)
    assert seed_study.main([str(PILOT), "--dry-run", "--study", "no_such_study"]) == 0
    out = capsys.readouterr().out
    assert "22 to run; at most $110.00" in out and out.count("  plan   ") == 22
    assert seed_study.main([str(PILOT), "--study", "no_such_study"]) == 2


def test_the_scheduler_waits_for_memory_disk_and_the_limits(monkeypatch):
    cell = next(c for c in seed_study.cells(seed_study.load_plan(PILOT)) if c.task == "swe")
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
    plan = seed_study.load_plan(settings.ROOT / "studies" / "ian_seed_prototyping_2.yaml")
    by_name = {c.name: c for c in seed_study.cells(plan)}
    assert by_name["ian0flipt_luna_baseline_none_s1"].task == "swe"
    assert by_name["ian0poem_haiku_baseline_none_s1"].task == "creative_writing"
    assert by_name["ian0binder_deepseek_baseline_none_s1"].task == "protein_design"
    assert by_name["ian0flipt_luna_seeded_exp1_called_cheating_s1"].task == "swe"
    assert seed_study.module_task("some.other.module.path") is None
