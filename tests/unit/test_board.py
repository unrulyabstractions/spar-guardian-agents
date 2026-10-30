"""The pull board (src/swarm/board.py) on the mock provider: which agent holds which board tool, what board_read
selects, and what the read and post budgets refuse."""
from __future__ import annotations

from src.swarm import board
from tests.support.turn_rules_harness import (
    PROMPTS,
    board_post,
    do,
    outcomes,
    pull,
    read,
    run,
    status,
    store,
    submit,
    tool_results,
)


def test_tools_by_mode_and_role():
    roles = {"a01": "worker", "a02": "regulator", "a03": "disregulator"}
    solo = board.agent_tool_names(pull(mode="solo_task"), roles)
    assert {n: solo[n] for n in ("a01", "a03")} == {n: ["board_status", "board_read", "board_post"]
                                                     for n in ("a01", "a03")}
    shared = board.agent_tool_names(pull(mode="shared_task"), roles)
    # agent_actions goes to the regulator only, never to a worker or a disregulator.
    assert shared["a01"] == shared["a03"] == ["board_status", "board_read", "board_post"]
    assert shared["a02"] == solo["a02"] == ["board_status", "board_read", "board_post", "agent_actions"]
    assert "agent_actions" in board.board_bindings(pull(), roles=roles)
    assert "team_submit" not in board.board_bindings(pull(), roles=roles)
    # A lone agent gets no board tools.
    assert board.agent_tool_names(pull(n_agents=1), None) == {"a01": []}
    assert board.board_bindings(pull(n_agents=1)) == {}


def test_the_systems_notice_names_no_sender_in_the_read_records(tmp_path):
    """A teammate who reads the system's notice of the hand-in reads no agent, so read_senders and the read graph
    name agents only."""
    config = pull(steps_per_agent=3, mode="shared_task", concurrent_rounds=False, turn_order="round_robin")
    sample, _ = run(config, {"a01": [submit("ready")], "a02": [read()], "a03": [do("C")]}, tmp_path)
    assert [m["sender"] for m in sample.store.get("CommunicationState:messages")] == ["system"]
    assert store(sample, "reads")["a02"] >= 1 and store(sample, "read_senders") == {}


def test_board_read_selection(tmp_path):
    """latest, from_agent and latest_from select posts, oldest first, at most read_page in all."""
    config = pull(steps_per_agent=4, read_page=3, free_board_actions=0, concurrent_rounds=False,
                  turn_order="round_robin")
    scripts = {
        "a01": [board_post("p1"), board_post("p3"), board_post("p5"), read(latest=10)],
        "a02": [board_post("p2"), board_post("p4"), read(from_agent="a01", latest_from=2),
                read(latest=1, from_agent="a01", latest_from=1)],
        "a03": [do("C"), read(), status(), do("C")],
    }
    sample, _ = run(config, scripts, tmp_path)
    results = [(f, r) for f, r in tool_results(sample) if f in ("board_read", "board_status")]
    assert results == [
        # a03, round 2, no arguments: the newest read_page posts.
        ("board_read", "[a02 #2] p2\n[a01 #3] p3\n[a02 #4] p4"),
        # a02, round 3: a01's newest two.
        ("board_read", "[a01 #3] p3\n[a01 #5] p5"),
        # a03, round 3: counts by sender; "new" is above its read cursor (it read up to #4), own posts never count.
        ("board_status", "a01: 3 posts (1 new)\na02: 2 posts (0 new)\ntotal 5, 1 new since your last read"),
        # a01, round 4: latest=10 is capped at read_page.
        ("board_read", "[a01 #3] p3\n[a02 #4] p4\n[a01 #5] p5"),
        # a02, round 4: the union of the newest post and a01's newest, deduplicated.
        ("board_read", "[a01 #5] p5"),
    ]
    # A read counts the posts returned by sender, the reader's own left out.
    assert store(sample, "read_senders") == {"a03": {"a01": 1, "a02": 2}, "a02": {"a01": 3}, "a01": {"a02": 1}}
    assert store(sample, "discovery_round") == {"a03": 2, "a02": 3, "a01": 4}


def test_regulators_read_without_a_cap_and_the_budgets_refuse_and_reset(tmp_path):
    """A worker's board_read returns at most what is left of its read budget for the round and is refused when
    none is left; board_post past the post budget is refused and sends nothing; both reset at the next round. A
    regulator reads the whole board, past read_page and past the budget."""
    config = pull(steps_per_agent=2, regulators=1, read_page=2, reads_per_turn=3, posts_per_turn=2,
                  free_board_actions=3, concurrent_rounds=False, turn_order="round_robin")
    roles = {"a01": "worker", "a02": "worker", "a03": "regulator"}
    scripts = {
        "a01": [board_post("p1"), board_post("p2"), board_post("p3"), board_post("p4"), do("A2")],
        "a02": [read(), read(latest=2), read(), read(latest=1), do("B2")],
        "a03": [do("R1"), read(), read(latest=10), do("R2")],
    }
    sample, _ = run(config, scripts, tmp_path, roles=roles)
    budget = PROMPTS["budget"]
    assert outcomes(sample, "board_post", "board_read") == [
        # round 1: a01 posts twice; the third post is refused.
        ("board_post", "posted #1", None), ("board_post", "posted #2", None),
        ("board_post", "", budget["post_refused"].format(used=2)),
        # a02 receives two posts, then one of the two it selected (the budget of three is spent), then nothing.
        ("board_read", "[a01 #1] p1\n[a01 #2] p2", None),
        ("board_read", "[a01 #2] p2\n" + budget["read_cut"].format(cut=1), None),
        ("board_read", "", budget["read_refused"].format(used=3)),
        # round 2: the budgets reset.
        ("board_post", "posted #3", None),
        ("board_read", "[a01 #3] p4", None),
        # the regulator: every post with no selection, past read_page 2, and again past the read budget of 3.
        ("board_read", "[a01 #1] p1\n[a01 #2] p2\n[a01 #3] p4", None),
        ("board_read", "[a01 #1] p1\n[a01 #2] p2\n[a01 #3] p4", None),
    ]
    assert [(m["sender"], m["content"]) for m in sample.store.get("CommunicationState:messages")] == [
        ("a01", "p1"), ("a01", "p2"), ("a01", "p4")]
    assert store(sample, "posts_refused") == {"a01": 1} and store(sample, "reads_refused") == {"a02": 1}
    assert store(sample, "posts") == {"a01": 3} and store(sample, "reads") == {"a02": 3, "a03": 2}
    assert store(sample, "turn_posts") == {"a01": [2, 1]} and store(sample, "turn_reads") == {"a02": [2, 1]}
    assert (store(sample, "reads_per_turn"), store(sample, "posts_per_turn")) == (3, 2)
    # A refused call is no free step: it is charged as work ("refused") and ends the agent's round.
    charges = {a: [(e["step"], e["charge"]) for e in store(sample, "timeline") if e["agent"] == a]
               for a in ("a01", "a02")}
    assert charges["a01"] == [("r1-a01", "free"), ("r1-a01-free1", "free"), ("r1-a01-free2", "refused"),
                              ("r2-a01", "free"), ("r2-a01-free1", "work")]
    assert charges["a02"] == [("r1-a02", "free"), ("r1-a02-free1", "free"), ("r1-a02-free2", "refused"),
                              ("r2-a02", "free"), ("r2-a02-free1", "work")]
    assert store(sample, "work_steps")["a01"] == store(sample, "work_steps")["a02"] == 2
