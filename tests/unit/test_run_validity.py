"""run_validity: a run is valid only when whole, on a task that met the collaboration rule, and with a regulator that
acted. Each case builds a run folder the way the export writes it."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.measures import run_validity as rv

NOTICE = rv.REFUSAL_NOTICE + " by configuring a fallback model"


def make_run(root: Path, name: str, *, task: str = "protein_design", commit: str = "HEAD", regulator_steps=None,
             failure: str | None = None, rung: str = "used", posts: int = 0) -> Path:
    run = root / "study" / name
    (run / "trajectories").mkdir(parents=True)
    agents = ["w1", "w2", "w3"] + (["r1"] if regulator_steps is not None else [])
    data = {"task": task, "code": {"commit": commit}, "agents": agents,
            "rules": {"workers": ["w1", "w2", "w3"], "posts": {"r1": posts}},
            "config": {"regulators": int(regulator_steps is not None)},
            "role_models": {"regulator": "openrouter/x" if regulator_steps is not None else None}}
    (run / "run.json").write_text(json.dumps(data))
    (run / "signals.jsonl").write_text(json.dumps({"row": "team", "rung": rung}) + "\n")
    (run / "episodes.jsonl").write_text(json.dumps({"failure": failure}) + "\n")
    if regulator_steps is not None:
        folder = run / "trajectories" / "row#1@r1"
        folder.mkdir()
        steps = [{"source": "agent", "message": m} for m in regulator_steps]
        (folder / "transcript.json").write_text(json.dumps({"trajectory": {"steps": steps}}))
    return run


@pytest.fixture(autouse=True)
def every_eval_succeeds(monkeypatch):
    monkeypatch.setattr(rv, "_eval_status", lambda name, logs: "success")


def test_a_whole_run_with_an_acting_regulator_is_valid(tmp_path):
    run = make_run(tmp_path, "binder_minimal_regulated_luna_luna_ambiguous1", regulator_steps=["", "I read the board."])
    record = rv.validity(run, tmp_path)
    assert record["valid"] and record["why"] == [] and record["regulators"][0]["acted"]


def test_a_regulator_that_only_refused_or_never_spoke_makes_the_run_invalid(tmp_path):
    refused = rv.validity(make_run(tmp_path, "a1", regulator_steps=[NOTICE] * 5), tmp_path)
    assert not refused["valid"] and refused["why"] == ["regulator r1 never acted: 5 of 5 turns were refusal notices"]
    silent = rv.validity(make_run(tmp_path, "b1", regulator_steps=[]), tmp_path)
    assert silent["why"] == ["regulator r1 made no turn"]


def test_an_swe_run_before_the_fix_is_invalid_and_after_it_valid(tmp_path):
    before = rv.validity(make_run(tmp_path, "flipt_minimal_luna1", task="software_engineering", commit="0dda147"),
                         tmp_path)
    assert before["swe_fixed"] is False and "SWE task before the fix" in before["why"][0]
    after = rv.validity(make_run(tmp_path, "flipt_minimal_luna_fixed1", task="software_engineering",
                                 commit=rv.SWE_FIX), tmp_path)
    assert after["swe_fixed"] is True and after["valid"]


def test_a_failed_episode_a_failed_eval_and_a_mock_are_invalid(tmp_path, monkeypatch):
    assert rv.validity(make_run(tmp_path, "c1", failure="timeout"), tmp_path)["why"] == ["failed episodes: timeout"]
    monkeypatch.setattr(rv, "_eval_status", lambda name, logs: "error")
    assert rv.validity(make_run(tmp_path, "d1"), tmp_path)["why"] == ["eval error"]
    monkeypatch.setattr(rv, "_eval_status", lambda name, logs: "success")
    mock = rv.validity(make_run(tmp_path, "binder_minimal_lunamock1"), tmp_path)
    assert mock["mock"] and not mock["valid"]
