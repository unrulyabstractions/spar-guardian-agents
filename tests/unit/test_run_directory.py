"""Where a study keeps its runs (src/measures/run_directory.py): runs at its top level and in its groups, and each run's
eval log at the same path under logs/. Every case builds the folders under tmp_path."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.configuration import settings
from src.measures.run_directory import GROUPS, RunDir, mirrored_in_logs, run_place, study_runs

LOG = "2026-11-01T03-31-41-00-00_x_abc.eval"


def make_run(folder: Path, recorded: str = f"/root/work/logs/study_x/{{name}}/{LOG}") -> Path:
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(json.dumps({"run_id": folder.name, "eval_log": recorded.format(name=folder.name)}))
    return folder


def make_log(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / LOG).write_text("log")
    return folder / LOG


@pytest.fixture
def elsewhere(tmp_path, monkeypatch):
    """This checkout's paths point at an empty folder, so only the run's own place can find its log."""
    monkeypatch.setattr(settings, "ROOT", tmp_path / "checkout")
    monkeypatch.setattr(settings, "LOGS", tmp_path / "checkout" / "logs" / "study_x")


def test_a_study_lists_its_runs_at_the_top_level_and_in_every_group(tmp_path):
    study = tmp_path / "runs" / "study_x"
    for path in ("b_luna1", "a_luna2", "mock/a_lunamock1", "invalid/c_luna1", "incomplete/d_luna1", "set_aside/e1"):
        make_run(study / path)
    (study / "smoke" / "scenario").mkdir(parents=True)
    (study / "smoke" / "scenario" / "run.json").write_text("{}")      # a smoke suite's folder is no group
    (study / "notes").mkdir()
    (study / "INDEX.jsonl").write_text("")
    assert [p.relative_to(study).as_posix() for p in study_runs(study)] == [
        "a_luna2", "mock/a_lunamock1", "b_luna1", "invalid/c_luna1", "incomplete/d_luna1", "set_aside/e1"]
    assert set(GROUPS) == {"invalid", "incomplete", "mock", "set_aside"}


def test_a_run_knows_its_study_and_its_group(tmp_path):
    assert run_place(tmp_path / "runs" / "study_x" / "a_luna1") == ("study_x", None)
    assert run_place(tmp_path / "runs" / "study_x" / "mock" / "a_lunamock1") == ("study_x", "mock")


def test_the_log_folder_mirrors_the_run_folder(tmp_path):
    runs, logs = tmp_path / "runs" / "study_x", tmp_path / "logs" / "study_x"
    assert mirrored_in_logs(runs / "mock" / "a1") == logs / "mock" / "a1"
    assert mirrored_in_logs(runs / "a1") == logs / "a1"
    assert mirrored_in_logs(tmp_path / "study_x" / "a1") is None


def test_a_run_in_a_group_finds_its_log_at_the_mirrored_path(tmp_path, elsewhere):
    run = make_run(tmp_path / "runs" / "study_x" / "invalid" / "c_luna1")
    assert RunDir(run).eval_log is None
    log = make_log(tmp_path / "logs" / "study_x" / "invalid" / "c_luna1")
    assert RunDir(run).eval_log == log


def test_a_run_moved_into_a_group_before_its_log_still_finds_it(tmp_path, elsewhere):
    run = make_run(tmp_path / "runs" / "study_x" / "mock" / "a_lunamock1")
    log = make_log(tmp_path / "logs" / "study_x" / "a_lunamock1")
    assert RunDir(run).eval_log == log


def test_a_run_at_the_top_level_of_another_checkout_finds_its_log_beside_it(tmp_path, elsewhere):
    run = make_run(tmp_path / "runs" / "study_x" / "a_luna1")
    log = make_log(tmp_path / "logs" / "study_x" / "a_luna1")
    assert RunDir(run).eval_log == log


def test_a_top_level_run_finds_its_log_as_before(tmp_path, monkeypatch):
    """The recorded path first; then this checkout's logs/<study>/<run>/, also for a run outside any runs/ folder; then
    the current study's; then the run's name beside the recorded folder."""
    monkeypatch.setattr(settings, "ROOT", tmp_path)
    monkeypatch.setattr(settings, "LOGS", tmp_path / "logs" / "study_now")
    recorded = make_log(tmp_path / "box" / "logs" / "study_x" / "old_name")
    run = make_run(tmp_path / "study_x" / "a_luna1", recorded=str(recorded))
    assert RunDir(run).eval_log == recorded
    recorded.unlink()
    assert RunDir(run).eval_log is None
    beside = make_log(tmp_path / "box" / "logs" / "study_x" / "a_luna1")
    assert RunDir(run).eval_log == beside
    current = make_log(tmp_path / "logs" / "study_now" / "a_luna1")
    assert RunDir(run).eval_log == current
    checkout = make_log(tmp_path / "logs" / "study_x" / "a_luna1")
    assert RunDir(run).eval_log == checkout
