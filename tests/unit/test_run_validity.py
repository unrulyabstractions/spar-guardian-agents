"""run_validity: a run is valid only when it is no rehearsal, completed, on a task that met the collaboration rule, with
a regulator that acted, a disregulator that acted and posted, and a team the code ladder placed. Each case builds a run
folder the way the export writes it, under runs/<study>/ or one of its groups, and its eval log at the same path under
logs/."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.measures import run_validity as rv
from tests.support.run_builder import write_status_log

NOTICE = rv.REFUSAL_NOTICE + " by configuring a fallback model"
LOG = "2026-11-01T03-31-41-00-00_run_abc.eval"


def make_run(root: Path, name: str, *, task: str = "protein_design", commit: str = "HEAD", regulator_steps=None,
             failure: str | None = None, rung: str | None = "used", posts: int = 0, group: str | None = None,
             disregulator_steps=None, disregulator_posts: int = 0) -> Path:
    run = root / "runs" / "study" / (group or "") / name
    (run / "trajectories").mkdir(parents=True)
    roles = {"w1": "worker", "w2": "worker", "w3": "worker",
             **({"r1": "regulator"} if regulator_steps is not None else {}),
             **({"d1": "disregulator"} if disregulator_steps is not None else {})}
    data = {"task": task, "code": {"commit": commit}, "agents": list(roles), "swarm": {"roles": roles},
            "rules": {"workers": ["w1", "w2", "w3"], "posts": {"r1": posts, "d1": disregulator_posts}},
            "config": {"regulators": int(regulator_steps is not None),
                       "disregulators": int(disregulator_steps is not None)},
            "model": "openrouter/workers",
            "role_models": {"regulator": "openrouter/x" if regulator_steps is not None else None},
            "eval_log": f"/root/work/logs/study/{name}/{LOG}"}
    (run / "run.json").write_text(json.dumps(data))
    (run / "signals.jsonl").write_text(json.dumps({"row": "team", "rung": rung}) + "\n" if rung else "")
    (run / "episodes.jsonl").write_text(json.dumps({"failure": failure}) + "\n")
    for agent, messages in (("r1", regulator_steps), ("d1", disregulator_steps)):
        if messages is not None:
            folder = run / "trajectories" / f"row#1@{agent}"
            folder.mkdir()
            steps = [{"source": "agent", "message": m} for m in messages]
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


def test_a_disregulator_that_acted_and_posted_keeps_the_run_valid_and_is_no_regulator(tmp_path, every_eval_succeeds):
    record = rv.validity(make_run(tmp_path, "d1", disregulator_steps=["Let us reuse it."], disregulator_posts=2))
    assert record["valid"] and "regulators" not in record
    assert record["disregulators"] == [{"agent": "d1", "model": "openrouter/workers", "turns": 1, "refusals": 0,
                                        "posts": 2, "acted": True}]


def test_a_run_with_both_roles_keeps_each_agent_under_its_own_role(tmp_path, every_eval_succeeds):
    record = rv.validity(make_run(tmp_path, "both1", regulator_steps=["I read it."], posts=1,
                                  disregulator_steps=["Let us reuse it."], disregulator_posts=1))
    assert record["valid"]
    assert [r["agent"] for r in record["regulators"]] == ["r1"]
    assert [d["agent"] for d in record["disregulators"]] == ["d1"]
    assert record["regulators"][0]["model"] == "openrouter/x"


@pytest.mark.parametrize(("steps", "posts", "reason", "why"), [
    ([], 0, "disregulator_silent", "disregulator d1 made no turn"),
    ([NOTICE] * 3, 0, "disregulator_refused", "disregulator d1 never acted: 3 of 3 turns were refusal notices"),
    (["I will think about it."], 0, "disregulator_no_post", "disregulator d1 never posted, so no teammate heard it"),
])
def test_a_disregulator_that_never_reached_the_team_makes_the_run_invalid(tmp_path, every_eval_succeeds, steps, posts,
                                                                          reason, why):
    record = rv.validity(make_run(tmp_path, "d2", disregulator_steps=steps, disregulator_posts=posts))
    assert not record["valid"] and record["reason"] == reason and record["why"] == [why]


def test_a_regulator_that_never_posted_stays_valid_where_a_disregulator_would_not(tmp_path, every_eval_succeeds):
    assert rv.validity(make_run(tmp_path, "r0", regulator_steps=["Nothing to report."], posts=0))["valid"]


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


def test_whole_holds_for_a_complete_run_whatever_the_task_or_the_regulator_did(tmp_path, every_eval_succeeds):
    before_fix = rv.validity(make_run(tmp_path, "flipt_minimal_luna2", task="software_engineering", commit="0dda147"))
    refused = rv.validity(make_run(tmp_path, "e1", regulator_steps=[NOTICE]))
    assert before_fix["whole"] and refused["whole"] and not before_fix["valid"] and not refused["valid"]
    assert not rv.validity(make_run(tmp_path, "f1", failure="timeout"))["whole"]
    assert not rv.validity(tmp_path / "runs" / "study" / "never_ran1")["whole"]


def test_whole_fails_on_an_eval_error_and_on_a_missing_team_row(tmp_path, monkeypatch, every_eval_succeeds):
    assert rv.validity(make_run(tmp_path, "g0"))["whole"]
    assert not rv.validity(make_run(tmp_path, "g1", rung=None))["whole"]
    monkeypatch.setattr(rv, "eval_status", lambda run: "error")
    assert not rv.validity(make_run(tmp_path, "g2"))["whole"]


def test_an_earlier_attempt_never_counts_and_keeps_its_runs_name(tmp_path, every_eval_succeeds):
    """attempts/<name>.<n> is superseded whatever it holds; the mock check reads the run's own name."""
    attempt = rv.validity(make_run(tmp_path, "f_luna1.2", group="attempts"))
    assert (attempt["run"], attempt["valid"], attempt["reason"], attempt["whole"]) == ("f_luna1", False, "superseded",
                                                                                      True)
    mock = rv.validity(make_run(tmp_path, "f_lunamock1.1", group="attempts"))
    assert mock["mock"] and mock["reasons"] == ["superseded", "mock"] and mock["reason"] == "superseded"
    assert rv.validity(make_run(tmp_path, "f_luna1"))["valid"]


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
