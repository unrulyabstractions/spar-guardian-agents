"""scripts/screen.py's resume over a study's groups: a finished run in a group is skipped, and a run started again
moves its earlier attempt into attempts/<name>.<n> first, so its export finds an empty folder. No model runs: the
subprocess is a stand-in."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import screen
from src.configuration import settings

CELL = "openmath.riemann_minimal"


@pytest.fixture
def study(tmp_path, monkeypatch) -> Path:
    """runs/study_x and logs/study_x under tmp_path, as this checkout's."""
    monkeypatch.setattr(settings, "ROOT", tmp_path)
    monkeypatch.setattr(settings, "RUNS", tmp_path / "runs" / "study_x")
    monkeypatch.setattr(settings, "LOGS", tmp_path / "logs" / "study_x")
    monkeypatch.setattr(screen, "LOGS", tmp_path / "logs" / "study_x" / "screen")
    return tmp_path


def left(folder: Path, *, signals: bool, log: Path | None = None) -> Path:
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(json.dumps({"eval_id": folder.name, "ledger": None}))
    if signals:
        (folder / "signals.jsonl").write_text(json.dumps({"row": "team", "sample": "s", "rung": "used"}) + "\n")
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("earlier")
    return folder


def test_a_finished_run_in_a_group_is_skipped_and_an_unfinished_one_set_aside_before_it_runs(study, monkeypatch):
    left(study / "runs" / "study_x" / "invalid" / "riemann_minimal_t1", signals=True)
    left(study / "runs" / "study_x" / "riemann_minimal_t2", signals=False,
         log=study / "logs" / "study_x" / "riemann_minimal_t2" / "a.eval")
    seen = []

    def run(command, **kwargs):
        name = command[command.index("--name") + 1]
        seen.append((name, (settings.RUNS / name).exists(), (settings.LOGS / name).exists()))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(screen.subprocess, "run", run)
    assert screen.main(["--cells", CELL, "--samples", "2", "--suffix", "t", "--jobs", "1"]) == 0
    assert seen == [("riemann_minimal_t2", False, False)]
    attempt = study / "runs" / "study_x" / "attempts" / "riemann_minimal_t2.1"
    assert json.loads((attempt / "run.json").read_text())["eval_id"] == "riemann_minimal_t2"
    assert (study / "logs" / "study_x" / "attempts" / "riemann_minimal_t2.1" / "a.eval").read_text() == "earlier"


def test_a_run_of_one_name_in_two_folders_is_refused_before_anything_starts(study, monkeypatch):
    left(study / "runs" / "study_x" / "riemann_minimal_t1", signals=True)
    left(study / "runs" / "study_x" / "mock" / "riemann_minimal_t1", signals=True)
    monkeypatch.setattr(screen.subprocess, "run", lambda *a, **k: pytest.fail("a run started"))
    with pytest.raises(ValueError, match="2 folders hold a run named riemann_minimal_t1"):
        screen.main(["--cells", CELL, "--samples", "1", "--suffix", "t", "--jobs", "1"])


def test_the_summary_reads_a_finished_run_in_its_group(study, capsys):
    left(study / "runs" / "study_x" / "set_aside" / "riemann_minimal_t1", signals=True)
    assert screen.main(["--cells", CELL, "--samples", "1", "--suffix", "t", "--summary-only"]) == 0
    line = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("riemann_minimal_t1"))
    assert "team:used" in line and "(no ledger)" in line
