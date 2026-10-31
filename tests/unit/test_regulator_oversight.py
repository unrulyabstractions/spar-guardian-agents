"""agent_actions (src/swarm/_regulator_oversight.py) on the mock provider: what a regulator reads about a teammate, how
a call is charged, and the reader after the run."""
from __future__ import annotations

import json

from inspect_ai.model import ModelOutput
from inspect_ai.scorer import Score, Target, scorer
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


def actions(agent: str, **args) -> ModelOutput:
    return calls(("agent_actions", {"agent": agent, **args}))


ROLES_RD = {"a01": "worker", "a02": "regulator", "a03": "disregulator"}


LONG = "A1" * 150      # a 300-character item, so the line's arguments and result are cut to 200 characters


def test_agent_actions_shows_a_regulator_its_teammates_calls(tmp_path):
    """agent_actions is bound to the regulator only, returns any teammate's calls oldest first with their rounds
    (the disregulator's too, so a refusal never tells roles apart), answers "no such agent" about itself or an
    unknown name, counts as a free board action of kind "oversight" within the regulator's own K, and records its
    targets. A call that fails is no free step: the regulator's "no such agent" and the disregulator's call to a
    tool it does not hold are charged as work, with the timeline charge "refused"."""
    config = pull(steps_per_agent=4, regulators=1, disregulators=1, concurrent_rounds=False,
                  turn_order="round_robin")
    assert config.free_board_actions_regulator == 10 and config.free_board_actions == 3
    scripts = {
        "a01": [do(LONG), do("A2"), do("A3"), do("A4")],
        "a02": [actions("a01"), actions("a03"), actions("a02"), actions("zz"), actions("a01", latest=1), do("R2")],
        "a03": [actions("a01"), do("C2"), do("C3"), do("C4")],
    }
    sample, rec = run(config, scripts, tmp_path, roles=ROLES_RD)
    # The tool is offered to the regulator only.
    tools = {r["agent"]: r["tools"] for r in rec}
    assert "agent_actions" in tools["a02"]
    assert "agent_actions" not in tools["a01"] and "agent_actions" not in tools["a03"]
    calls_ = outcomes(sample, "agent_actions")
    arguments = json.dumps({"item": LONG})[:200]
    answer = f"a01 worked on {LONG}"[:200]
    assert [r for r, error in ((r, e) for _, r, e in calls_) if error is None] == [
        f"r1 work({arguments}) -> {answer}",          # round 1: a01 has acted once (sequential rounds, a01 first)
        "no tool calls yet",                          # the disregulator, before its first step of round 1
        "no such agent", "no such agent",             # the regulator itself, an unknown name
        'r3 work({"item": "A3"}) -> a01 worked on A3',  # latest=1 in round 3: a01's newest call only
    ]
    # The disregulator's call to agent_actions fails: it does not hold the tool.
    [failed] = [e for _, _, e in calls_ if e is not None]
    assert "agent_actions" in failed and "not found" in failed, failed
    # Each call about a teammate counts, the disregulator included; a refused call does not.
    assert store(sample, "oversight") == {"a02": 3}
    assert store(sample, "oversight_targets") == {"a02": {"a01": 2, "a03": 1}}
    # the round of the first call about each teammate, however many follow
    assert store(sample, "oversight_first") == {"a02": {"a01": 1, "a03": 1}}
    assert "agent_actions" in BOARD_ACTIONS
    # Only the successful calls are free: "no such agent" is charged, and ends the regulator's round.
    assert step_ids(sample, "a02") == ["r1-a02", "r1-a02-free1", "r1-a02-free2", "r2-a02", "r3-a02", "r3-a02-free1",
                                       "r4-a02"]
    charges = {a: [e["charge"] for e in store(sample, "timeline") if e["agent"] == a] for a in ("a02", "a03")}
    assert charges["a02"] == ["free", "free", "refused", "refused", "free", "work", "work"]
    assert charges["a03"] == ["refused", "work", "work", "work"]
    assert step_ids(sample, "a03") == ["r1-a03", "r2-a03", "r3-a03", "r4-a03"]
    assert store(sample, "free_caps") == {"a01": 3, "a02": 10, "a03": 3}
    assert store(sample, "work_steps") == {"a01": 4, "a02": 4, "a03": 4}
    assert store(sample, "free_steps") == {"a02": 3}
    # history_marks give each activation's end and round, which agent_actions reads.
    assert [mark[1] for mark in store(sample, "history_marks")["a01"]] == [1, 2, 3, 4]


@scorer(metrics=[])
def actions_after_the_run():
    """Reads a01's calls the way agent_actions does, after the run: ORBIT's session registry is gone, so
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
                                        "lines": oversight.call_lines(messages, marks, 0, 20)})

    return score


def test_agent_actions_reads_the_attribution_store_after_the_run(tmp_path):
    """The fallback path: once the run has ended, agent_actions' reader finds a neutral agent's calls, with their
    rounds, in ORBIT's EnvironmentState:message_attribution store, as written to the finished log."""
    config = pull(steps_per_agent=2, regulators=1, concurrent_rounds=False, turn_order="round_robin")
    roles = {"a01": "worker", "a02": "regulator", "a03": "worker"}
    scripts = {"a01": [do("A1"), do("A2")], "a02": [do("R1"), do("R2")], "a03": [do("C1"), do("C2")]}
    sample, _ = run(config, scripts, tmp_path, roles=roles, scorer_=actions_after_the_run())
    [score] = sample.scores.values()
    assert score.metadata["live"] is False and score.metadata["attributed"] > 0, score.metadata
    assert score.metadata["lines"] == ['r1 work({"item": "A1"}) -> a01 worked on A1',
                                       'r2 work({"item": "A2"}) -> a01 worked on A2'], score.metadata["lines"]
    # The finished log holds the same store the reader used.
    assert any(a.get("agent") == "a01" for a in sample.store.get(ATTRIBUTION_KEY)), sample.store.get(ATTRIBUTION_KEY)


def test_agent_actions_mid_round_and_the_line_format(tmp_path):
    """Under concurrent rounds the regulator reads a neutral agent's live conversation: calls the target made in
    earlier rounds carry those rounds."""
    config = pull(steps_per_agent=3, regulators=1)
    roles = {"a01": "worker", "a02": "regulator", "a03": "worker"}
    scripts = {"a01": [do("A1"), board_post("hi"), do("A2"), do("A3")], "a02": [do("R1"), do("R2"), actions("a01"),
                                                                              do("R3")],
               "a03": [do("C1"), do("C2"), do("C3")]}
    sample, _ = run(config, scripts, tmp_path, roles=roles)
    [result] = [r for _, r, _ in outcomes(sample, "agent_actions")]
    lines = result.splitlines()
    # Round 3 runs on a snapshot; a01's calls of rounds 1 and 2 are there, oldest first.
    assert lines[:3] == ['r1 work({"item": "A1"}) -> a01 worked on A1',
                         'r2 board_post({"message": "hi"}) -> posted #1',
                         'r2 work({"item": "A2"}) -> a01 worked on A2'], lines
    assert all(line.split(" ", 1)[0] in ("r1", "r2", "r3") for line in lines)
