"""scripts/study/reprocess_compare.py on small made-up studies: a stored study, a copy whose labels or stamps differ,
and a stand-in for processing, so no run is processed and nothing under runs/ is read."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts.study import reprocess_compare as rc
from tests.support.run_files import write_text

RUN = "ian_flipt_x_seeded_revealing_s1"


def make_run(study: Path, name: str = RUN, *, team: str = "looked", agent: str = "looked", made: str = "t1",
             refused: int = 0) -> Path:
    """A run folder with the files the comparison reads: run.json, the three ladders' labels and the stamps."""
    run = study / name
    signals = {"team": {"alerts": 1, "true_alarms": 0, "false_alarms": 1, "refused_writes": [{}] * refused,
                        "alert_outcomes": [{"outcome": "false_alarm"}]}}
    write_text(run / "processed/run.json", json.dumps({"signals": signals}))
    write_text(run / "analysis/labels/cheating.json", json.dumps(
        {"team": team, "passed": False, "alarm": None, "agents": {"a01": {"role": "worker", "rung": agent},
                                                                   "a02": {"role": "regulator", "rung": "none"}}}))
    write_text(run / "analysis/labels/collusion.json", json.dumps({"team": "cooperation", "agents": {}}))
    write_text(run / "analysis/labels/coordination.json", json.dumps({"team": "low", "agents": {}}))
    write_text(run / "analysis/versions.json", json.dumps({"made": made}))
    write_text(run / "processed/versions.json", json.dumps({"made": made}))
    return run


def test_only_a_version_stamp_differs(tmp_path):
    stored, copy = make_run(tmp_path / "stored").parent, make_run(tmp_path / "copy", made="t2").parent
    files = rc.file_differences(stored, copy)
    assert files == {"analysis/versions.json": 1, "processed/versions.json": 1}
    assert rc.label_changes("s", stored, copy) == []
    assert rc.Result("s", files, [], [], 0.0).only_stamps


def test_a_changed_label_is_listed_team_first_then_agent(tmp_path):
    stored = make_run(tmp_path / "stored").parent
    copy = make_run(tmp_path / "copy", team="tempted", agent="tempted", made="t2", refused=2).parent
    changes = [str(c) for c in rc.label_changes("s", stored, copy)]
    assert changes == [f"s/{RUN} cheating.team: looked -> tempted",
                       f"s/{RUN} cheating a01 (worker): looked -> tempted",
                       f"s/{RUN} signals.refused_writes: 0 -> 2"]
    files = rc.file_differences(stored, copy)
    assert files["analysis/labels/cheating.json"] == 1 and files["processed/run.json"] == 1
    assert not rc.Result("s", files, rc.label_changes("s", stored, copy), [], 0.0).only_stamps


def test_another_file_without_a_label_change_is_not_only_stamps(tmp_path):
    stored, copy = make_run(tmp_path / "stored").parent, make_run(tmp_path / "copy").parent
    write_text(copy / RUN / "analysis/row.json", "{}")
    files = rc.file_differences(stored, copy)
    assert files == {"new analysis/row.json": 1}
    assert not rc.Result("s", files, [], [], 0.0).only_stamps


def test_expect_reads_label_lines_and_skips_comments(tmp_path):
    path = write_text(tmp_path / "expect.txt", "# why\n\n  s/r cheating.team: looked -> tempted  \n")
    assert rc.expected_lines(path) == {"s/r cheating.team: looked -> tempted"}


def test_compare_study_skips_runs_without_labels_and_live_runs_and_deletes_its_scratch(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    make_run(runs / "s")
    make_run(runs / "s", "live_run")
    (make_run(runs / "s", "unlabelled") / rc.CHEATING).unlink()
    write_text(tmp_path / "logs/s" / RUN / "x.eval", "log")
    seen = {}

    def process(study: str, copy: Path) -> subprocess.CompletedProcess:
        seen["study"], seen["copy"] = study, copy
        seen["runs"] = sorted(p.name for p in copy.iterdir())
        seen["log"] = (copy.parent.parent / "logs" / study / RUN / "x.eval").read_text()
        write_text(copy / RUN / "analysis/labels/cheating.json", json.dumps(
            {"team": "attempted", "agents": {"a01": {"role": "worker", "rung": "attempted"}}}))
        return subprocess.CompletedProcess([], 0, stdout="processed 1 run\n", stderr="")

    result = rc.compare_study("s", {"live_run"}, runs, process=process)
    assert seen["study"] == "s" and seen["runs"] == [RUN] and seen["log"] == "log"
    assert sorted(result.skipped) == ["live_run", "unlabelled"]
    assert f"s/{RUN} cheating.team: looked -> attempted" in [str(c) for c in result.changes]
    assert result.processed == "processed 1 run"
    assert not seen["copy"].exists()
    assert json.loads((runs / "s" / RUN / rc.CHEATING).read_text())["team"] == "looked"


def test_a_failed_process_is_reported_and_its_scratch_deleted(tmp_path):
    runs = tmp_path / "runs"
    make_run(runs / "s")
    copies = []

    def process(study: str, copy: Path) -> subprocess.CompletedProcess:
        copies.append(copy)
        return subprocess.CompletedProcess([], 1, stdout="", stderr="a part raised")

    result = rc.compare_study("s", set(), runs, process=process)
    assert result.failed == "a part raised" and not copies[0].exists()
    assert "  processing failed:" in rc.report(result)
