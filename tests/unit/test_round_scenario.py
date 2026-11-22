"""The rounds smoke scenario's script and its transcript reader (tests/support/round_scenario.py), with no sandbox."""
from __future__ import annotations

import pytest

from src.swarm.turn_rules._turn_state import END_ROUND, PRESENCE_TAG, ROUND_TAG
from tests.support import round_scenario
from tests.support.atif_builder import transcript


def test_the_script_holds_each_round_and_one_entry_past_the_plan():
    script, plan = round_scenario.rounds_script(["a01", "a02", "a03"], "a04")
    assert sorted(script) == sorted(plan) == ["a01", "a02", "a03", "a04"]
    for agent, rounds in plan.items():
        assert len(script[agent]) == sum(len(r["outputs"]) for r in rounds) + 1, agent
        assert [r["used"] for r in rounds] == sorted(r["used"] for r in rounds), agent
    # w1 asks for one call more than N in one reply; w3's first round is a reply with no tool call
    assert len(plan["a01"][1]["calls"]) == round_scenario.CALLS + 1 and len(plan["a01"][1]["outputs"]) == 1
    assert plan["a03"][0]["calls"] == [] and plan["a03"][0]["end"] == "no_tool_call"
    # every round uses one up: each agent has R rounds, the regulator's all work and the workers' last a stop
    assert all([r["used"] for r in rounds] == list(range(1, round_scenario.ROUNDS + 1)) for rounds in plan.values())
    assert all(r["calls"][-1][0] == END_ROUND for r in plan["a04"])


def _user(text: str, tag: str | None = None, value: dict | None = None) -> dict:
    return {"source": "user", "message": text, **({"extra": {tag: value}} if tag else {})}


def _agent(cid: str) -> dict:
    return {"source": "agent", "message": "", "tool_calls": [{"tool_call_id": cid, "function_name": "shell",
                                                                "arguments": {}}]}


def test_round_groups_cut_at_each_line_with_what_was_heard_before_the_first_call():
    t = transcript([])
    t["trajectory"]["steps"] = [
        {"source": "system", "message": "s"}, _user("task"),
        _user("Round 1 of 3.", ROUND_TAG, {"round": 1, "of": 3}),
        _user("a02 entered.", PRESENCE_TAG, {"kind": "entered"}),
        _agent("c1"), _user("[Since your last call] the board changed."), _agent("c2"),
        _user("a02 exited.", PRESENCE_TAG, {"kind": "exited"}),
        _user("Round 2 of 3.", ROUND_TAG, {"round": 2, "of": 3}),
        _agent("c3")]
    groups = round_scenario.round_groups(t)
    assert [g["line"] for g in groups] == ["Round 1 of 3.", "Round 2 of 3."]
    assert [g["heard"] for g in groups] == [["a02 entered."], ["a02 exited."]]
    assert groups[0]["notices"] == ["[Since your last call] the board changed."] and groups[1]["notices"] == []
    assert [[s["tool_calls"][0]["tool_call_id"] for s in g["steps"]] for g in groups] == [["c1", "c2"], ["c3"]]


def test_round_groups_refuse_a_user_line_of_no_round():
    t = transcript([])
    t["trajectory"]["steps"] = [{"source": "system", "message": "s"}, _user("task"),
                                _user("Round 1 of 3.", ROUND_TAG, {"round": 1}), _user("Please continue.")]
    with pytest.raises(AssertionError):
        round_scenario.round_groups(t)
