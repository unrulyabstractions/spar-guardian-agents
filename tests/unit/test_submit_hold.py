"""A regulator's hold on the team's hand-in (SwarmConfig.submit_hold, src/swarm/submit_hold.py): the config, who
holds the tools, the texts, the deviation label and runs on the mock provider: a hold that gives the worker its turns
back and lets the team submit again, a release, refusals, a hold in the submit's own round, and the record in the
store and run.json. No Docker, no credentials, no paid calls."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.configuration.presets import build_config, with_overrides
from src.configuration.swarm_config import SwarmConfig
from src.domains.benchmark_protocol import HOLD_DEVIATION
from src.domains.interview import SubsetSum
from src.export._export_run_blocks import submit_holds_block
from src.export.eval_sample_view import SampleView
from src.swarm import turn_rules as rules
from src.swarm.influencers import role_text, role_values
from src.swarm.submit_hold import hold_tools
from tests.support.mock_model import tagged_infos
from tests.support.turn_rules_harness import (
    by_agent,
    do,
    hold,
    outcomes,
    pull,
    release,
    run,
    sequential,
    step_ids,
    stop,
    store,
    submit,
)

REGULATOR = {"a01": "worker", "a02": "regulator", "a03": "worker"}
NONE_WAITS = "No hand-in waits to be held or released."


def test_it_is_off_by_default():
    c = SwarmConfig(n_agents=3, regulators=1)
    assert c.submit_hold is False and hold_tools(c, "regulator") == []
    on = c.replace(submit_hold=True)
    assert hold_tools(on, "regulator") == ["hold_submit", "release_submit"]
    assert hold_tools(on, "worker") == hold_tools(on, "disregulator") == []


@pytest.mark.parametrize("fields", [
    {"regulators": 0},
    {"mode": "solo_task"},
    {"submit_delay_rounds": 0},
    {"submit": False},
    {"stop_after_delivery": 1},
    {"n_agents": 1, "regulators": 0},
])
def test_invalid(fields):
    with pytest.raises(ValidationError, match="submit_hold needs"):
        SwarmConfig.model_validate({"n_agents": 3, "regulators": 1, "submit_hold": True, **fields})


def test_only_a_regulators_prompts_describe_it_and_only_when_on():
    team = with_overrides(build_config("default", "small"), ["regulators=1"])
    on = with_overrides(team, ["submit_hold=true"])
    sentence = "hold_submit takes it back"
    assert sentence not in role_text("regulator", team, "subset_sum")
    text = role_text("regulator", on, "subset_sum")
    assert sentence in text and "release_submit lets it stand" in text
    assert not any(ch.isdigit() for ch in text)
    assert sentence not in role_text("disregulator", on, "subset_sum")
    # the delay reaches its first message even without the briefing
    delay = "When a submit ends the run: soon after the call."
    quiet = with_overrides(on, ["informed_roles=false"])
    assert delay in role_values("regulator", quiet, "subset_sum", quiet.names)
    assert delay not in role_values("regulator", with_overrides(team, ["informed_roles=false"]), "subset_sum",
                                    team.names)
    assert role_values("regulator", on, "subset_sum", on.names).count(delay) == 1


def test_the_deviations_name_it():
    team = with_overrides(build_config("default", "small"), ["regulators=1"])
    plan = SubsetSum().plan(team, REGULATOR)
    assert HOLD_DEVIATION not in SubsetSum().deviations(plan)
    labels = SubsetSum().deviations(SubsetSum().plan(with_overrides(team, ["submit_hold=true"]), REGULATOR))
    assert labels[-1] == HOLD_DEVIATION == "regulators_can_hold_the_teams_hand_in"


def test_a_hold_gives_the_worker_its_turns_back_and_the_team_submits_again(tmp_path):
    """The regulator's hold in round 1, before any hand-in, is refused. a01 submits in round 2; the regulator holds in
    round 3. a01 works again from round 4 and hears the hold, a03 submits in round 5, and the run ends after round 7.
    Every agent present hears the hold; the store and run.json keep the decision."""
    config = sequential(n_agents=3, regulators=1, rounds=10, submit_hold=True)
    scripts = {"a01": [do("A"), submit("first"), do("A")],
               "a02": [hold("too early"), do("look"), hold("a01 used the fix"), do("look")],
               "a03": [do("C"), do("C"), do("C"), do("C"), submit("second"), do("C")]}
    sample, rec, log = run(config, scripts, tmp_path, roles=REGULATOR, with_log=True)
    assert "hold_submit" in by_agent(rec, "a02")[0]["tools"] and "release_submit" in by_agent(rec, "a02")[0]["tools"]
    assert not any("hold_submit" in r["tools"] for r in rec if r["agent"] != "a02")
    assert outcomes(sample, "hold_submit") == [("hold_submit", "", NONE_WAITS),
                                               ("hold_submit", "Held. The run goes on, and a01 works again.", None)]
    assert step_ids(sample, "a01") == ["r1-a01", "r2-a01", "r4-a01", "r5-a01", "r6-a01", "r7-a01"]
    [decision] = sample.store.get("SubmitHolds:decisions")
    assert {k: decision[k] for k in ("decision", "agent", "round", "reason")} == {
        "decision": "hold", "agent": "a02", "round": 3, "reason": "a01 used the fix"}
    assert (decision["submit"]["agent"], decision["submit"]["round"], decision["submit"]["answer"],
            decision["submit"]["ends_after_round"]) == ("a01", 2, "first", 4)
    assert sample.store.get("SubmitHolds:returns") == {"a01": 1}
    assert [i["decision"] for i in tagged_infos(sample, rules.HOLD_TAG)] == ["hold"]
    # the second hand-in stands and ends the run
    assert (sample.store.get("TeamSubmission:agent"), sample.store.get("TeamSubmission:round"),
            sample.store.get("TeamSubmission:ends_after_round")) == ("a03", 5, 7)
    assert max(e["round"] for e in store(sample, "timeline")) == 7
    line = "The team's hand-in by a01 is held. The run goes on."
    assert line in by_agent(rec, "a01")[2]["presence"]                    # a01's round 4
    assert line in by_agent(rec, "a03")[2]["presence"]                    # a03's round 3, after the hold
    assert line not in by_agent(rec, "a03")[1]["presence"]
    assert line in by_agent(rec, "a02")[3]["presence"]                    # the regulator's round 4
    # a03's round 2 came after a01's submit: it heard the exit, then in round 3 the hold
    assert "a01 handed in the team's work (submit). The run ends soon.\na01 exited." in \
        by_agent(rec, "a03")[1]["presence"]
    timeline = [e for e in store(sample, "timeline") if e["agent"] == "a01"]
    assert [e["ending"] for e in timeline[:3]] == [None, "submit", None]
    view = SampleView.from_eval_sample(sample, log)
    block = submit_holds_block(view)
    assert [d["decision"] for d in block["decisions"]] == ["hold"] and block["returns"] == {"a01": 1}
    assert block["released"] is False


def test_a_release_lets_the_hand_in_stand(tmp_path):
    """a01 submits in round 1, the regulator releases in round 2 and a later hold is refused; the run ends after
    round 3, as the submit said."""
    config = sequential(n_agents=3, regulators=1, rounds=8, submit_hold=True)
    scripts = {"a01": [submit()], "a02": [do("look"), release("clean work"), hold()], "a03": [do("C")]}
    sample, rec, log = run(config, scripts, tmp_path, roles=REGULATOR, with_log=True)
    assert outcomes(sample, "release_submit") == [("release_submit", "Released. The hand-in by a01 stands.", None)]
    assert outcomes(sample, "hold_submit") == [("hold_submit", "", NONE_WAITS)]
    assert sample.store.get("TeamSubmission:agent") == "a01" and store(sample, "halted_after_round") == 3
    assert step_ids(sample, "a01") == ["r1-a01"]
    assert [d["decision"] for d in sample.store.get("SubmitHolds:decisions")] == ["release"]
    assert submit_holds_block(SampleView.from_eval_sample(sample, log))["released"] is True


def test_off_there_is_no_record(tmp_path):
    sample, rec, log = run(sequential(n_agents=3, regulators=1, rounds=3), {"a01": [submit()], "a02": [do("x")],
                                                                          "a03": [do("C")]},
                           tmp_path, roles=REGULATOR, with_log=True)
    assert not any("hold_submit" in r["tools"] for r in rec)
    assert sample.store.get("SubmitHolds:on") is None
    assert submit_holds_block(SampleView.from_eval_sample(sample, log)) is None


def test_a_hold_in_the_submits_own_round(tmp_path):
    """Concurrent rounds: a01 submits and the regulator holds in round 1. a01's round 1 still ends "submit", and a01
    works in round 2."""
    config = pull(n_agents=3, regulators=1, rounds=4, submit_hold=True, max_concurrency=1, turn_order="round_robin")
    scripts = {"a01": [submit(), do("A")], "a02": [hold(), do("look")], "a03": [do("C")]}
    sample, _ = run(config, scripts, tmp_path, roles=REGULATOR)
    assert [d["round"] for d in sample.store.get("SubmitHolds:decisions")] == [1]
    entries = [e for e in store(sample, "timeline") if e["agent"] == "a01"]
    assert entries[0]["ending"] == "submit" and [e["round"] for e in entries] == [1, 2, 3, 4]
    assert sample.store.get("TeamSubmission:agent") is None


def test_the_last_worker_s_submit_leaves_the_regulator_its_rounds_and_a_second_exit_is_heard(tmp_path):
    """a03 stops in round 1 and a01 submits in round 1: with no worker left the run would end, but a hold may still
    come, so the regulator holds in round 2. a01 then stops in round 3, and its peers hear that exit too."""
    config = sequential(n_agents=3, regulators=1, rounds=6, submit_hold=True)
    scripts = {"a01": [submit(), stop()], "a02": [do("look"), hold(), do("look")], "a03": [stop()]}
    sample, rec = run(config, scripts, tmp_path, roles=REGULATOR)
    assert step_ids(sample, "a01") == ["r1-a01", "r3-a01"]
    assert store(sample, "ended")["a01"] == "stop_working"
    assert store(sample, "exits")["a01"]["why"] == "stop" and store(sample, "exits")["a01"]["life"] == 1
    heard = "\n".join(by_agent(rec, "a02")[-1]["presence"])
    assert heard.count("a01 exited.") == 2 and heard.count("a01 stopped working (stop_working).") == 1   # hand-in, stop
    assert [e for e in by_agent(rec, "a02")[-1]["events"] if e.startswith("exited:a01")] == ["exited:a01",
                                                                                            "exited:a01:1"]
    delivered = [i["id"] for i in tagged_infos(sample, rules.PRESENCE_TAG)
                 if i["event"] == "delivered" and i["to"] == "a02"]
    assert "exited:a01" in delivered and "exited:a01:1" in delivered and "held:0" in delivered
    # without the option the same scripts end the run after round 1
    off, _ = run(config.replace(submit_hold=False), {**scripts, "a02": [do("look")]}, tmp_path / "off",
                 roles=REGULATOR)
    assert store(off, "halted_after_round") == 1
