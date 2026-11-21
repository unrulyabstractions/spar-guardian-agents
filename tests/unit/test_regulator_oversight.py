"""The regulator's oversight tools (src/swarm/_regulator_oversight.py), get_peer_actions and get_peer_observations, on
the mock provider: what a regulator reads about a teammate, how a call is charged, and the reader after the run."""
from __future__ import annotations

import json

from inspect_ai.model import ChatMessageAssistant, ChatMessageTool, ModelOutput
from inspect_ai.scorer import Score, Target, scorer
from inspect_ai.tool import ToolCall, ToolCallError
from inspect_ai.util import store_as
from orbit.execution.sessions import current_session_registry

from src.export.eval_sample_view import ATTRIBUTION_KEY
from src.swarm import _regulator_oversight as oversight
from src.swarm import turn_rules as rules
from src.swarm.turn_rules._turn_state import BOARD_ACTIONS
from tests.support.mock_model import calls
from tests.support.turn_rules_harness import (
    board_post,
    do,
    outcomes,
    pull,
    run,
    step_ids,
    store,
)

ACTIONS, OBSERVATIONS = "get_peer_actions", "get_peer_observations"


def actions(agent: str, **args) -> ModelOutput:
    return calls((ACTIONS, {"peer": agent, **args}))


def observations(agent: str, **args) -> ModelOutput:
    return calls((OBSERVATIONS, {"peer": agent, **args}))


ROLES_RD = {"a01": "worker", "a02": "regulator", "a03": "disregulator"}


LONG = "A1" * 150      # a 300-character item: arguments come back whole, a result past max_chars is cut


def test_get_peer_actions_shows_an_influencer_its_teammates_calls(tmp_path):
    """The peer tools are bound to both influencers, never to a worker. get_peer_actions returns any teammate's calls
    oldest first as JSON, each with its id, round, tool and whole arguments (an influencer's too, so a refusal never
    tells roles apart), and fails with "no such agent" about the caller itself or an unknown name. A call counts as a
    free board action of kind "oversight" within the caller's K, the same K for the regulator and the disregulator,
    and records its target. A call that fails is no free step: "no such agent" is charged as work, with the timeline
    charge "refused"."""
    config = pull(steps_per_agent=4, regulators=1, disregulators=1, concurrent_rounds=False,
                  turn_order="round_robin")
    assert config.free_board_actions_influencer == 10 and config.free_board_actions == 3
    scripts = {
        "a01": [do(LONG), do("A2"), do("A3"), do("A4")],
        "a02": [actions("a01"), actions("a03"), actions("a02"), actions("zz"), actions("a01", latest=1), do("R2")],
        "a03": [actions("a01"), do("C2"), do("C3"), do("C4")],
    }
    sample, rec = run(config, scripts, tmp_path, roles=ROLES_RD)
    # The tools are offered to the regulator and the disregulator, never to the worker.
    tools = {r["agent"]: r["tools"] for r in rec}
    for influencer in ("a02", "a03"):
        assert ACTIONS in tools[influencer] and OBSERVATIONS in tools[influencer]
    assert not {ACTIONS, OBSERVATIONS} & set(tools["a01"])
    calls_ = outcomes(sample, ACTIONS)
    answers = [json.loads(r) for _, r, error in calls_ if error is None]
    assert answers == [
        # round 1: a01 has acted once (sequential rounds, a01 first); its 300-character argument comes back whole
        {"peer": "a01", "total_calls": 1, "shown": 1,
         "actions": [{"id": 0, "round": 1, "tool": "work", "arguments": {"item": LONG}}]},
        # the disregulator, before its first step of round 1
        {"peer": "a03", "total_calls": 0, "shown": 0, "actions": []},
        # the disregulator's own query of a01, after the regulator's turn of round 1
        {"peer": "a01", "total_calls": 1, "shown": 1,
         "actions": [{"id": 0, "round": 1, "tool": "work", "arguments": {"item": LONG}}]},
        # latest=1 in round 3: a01's newest call only
        {"peer": "a01", "total_calls": 3, "shown": 1,
         "actions": [{"id": 2, "round": 3, "tool": "work", "arguments": {"item": "A3"}}]},
    ]
    errors = [e for _, _, e in calls_ if e is not None]
    assert errors == ["no such agent", "no such agent"]          # the regulator itself, an unknown name
    # Each call about a teammate counts, the disregulator's too; a refused call does not.
    assert store(sample, "oversight") == {"a02": 3, "a03": 1}
    assert store(sample, "oversight_targets") == {"a02": {"a01": 2, "a03": 1}, "a03": {"a01": 1}}
    # the round of the first call about each teammate, however many follow
    assert store(sample, "oversight_first") == {"a02": {"a01": 1, "a03": 1}, "a03": {"a01": 1}}
    assert ACTIONS in BOARD_ACTIONS and OBSERVATIONS in BOARD_ACTIONS
    # Only the successful calls are free: "no such agent" is charged, and ends the regulator's round.
    assert step_ids(sample, "a02") == ["r1-a02", "r1-a02-free1", "r1-a02-free2", "r2-a02", "r3-a02", "r3-a02-free1",
                                       "r4-a02"]
    charges = {a: [e["charge"] for e in store(sample, "timeline") if e["agent"] == a] for a in ("a02", "a03")}
    assert charges["a02"] == ["free", "free", "refused", "refused", "free", "work", "work"]
    # The disregulator's query is free and its follow-up step comes in the same round, within its K of 10.
    assert charges["a03"] == ["free", "work", "work", "work", "work"]
    assert step_ids(sample, "a03") == ["r1-a03", "r1-a03-free1", "r2-a03", "r3-a03", "r4-a03"]
    assert store(sample, "free_caps") == {"a01": 3, "a02": 10, "a03": 10}
    assert store(sample, "work_steps") == {"a01": 4, "a02": 4, "a03": 4}
    assert store(sample, "free_steps") == {"a02": 3, "a03": 1}
    # history_marks give each activation's end and round, which the oversight tools read.
    assert [mark[1] for mark in store(sample, "history_marks")["a01"]] == [1, 2, 3, 4]


def test_get_peer_observations_returns_results_by_id_cut_with_the_cut_stated(tmp_path):
    """get_peer_observations returns what a teammate's calls returned: by id in the order asked, else the newest,
    each cut to max_chars with chars, shown_chars and truncated; an id past the teammate's calls is "no such call". A
    call counts as oversight and is a free step."""
    config = pull(steps_per_agent=2, regulators=1, concurrent_rounds=False, turn_order="round_robin")
    roles = {"a01": "worker", "a02": "regulator", "a03": "worker"}
    scripts = {"a01": [do(LONG), do("A2")],
               "a02": [observations("a01", ids=[0, 7], max_chars=20), observations("a01", latest=1), do("R1"),
                       do("R2")],
               "a03": [do("C1"), do("C2")]}
    sample, _ = run(config, scripts, tmp_path, roles=roles)
    answers = [json.loads(r) for _, r, error in outcomes(sample, OBSERVATIONS) if error is None]
    full = f"a01 worked on {LONG}"
    assert answers[0] == {"peer": "a01", "observations": [
        {"id": 0, "round": 1, "tool": "work", "status": "ok", "result": full[:20], "chars": len(full),
         "shown_chars": 20, "truncated": True},
        {"id": 7, "status": "no such call"}]}
    assert answers[1] == {"peer": "a01", "observations": [
        {"id": 0, "round": 1, "tool": "work", "status": "ok", "result": full, "chars": len(full),
         "shown_chars": len(full), "truncated": False}]}
    assert store(sample, "oversight") == {"a02": 2}
    assert [e["charge"] for e in store(sample, "timeline") if e["agent"] == "a02"][:2] == ["free", "free"]


def test_the_payloads_state_status_and_cuts():
    """The pure parts: ids from 0 in call order, an error's message and a pending call's null result, since_round."""
    messages = [
        ChatMessageAssistant(content="", tool_calls=[ToolCall(id="c0", function="shell", arguments={"cmd": "ls"}),
                                                     ToolCall(id="c1", function="shell", arguments={"cmd": "cat x"})]),
        ChatMessageTool(content="a b", tool_call_id="c0"),
        ChatMessageTool(content="", tool_call_id="c1", error=ToolCallError("unknown", "no such file")),
        ChatMessageAssistant(content="", tool_calls=[ToolCall(id="c2", function="board_read", arguments={})]),
    ]
    got = oversight.called(messages, [[3, 1]], 2)
    assert [(c.id, c.round, c.tool, c.status, c.result) for c in got] == [
        (0, 1, "shell", "ok", "a b"), (1, 1, "shell", "error", "no such file"), (2, 2, "board_read", "pending", None)]
    assert oversight.actions_payload("a01", got, 20, 2)["actions"] == [
        {"id": 2, "round": 2, "tool": "board_read", "arguments": {}}]
    rows = oversight.observations_payload("a01", got, None, 20, 4000)["observations"]
    assert rows[2] == {"id": 2, "round": 2, "tool": "board_read", "status": "pending", "result": None}
    assert rows[1]["status"] == "error" and rows[1]["result"] == "no such file"


@scorer(metrics=[])
def actions_after_the_run():
    """Reads a01's calls the way the oversight tools do, after the run: ORBIT's session registry is gone, so
    oversight.conversation falls back to the sample's attributed conversation."""
    async def score(state, target: Target) -> Score:
        try:
            current_session_registry()
            live = True
        except RuntimeError:
            live = False
        messages = oversight.conversation("a01")
        marks = store_as(rules.TurnState).history_marks.get("a01", [])
        return Score(value=0, metadata={"live": live, "attributed": len(state.store.get(ATTRIBUTION_KEY) or []),
                                        "actions": oversight.actions_payload(
                                            "a01", oversight.called(messages, marks, 0), 20, None)["actions"]})

    return score


def test_the_oversight_reader_reads_the_attribution_store_after_the_run(tmp_path):
    """The fallback path: once the run has ended, the oversight tools' reader finds a neutral agent's calls, with their
    rounds, in ORBIT's EnvironmentState:message_attribution store, as written to the finished log."""
    config = pull(steps_per_agent=2, regulators=1, concurrent_rounds=False, turn_order="round_robin")
    roles = {"a01": "worker", "a02": "regulator", "a03": "worker"}
    scripts = {"a01": [do("A1"), do("A2")], "a02": [do("R1"), do("R2")], "a03": [do("C1"), do("C2")]}
    sample, _ = run(config, scripts, tmp_path, roles=roles, scorer_=actions_after_the_run())
    [score] = sample.scores.values()
    assert score.metadata["live"] is False and score.metadata["attributed"] > 0, score.metadata
    assert score.metadata["actions"] == [{"id": 0, "round": 1, "tool": "work", "arguments": {"item": "A1"}},
                                         {"id": 1, "round": 2, "tool": "work", "arguments": {"item": "A2"}}]
    # The finished log holds the same store the reader used.
    assert any(a.get("agent") == "a01" for a in sample.store.get(ATTRIBUTION_KEY)), sample.store.get(ATTRIBUTION_KEY)


def test_get_peer_actions_mid_round(tmp_path):
    """Under concurrent rounds the regulator reads a neutral agent's live conversation: calls the target made in
    earlier rounds carry those rounds."""
    config = pull(steps_per_agent=3, regulators=1)
    roles = {"a01": "worker", "a02": "regulator", "a03": "worker"}
    scripts = {"a01": [do("A1"), board_post("hi"), do("A2"), do("A3")], "a02": [do("R1"), do("R2"), actions("a01"),
                                                                              do("R3")],
               "a03": [do("C1"), do("C2"), do("C3")]}
    sample, _ = run(config, scripts, tmp_path, roles=roles)
    [result] = [r for _, r, _ in outcomes(sample, ACTIONS)]
    got = json.loads(result)["actions"]
    # Round 3 runs on a snapshot; a01's calls of rounds 1 and 2 are there, oldest first.
    assert got[:3] == [{"id": 0, "round": 1, "tool": "work", "arguments": {"item": "A1"}},
                       {"id": 1, "round": 2, "tool": "board_post", "arguments": {"message": "hi"}},
                       {"id": 2, "round": 2, "tool": "work", "arguments": {"item": "A2"}}], got
    assert all(c["round"] in (1, 2, 3) for c in got)
