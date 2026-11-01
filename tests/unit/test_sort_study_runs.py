"""scripts/sort_study_runs.py on a study built under tmp_path: the dry run writes nothing; --apply moves each run into
its group with its eval log and its figures, renames a hand-made earlier attempt into attempts/, records every file with
its size and sha256 and checks each one after the move; it refuses live boxes, young runs, names two folders hold and
taken targets, and a mismatch exits 1. Every run keeps every index field but its path and its group."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from scripts import sort_study_runs as sort
from src.measures.run_index import FIELDS, build
from tests.support.run_builder import write_status_log

STUDY = "study_x"
LOG = "2026-11-01T03-31-41-00-00_run_abc.eval"
OLD = time.time() - 3600


def run(root: Path, place: str, *, status: str = "success", commit: str = "94940bc", task: str = "openmath_agent",
        log: str = LOG, study: str = STUDY) -> Path:
    """A run folder at runs/<study>/<place> whose run.json names its eval log, the log at the same place under logs/,
    an hour old."""
    name = Path(place).name.split(".attempt")[0]
    folder = root / "runs" / study / place
    folder.mkdir(parents=True)
    data = {"run_id": name, "task": task, "eval_id": f"{place}-eval", "config": {"seed": 1}, "code": {"commit": commit},
            "agents": ["a01"], "rules": {"workers": ["a01"]}, "eval_log": f"/root/work/logs/{study}/{name}/{log}",
            "validity": {"ok": True},
            **({"swe": {"issue": "flipt-02e2", "honeypot": "jigsaw"}} if task == "software_engineering" else {})}
    (folder / "run.json").write_text(json.dumps(data))
    (folder / "signals.jsonl").write_text(json.dumps({"row": "team", "rung": "none"}) + "\n")
    (folder / "episodes.jsonl").write_text(json.dumps({"failure": None}) + "\n")
    os.utime(folder / "run.json", (OLD, OLD))
    return folder


@pytest.fixture
def study(tmp_path) -> Path:
    """A study of every case the sort meets, with a second study that shares one run name."""
    for name, status in (("riemann_minimal_luna1", "success"), ("riemann_minimal_lunamock1", "success"),
                         ("riemann_minimal_luna2", "error"), ("riemann_minimal_luna3", "error"),
                         ("riemann_minimal_luna4", "success"), ("riemann_minimal_luna5", "success"),
                         ("riemann_minimal_luna6", "success")):
        run(tmp_path, name)
        write_status_log(tmp_path / "logs" / STUDY / name / LOG, status)
    run(tmp_path, "flipt_minimal_luna1", commit="0dda147", task="software_engineering")    # SWE before the fix
    write_status_log(tmp_path / "logs" / STUDY / "flipt_minimal_luna1" / LOG, "success")
    # an earlier attempt of riemann_minimal_luna3, set aside by hand; its log sits in its run's log folder
    run(tmp_path, "failed_attempts/riemann_minimal_luna3.attempt1", log="2026-11-01T01-00-00-00-00_early.eval")
    write_status_log(tmp_path / "logs" / STUDY / "riemann_minimal_luna3" / "2026-11-01T01-00-00-00-00_early.eval",
                     "error")
    os.utime(tmp_path / "runs" / STUDY / "riemann_minimal_luna4" / "run.json")               # just pulled
    (tmp_path / "logs" / STUDY / "screen").mkdir(parents=True)
    (tmp_path / "logs" / STUDY / "screen" / "box_b1.json").write_text(json.dumps(
        {"box": "b1", "runs": {"riemann_minimal_luna5": {}}}))
    (tmp_path / "runs" / "boxes.json").write_text(json.dumps({"boxes": [{"id": 1, "box": "b1", "destroyed": None},
                                                                        {"id": 2, "box": "b0", "destroyed": "x"}]}))
    run(tmp_path, "set_aside/riemann_minimal_luna6")                                          # held twice
    for place in ("riemann_minimal_luna1", f"{STUDY}/riemann_minimal_lunamock1", "flipt_minimal_luna1"):
        (tmp_path / "out" / place).mkdir(parents=True)
        (tmp_path / "out" / place / "timeline.png").write_text(f"png of {place}")
    run(tmp_path, "flipt_minimal_luna1", study="study_y")              # another study holds that name, no figures
    return tmp_path


def tree(root: Path) -> dict[str, tuple[int, float, str]]:
    """Every file under root with its size, its mtime and its sha256."""
    return {path.relative_to(root).as_posix(): (path.stat().st_size, path.stat().st_mtime,
                                                hashlib.sha256(path.read_bytes()).hexdigest())
            for path in sorted(root.rglob("*")) if path.is_file()}


def test_the_dry_run_writes_nothing_and_says_what_would_move(study, capsys):
    before = tree(study)
    assert sort.main([str(study / "runs" / STUDY)]) == 0
    assert tree(study) == before
    out = capsys.readouterr().out
    assert "== runs to move: 5 (attempts 1, incomplete 2, invalid 1, mock 1)" in out
    assert "== left, live box: 1 ['riemann_minimal_luna5']" in out
    assert "== left, young run.json: 1 ['riemann_minimal_luna4']" in out
    assert "== left, two folders: 2 ['riemann_minimal_luna6', 'set_aside/riemann_minimal_luna6']" in out
    assert "flipt_minimal_luna1: out/flipt_minimal_luna1 may be another study's (study_y)" in out
    assert "== dry run: nothing moved" in out


def records(study: Path) -> dict[str, dict]:
    return {rec["path"]: rec for rec in build(study / "runs" / STUDY)[0]}


def test_apply_moves_each_run_with_its_log_and_figures_and_checks_every_file(study, capsys):
    before = records(study)
    assert sort.main([str(study / "runs" / STUDY), "--apply"]) == 0
    runs, logs, out = (study / top / STUDY for top in ("runs", "logs", "out"))
    for place in ("mock/riemann_minimal_lunamock1", "incomplete/riemann_minimal_luna3", "invalid/flipt_minimal_luna1",
                  "incomplete/riemann_minimal_luna2", "riemann_minimal_luna1", "riemann_minimal_luna4",
                  "riemann_minimal_luna5"):
        assert (runs / place / "run.json").is_file(), place
    assert (logs / "mock" / "riemann_minimal_lunamock1" / LOG).is_file()
    assert sorted(path.name for path in (logs / "incomplete" / "riemann_minimal_luna3").iterdir()) == [LOG]
    attempt = runs / "attempts" / "riemann_minimal_luna3.1"
    assert json.loads((attempt / "run.json").read_text())["run_id"] == "riemann_minimal_luna3"
    assert [path.name for path in (logs / "attempts" / "riemann_minimal_luna3.1").iterdir()] == [
        "2026-11-01T01-00-00-00-00_early.eval"]
    assert not (runs / sort.HAND_MADE).exists()
    assert (out / "riemann_minimal_luna1" / "timeline.png").read_text() == "png of riemann_minimal_luna1"
    assert (out / "mock" / "riemann_minimal_lunamock1" / "timeline.png").is_file()
    assert (study / "out" / "flipt_minimal_luna1").is_dir() and not (out / "invalid" / "flipt_minimal_luna1").exists()
    [record] = (study / "logs" / "moves").glob(f"*_sort_{STUDY}.jsonl")
    *rows, result = [json.loads(line) for line in record.read_text().splitlines()]
    assert result == {"verified": True, "files": len(rows), "bytes": sum(row["size"] for row in rows),
                      "mismatches": []}
    for row in rows:
        target = study / row["target"]
        assert target.stat().st_size == row["size"] and hashlib.sha256(target.read_bytes()).hexdigest() == row["sha256"]
        assert not (study / row["source"]).exists()
    assert "== verified" in capsys.readouterr().out
    after = {rec["run"] + "|" + str(rec["group"] == "attempts"): rec for rec in records(study).values()}
    for path, rec in before.items():
        moved = after[rec["run"] + "|False"]
        assert {key: moved[key] for key in FIELDS if key not in ("path", "group")} == {
            key: rec[key] for key in FIELDS if key not in ("path", "group")}, path
    assert after["riemann_minimal_luna3|True"]["reason"] == "superseded"


def test_a_file_that_arrives_changed_exits_1(study, monkeypatch, capsys):
    rename = Path.rename

    def damaging(self, target):
        moved = rename(self, target)
        if Path(target).name == "riemann_minimal_lunamock1" and Path(target).parent.name == "mock" and \
                Path(target).parent.parent.parent.name == "runs":
            (Path(target) / "signals.jsonl").write_text("changed on the way")
        return moved

    monkeypatch.setattr(Path, "rename", damaging)
    assert sort.main([str(study / "runs" / STUDY), "--apply"]) == 1
    assert "MISMATCH: runs/study_x/mock/riemann_minimal_lunamock1/signals.jsonl: size or sha256 differs" in \
        capsys.readouterr().out
    [record] = (study / "logs" / "moves").glob("*.jsonl")
    assert json.loads(record.read_text().splitlines()[-1])["verified"] is False


def test_a_taken_target_is_left_and_named(study, capsys):
    run(study, "mock/riemann_minimal_lunamock1x")
    (study / "runs" / STUDY / "mock" / "riemann_minimal_lunamock1x").rename(
        study / "runs" / STUDY / "mock" / "riemann_minimal_lunamock1")
    (study / "runs" / STUDY / "mock" / "riemann_minimal_lunamock1" / "run.json").unlink()      # a folder, no run
    assert sort.main([str(study / "runs" / STUDY)]) == 0
    assert "== left, target exists: 1 ['runs/study_x/mock/riemann_minimal_lunamock1']" in capsys.readouterr().out


def test_a_live_box_with_no_name_refuses_the_whole_sort(study, capsys):
    boxes = json.loads((study / "runs" / "boxes.json").read_text())
    boxes["boxes"].append({"id": 3, "destroyed": None})
    (study / "runs" / "boxes.json").write_text(json.dumps(boxes))
    before = tree(study)
    assert sort.main([str(study / "runs" / STUDY), "--apply"]) == 2
    assert "REFUSED: live instances with no box name [3]" in capsys.readouterr().out
    assert tree(study) == before


def test_a_folder_that_is_no_study_is_refused(study):
    for folder in (study / "runs", study / "runs" / STUDY / "set_aside", study / "elsewhere"):
        with pytest.raises(SystemExit) as refused:
            sort.main([str(folder)])
        assert refused.value.code == 2
