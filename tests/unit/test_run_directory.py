"""Where a study keeps its runs (src/measures/run_directory.py): runs at its top level and in its groups, and each run's
eval log at the same path under logs/. Every case builds the folders under tmp_path."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.configuration import settings
from src.measures.run_directory import (
    GROUPS,
    RunDir,
    figures_dir,
    find_run,
    mirrored_in_logs,
    run_name,
    run_place,
    set_aside_attempt,
    study_runs,
)

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
    for path in ("b_luna1", "a_luna2", "mock/a_lunamock1", "invalid/c_luna1", "incomplete/d_luna1", "set_aside/e1",
                 "attempts/b_luna1.1"):
        make_run(study / path)
    (study / "smoke" / "scenario").mkdir(parents=True)
    (study / "smoke" / "scenario" / "run.json").write_text("{}")      # a smoke suite's folder is no group
    (study / "notes").mkdir()
    (study / "INDEX.jsonl").write_text("")
    assert [p.relative_to(study).as_posix() for p in study_runs(study)] == [
        "a_luna2", "mock/a_lunamock1", "b_luna1", "attempts/b_luna1.1", "invalid/c_luna1", "incomplete/d_luna1",
        "set_aside/e1"]
    assert set(GROUPS) == {"invalid", "incomplete", "mock", "set_aside", "attempts"}


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


def test_a_nested_smoke_run_finds_its_log_by_the_mirror(tmp_path, elsewhere):
    """A smoke run sits three folders under its study; its recorded path is the box's or a moved checkout's."""
    place = Path("study_smoke") / "smoke_access" / "20261031T061858Z" / "hidden_read"
    run = make_run(tmp_path / "runs" / place, recorded=f"/root/work/logs/smoke_access/20261031T061858Z/{{name}}/{LOG}")
    assert RunDir(run).eval_log is None
    log = make_log(tmp_path / "logs" / place)
    assert RunDir(run).eval_log == log


def test_an_earlier_attempt_keeps_its_runs_name_and_never_answers_to_it(tmp_path):
    study = tmp_path / "runs" / "study_x"
    attempt, current = make_run(study / "attempts" / "f_luna1.2"), make_run(study / "f_luna1")
    assert (run_name(attempt), run_name(current), RunDir(attempt).run_name, RunDir(attempt).name) == (
        "f_luna1", "f_luna1", "f_luna1", "f_luna1.2")
    assert run_name(study / "invalid" / "g_luna1.2") == "g_luna1.2"      # only the attempts group drops .<n>
    assert run_place(attempt) == ("study_x", "attempts")
    assert find_run(study, "f_luna1") == current
    current.rename(study / "gone")
    assert find_run(study, "f_luna1") is None                             # the attempt does not stand in for it


def test_a_run_is_found_by_name_at_the_top_level_or_in_its_one_group(tmp_path):
    study = tmp_path / "runs" / "study_x"
    grouped = make_run(study / "invalid" / "c_luna1")
    assert find_run(study, "c_luna1") == grouped
    make_run(study / "c_luna1")
    with pytest.raises(ValueError, match="2 folders hold a run named c_luna1"):
        find_run(study, "c_luna1")
    assert find_run(study, "nothing1") is None


def test_the_figures_mirror_the_run_folder_under_out(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ROOT", tmp_path / "checkout")
    runs, out = tmp_path / "runs" / "study_x", tmp_path / "out" / "study_x"
    assert figures_dir(runs / "a1") == out / "a1"
    assert figures_dir(runs / "mock" / "a1") == out / "mock" / "a1"
    assert figures_dir(runs / "attempts" / "a1.2") == out / "attempts" / "a1.2"
    assert figures_dir(tmp_path / "elsewhere" / "a1") == tmp_path / "checkout" / "out" / "a1"


def made(folder: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(text)
    return folder


def files_under(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): path.read_text() for path in sorted(root.rglob("*")) if path.is_file()}


def test_an_earlier_attempt_moves_into_attempts_with_its_log_and_figures(tmp_path):
    """The run folder, its eval log folder and its figures move to attempt 1, every byte kept; a second set-aside takes
    attempt 2. Nothing is left at the run's own places."""
    run = made(tmp_path / "runs" / "study_x" / "f_luna1", {"run.json": "{}", "signals.jsonl": "first"})
    made(tmp_path / "logs" / "study_x" / "f_luna1", {"a.eval": "log a"})
    made(tmp_path / "out" / "study_x" / "f_luna1", {"timeline.png": "png a"})
    before = files_under(tmp_path)
    moved = set_aside_attempt(run)
    assert [target.relative_to(tmp_path).as_posix() for _, target in moved] == [
        "runs/study_x/attempts/f_luna1.1", "logs/study_x/attempts/f_luna1.1", "out/study_x/attempts/f_luna1.1"]
    assert files_under(tmp_path) == {name.replace("study_x/f_luna1", "study_x/attempts/f_luna1.1"): text
                                     for name, text in before.items()}
    made(run, {"run.json": "{}"})
    assert [target.name for _, target in set_aside_attempt(run)] == ["f_luna1.2"]
    assert set_aside_attempt(run) == []                                   # nothing left to set aside


def test_attempt_n_is_free_in_runs_logs_and_out(tmp_path):
    """A number taken under logs/ alone is taken: the run's three folders share their n."""
    run = made(tmp_path / "runs" / "study_x" / "f_luna1", {"run.json": "{}"})
    made(tmp_path / "logs" / "study_x" / "attempts" / "f_luna1.1", {"x.eval": "older"})
    assert [target.relative_to(tmp_path).as_posix() for _, target in set_aside_attempt(run)] == [
        "runs/study_x/attempts/f_luna1.2"]


def test_the_log_about_to_be_exported_stays_where_it_is(tmp_path):
    """A later eval's log in the earlier run's log folder stays; every other entry of that folder moves."""
    run = made(tmp_path / "runs" / "study_x" / "f_luna1", {"run.json": "{}"})
    logs = made(tmp_path / "logs" / "study_x" / "f_luna1", {"a.eval": "earlier", "b.eval": "later"})
    moved = set_aside_attempt(run, keep=logs / "b.eval")
    assert files_under(tmp_path / "logs") == {"study_x/attempts/f_luna1.1/a.eval": "earlier",
                                              "study_x/f_luna1/b.eval": "later"}
    assert [source.name for source, _ in moved] == ["f_luna1", "a.eval"]


def test_a_run_in_a_group_is_set_aside_into_its_studys_attempts(tmp_path):
    run = made(tmp_path / "runs" / "study_x" / "incomplete" / "f_luna1", {"run.json": "{}"})
    made(tmp_path / "logs" / "study_x" / "incomplete" / "f_luna1", {"a.eval": "log"})
    assert [target.relative_to(tmp_path).as_posix() for _, target in set_aside_attempt(run)] == [
        "runs/study_x/attempts/f_luna1.1", "logs/study_x/attempts/f_luna1.1"]


def test_an_attempt_a_run_outside_runs_and_a_deeper_kept_file_are_refused(tmp_path):
    attempt = made(tmp_path / "runs" / "study_x" / "attempts" / "f_luna1.1", {"run.json": "{}"})
    with pytest.raises(ValueError, match="earlier attempt already"):
        set_aside_attempt(attempt)
    with pytest.raises(ValueError, match="no runs/ folder"):
        set_aside_attempt(made(tmp_path / "elsewhere" / "f_luna1", {"run.json": "{}"}))
    run = made(tmp_path / "runs" / "study_x" / "g_luna1", {"run.json": "{}"})
    logs = made(tmp_path / "logs" / "study_x" / "g_luna1", {"sub/b.eval": "later"})
    with pytest.raises(ValueError, match="subfolder"):
        set_aside_attempt(run, keep=logs / "sub" / "b.eval")


def test_an_earlier_attempt_reads_its_own_log_never_the_later_runs(tmp_path, elsewhere):
    """After the set-aside a later run writes logs/<study>/<name>/ again; the attempt finds its log under attempts/."""
    run = make_run(tmp_path / "runs" / "study_x" / "f_luna1")
    make_log(tmp_path / "logs" / "study_x" / "f_luna1")
    set_aside_attempt(run)
    later = make_log(tmp_path / "logs" / "study_x" / "f_luna1")
    attempt = RunDir(tmp_path / "runs" / "study_x" / "attempts" / "f_luna1.1")
    assert attempt.eval_log == tmp_path / "logs" / "study_x" / "attempts" / "f_luna1.1" / LOG != later
