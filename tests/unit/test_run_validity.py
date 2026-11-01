"""run_validity: a run is valid only when it is no rehearsal, completed, on a task that met the collaboration rule, with
a regulator that acted and a team the code ladder placed. Each case builds a run folder the way the export writes it,
under runs/<study>/ or one of its groups, and its eval log at the same path under logs/."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.measures import run_validity as rv
from tests.support.run_builder import write_status_log

NOTICE = rv.REFUSAL_NOTICE + " by configuring a fallback model"
LOG = "2026-11-01T03-31-41-00-00_run_abc.eval"


def make_run(root: Path, name: str, *, task: str = "protein_design", commit: str = "HEAD", regulator_steps=None,
             failure: str | None = None, rung: str | None = "used", posts: int = 0, group: str | None = None) -> Path:
    run = root / "runs" / "study" / (group or "") / name
    (run / "trajectories").mkdir(parents=True)
    agents = ["w1", "w2", "w3"] + (["r1"] if regulator_steps is not None else [])
    data = {"task": task, "code": {"commit": commit}, "agents": agents,
            "rules": {"workers": ["w1", "w2", "w3"], "posts": {"r1": posts}},
            "config": {"regulators": int(regulator_steps is not None)},
            "role_models": {"regulator": "openrouter/x" if regulator_steps is not None else None},
            "eval_log": f"/root/work/logs/study/{name}/{LOG}"}
    (run / "run.json").write_text(json.dumps(data))
    (run / "signals.jsonl").write_text(json.dumps({"row": "team", "rung": rung}) + "\n" if rung else "")
    (run / "episodes.jsonl").write_text(json.dumps({"failure": failure}) + "\n")
    if regulator_steps is not None:
        folder = run / "trajectories" / "row#1@r1"
        folder.mkdir()
        steps = [{"source": "agent", "message": m} for m in regulator_steps]
        (folder / "transcript.json").write_text(json.dumps({"trajectory": {"steps": steps}}))
    return run


@pytest.fixture
def every_eval_succeeds(monkeypatch):
    monkeypatch.setattr(rv, "eval_status", lambda run: "success")


def test_a_whole_run_with_an_acting_regulator_is_valid(tmp_path, every_eval_succeeds):
    run = make_run(tmp_path, "binder_minimal_regulated_luna_luna_ambiguous1", regulator_steps=["", "I read the board."])
    record = rv.validity(run)
    assert record["valid"] and record["why"] == [] and record["reason"] == "valid" and record["completed"]
    assert record["regulators"][0]["acted"]


def test_a_regulator_that_only_refused_or_never_spoke_makes_the_run_invalid(tmp_path, every_eval_succeeds):
    refused = rv.validity(make_run(tmp_path, "a1", regulator_steps=[NOTICE] * 5))
    assert not refused["valid"] and refused["why"] == ["regulator r1 never acted: 5 of 5 turns were refusal notices"]
    assert refused["reason"] == "regulator_refused"
    silent = rv.validity(make_run(tmp_path, "b1", regulator_steps=[]))
    assert silent["why"] == ["regulator r1 made no turn"] and silent["reason"] == "regulator_silent"


@pytest.mark.parametrize(("commit", "fixed"), [
    ("0dda147", False),     # the first gate, before both fixes
    ("bfc81a8", False),     # after 7331882: one agent could not cheat alone, but the pieces together did not pass
    ("f0d1c34", True),      # SWE_FIX: every piece together passes, no agent's own holdings do
    ("94940bc", True),      # the fixed stages' runs
])
def test_an_swe_run_counts_only_from_the_commit_whose_pieces_pass_together(tmp_path, every_eval_succeeds, commit,
                                                                            fixed):
    record = rv.validity(make_run(tmp_path, "flipt_minimal_luna1", task="software_engineering", commit=commit))
    assert record["swe_fixed"] is fixed and record["valid"] is fixed
    assert record["reason"] == ("valid" if fixed else "swe_before_fix")


def test_an_swe_commit_git_does_not_know_is_invalid(tmp_path, every_eval_succeeds):
    record = rv.validity(make_run(tmp_path, "flipt_minimal_luna2", task="software_engineering", commit="ffffff0"))
    assert record["swe_fixed"] is None and record["reason"] == "swe_commit_unknown"


def test_a_failed_episode_a_failed_eval_a_missing_team_and_a_mock_are_invalid(tmp_path, monkeypatch,
                                                                              every_eval_succeeds):
    timeout = rv.validity(make_run(tmp_path, "c1", failure="timeout"))
    assert timeout["why"] == ["failed episodes: timeout"] and timeout["reason"] == "failed_episodes"
    assert timeout["completed"]
    died = rv.validity(make_run(tmp_path, "c2", failure="connection_closed"))
    assert died["why"] == ["failed episodes: connection_closed"] and died["reason"] == "incomplete"
    assert not died["completed"]
    unplaced = rv.validity(make_run(tmp_path, "c3", rung=None))
    assert unplaced["why"] == ["no team row in signals.jsonl"] and unplaced["reason"] == "no_team_record"
    mock = rv.validity(make_run(tmp_path, "binder_minimal_lunamock1", task="software_engineering", commit="0dda147"))
    assert mock["mock"] and not mock["valid"] and mock["reason"] == "mock"
    assert mock["reasons"] == ["mock", "swe_before_fix"] and len(mock["why"]) == 2
    monkeypatch.setattr(rv, "eval_status", lambda run: "error")
    error = rv.validity(make_run(tmp_path, "d1"))
    assert error["why"] == ["eval error"] and error["reason"] == "incomplete" and not error["completed"]


def test_the_eval_status_is_read_from_the_log_at_the_mirrored_path(tmp_path):
    """No stub: the header of the log under logs/<study>/[<group>/]<run>/ decides."""
    top = make_run(tmp_path, "e1")
    assert rv.validity(top)["why"][0] == "eval log missing"
    write_status_log(tmp_path / "logs" / "study" / "e1" / LOG, "success")
    assert rv.validity(top)["valid"]
    grouped = make_run(tmp_path, "e2", group="incomplete")
    write_status_log(tmp_path / "logs" / "study" / "incomplete" / "e2" / LOG, "error")
    record = rv.validity(grouped)
    assert record["eval_status"] == "error" and record["reason"] == "incomplete"


def test_the_command_reads_the_runs_of_every_group(tmp_path, capsys, every_eval_succeeds):
    make_run(tmp_path, "f1")
    make_run(tmp_path, "f2", group="invalid", failure="timeout")
    make_run(tmp_path, "f3mock1", group="mock")
    make_run(tmp_path, "f4", group="set_aside", regulator_steps=[])
    assert rv.main([str(tmp_path / "runs" / "study")]) == 0
    out = capsys.readouterr().out
    assert "invalid/f2" in out and "mock/f3mock1" in out and "set_aside/f4" in out
    assert "== 3 runs: 1 valid, 2 not; 1 mock rehearsals" in out
    assert "== by reason: valid 1, mock 1, regulator_silent 1, failed_episodes 1" in out
