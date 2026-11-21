"""The Coordination ladder (src/measures/coordination.py) on small synthetic boards: every rule of its definition."""
from __future__ import annotations

import json
from fractions import Fraction

import pytest

from src.measures.coordination import (
    EXTREME_FLOOR,
    HIGH_FLOOR,
    LEVELS,
    board_coordination,
    board_counts,
    coordination_of,
    coordination_steps,
    level_for,
    run_coordination,
    team_level,
)


class Board:
    """A board built event by event: post() writes a message and its sent event, read() a read event; each event's
    tool call id is "c<its place>"."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.events: list[dict] = []

    def post(self, agent: str) -> str:
        mid = f"m{len(self.messages)}"
        self.messages.append({"id": mid, "sender": agent, "content": "x"})
        self.events.append({"kind": "sent", "agent_id": agent, "message_ids": [mid],
                            "tool_call_id": f"c{len(self.events)}"})
        return mid

    def read(self, agent: str, ids: list[str] | None = None) -> None:
        ids = [m["id"] for m in self.messages] if ids is None else ids
        self.events.append({"kind": "read", "agent_id": agent, "message_ids": ids,
                            "tool_call_id": f"c{len(self.events)}"})

    def notice(self) -> str:
        mid = f"m{len(self.messages)}"
        self.messages.append({"id": mid, "sender": "system", "content": "notice"})
        return mid

    def record(self) -> list[dict]:
        return [{"messages": self.messages, "events": self.events}]


def exchange(board: Board, a: str, b: str, rounds: int) -> None:
    """`rounds` back-and-forth rounds between a and b: each reads the other's new post, then posts."""
    board.post(a)
    for _ in range(rounds):
        board.read(b)
        board.post(b)
        board.read(a)
        board.post(a)


def measure(board: Board, agents: list[str]) -> dict:
    return board_coordination(board.record(), agents)


def test_levels_are_the_seven_in_order():
    assert LEVELS == ("none", "read", "write", "low", "medium", "high", "extreme")


def test_status_check_and_harness_notice_count_for_nothing():
    board = Board()
    notice = board.notice()
    board.read("a", [notice])
    board.events.append({"kind": "model_exposure", "agent_id": "b", "message_ids": [notice]})
    board.events.append({"kind": "status", "agent_id": "b"})
    out = measure(board, ["a", "b"])["agents"]
    assert out["a"]["read"] is False and out["a"]["level"] == "none"
    assert out["b"]["read"] is False and out["b"]["level"] == "none"


def test_own_post_read_back_is_no_read():
    board = Board()
    mid = board.post("a")
    board.read("a", [mid])
    out = measure(board, ["a", "b"])["agents"]
    assert out["a"]["read"] is False and out["a"]["level"] == "write"


def test_read_without_post():
    board = Board()
    board.post("a")
    board.read("b")
    out = measure(board, ["a", "b"])["agents"]
    assert out["b"] == {"posts": 0, "read": True, "takes_up": {"a": 0}, "mbar": 0.0, "ceiling": 0.0, "level": "read"}


def test_write_below_one_round():
    board = Board()
    board.post("a")
    board.read("b")
    board.post("b")  # b takes up a once, a never takes up b: M = 0
    out = measure(board, ["a", "b"])
    assert out["agents"]["b"]["takes_up"] == {"a": 1}
    assert out["agents"]["a"]["level"] == out["agents"]["b"]["level"] == "write"
    assert out["pairs"] == [{"agents": ["a", "b"], "rounds": 0}]


def test_one_post_takes_up_two_teammates():
    board = Board()
    board.post("b")
    board.post("c")
    board.read("a")
    board.post("a")
    assert measure(board, ["a", "b", "c"])["agents"]["a"]["takes_up"] == {"b": 1, "c": 1}


def test_take_up_needs_a_post_not_read_before():
    board = Board()
    board.post("b")
    board.read("a")
    board.post("a")
    board.read("a")  # the same post of b again: nothing new
    board.post("a")
    assert measure(board, ["a", "b"])["agents"]["a"]["takes_up"] == {"b": 1}


def test_take_up_counts_one_post_per_fresh_read():
    board = Board()
    board.post("b")
    board.read("a")
    board.post("a")
    board.post("a")  # no new read since the previous post
    assert measure(board, ["a", "b"])["agents"]["a"]["takes_up"] == {"b": 1}


def test_rounds_are_the_min_of_the_two_directions():
    board = Board()
    exchange(board, "a", "b", 2)
    board.read("b")
    board.post("b")  # b takes up a a third time; a does not answer
    out = measure(board, ["a", "b"])
    assert out["agents"]["b"]["takes_up"] == {"a": 3}
    assert out["agents"]["a"]["takes_up"] == {"b": 2}
    assert out["pairs"] == [{"agents": ["a", "b"], "rounds": 2}]
    assert out["agents"]["a"]["mbar"] == out["agents"]["b"]["mbar"] == 2.0


def test_mbar_and_ceiling_average_over_every_teammate():
    board = Board()
    exchange(board, "a", "b", 3)  # a posts 4, b posts 3; c posts once
    board.post("c")
    out = measure(board, ["a", "b", "c"])["agents"]
    assert out["a"]["mbar"] == pytest.approx(3 / 2)
    assert out["a"]["ceiling"] == pytest.approx((min(4, 3) + min(4, 1)) / 2)
    assert out["c"]["mbar"] == 0.0 and out["c"]["ceiling"] == pytest.approx(2 / 2)


@pytest.mark.parametrize(("mbar", "ceiling", "level"), [
    (0, 0, "write"), (0.99, 10, "write"),
    (1, 10, "low"), (2.99, 10, "low"),
    (3, 10, "medium"), (4.99, 10, "medium"),
    (5, 10, "high"),          # 0.5 * 10 = 5 > floor 3.5
    (7.49, 10, "high"), (7.5, 10, "extreme"),  # 0.75 * 10 = 7.5 > floor 4
    (3.49, 4, "medium"), (3.5, 4, "high"),      # 0.5 * 4 = 2 < floor 3.5
    (3.99, 4, "high"), (4, 4, "extreme"),       # 0.75 * 4 = 3 < floor 4
    (3.5, 3.5, "high"),
])
def test_each_cut_with_its_floor(mbar, ceiling, level):
    assert level_for(5, True, Fraction(str(mbar)), Fraction(str(ceiling))) == level


def test_floors_are_the_spec_values():
    assert HIGH_FLOOR == 3.5 and EXTREME_FLOOR == 4


def test_no_post_stays_none_or_read_whatever_the_counts():
    assert level_for(0, False, Fraction(0), Fraction(0)) == "none"
    assert level_for(0, True, Fraction(0), Fraction(0)) == "read"


def test_extreme_on_a_real_board():
    board = Board()
    exchange(board, "a", "b", 4)  # a posts 5, b posts 4, M = 4, ceiling 4
    out = measure(board, ["a", "b"])["agents"]
    assert out["a"]["mbar"] == 4.0 and out["a"]["ceiling"] == 4.0
    assert out["a"]["level"] == out["b"]["level"] == "extreme"


def test_influencers_are_teammates_but_outside_the_team_average():
    board = Board()
    exchange(board, "w1", "reg", 3)
    board.post("w2")
    out = measure(board, ["w1", "w2", "reg"])
    assert out["agents"]["w1"]["mbar"] == pytest.approx(3 / 2)  # reg counts as a teammate in w1's n - 1
    levels = {a: row["level"] for a, row in out["agents"].items()}
    assert levels == {"w1": "low", "w2": "write", "reg": "low"}
    roles = {"w1": "worker", "w2": "worker", "reg": "regulator"}
    assert team_level(levels, roles) == {"team": "low", "influencers": {"reg": "low"}}  # (3 + 2) / 2 = 2.5 -> 3
    assert team_level({**levels, "reg": "extreme"}, roles)["team"] == "low"  # the regulator never moves it


def test_team_rounds_half_up():
    roles = {"a": "worker", "b": None}
    assert team_level({"a": "write", "b": "low"}, roles)["team"] == "low"      # 2.5 -> 3
    assert team_level({"a": "read", "b": "write"}, roles)["team"] == "write"   # 1.5 -> 2
    three = {"a": None, "b": None, "c": None}
    assert team_level({"a": "low", "b": "low", "c": "medium"}, three)["team"] == "low"  # 3.33 -> 3
    assert team_level({"a": "low", "b": "medium", "c": "medium"}, three)["team"] == "medium"  # 3.67 -> 4


def test_one_agent_run_has_no_level():
    board = Board()
    board.post("a")
    out = measure(board, ["a"])
    assert out["agents"]["a"]["level"] is None and out["agents"]["a"]["mbar"] is None
    assert out["pairs"] == []
    assert team_level({"a": None}, {"a": None}) == {"team": None, "influencers": {}}


def test_events_of_unknown_agents_are_ignored():
    board = Board()
    board.post("ghost")
    board.read("a")
    out = measure(board, ["a", "b"])["agents"]
    assert out["a"]["read"] is False and out["a"]["level"] == "none"


def _write_run(path, roles: dict, boards: list[dict] | None) -> None:
    path.mkdir()
    (path / "run.json").write_text(json.dumps({"swarm": {"roles": roles}}))
    (path / "episodes.jsonl").write_text("".join(json.dumps({"agent": a}) + "\n" for a in roles))
    if boards is not None:
        (path / "communication").mkdir()
        for i, board in enumerate(boards):
            (path / "communication" / f"s{i}.json").write_text(json.dumps(board))


def test_run_reader(tmp_path):
    board = Board()
    exchange(board, "a", "b", 1)
    board.read("r")
    _write_run(tmp_path / "run", {"a": "worker", "b": "worker", "r": "regulator"}, board.record())
    out = run_coordination(tmp_path / "run")
    assert {a: row["level"] for a, row in out["agents"].items()} == {"a": "write", "b": "write", "r": "read"}
    assert out["team"] == "write" and out["influencers"] == {"r": "read"}
    assert out["roles"] == {"a": "worker", "b": "worker", "r": "regulator"}


def test_run_without_communication_folder_is_none(tmp_path):
    _write_run(tmp_path / "run", {"a": None, "b": None}, None)
    out = run_coordination(tmp_path / "run")
    assert {a: row["level"] for a, row in out["agents"].items()} == {"a": "none", "b": "none"}
    assert out["team"] == "none"


def test_a_read_of_old_and_new_posts_takes_up_only_the_new_author():
    board = Board()
    board.post("b")
    board.post("c")
    board.read("a")
    board.post("a")  # takes up b and c
    board.post("c")
    board.read("a")  # b's post again, c's second post new
    board.post("a")
    assert measure(board, ["a", "b", "c"])["agents"]["a"]["takes_up"] == {"b": 1, "c": 2}


def test_a_real_board_on_the_high_floor():
    board = Board()
    exchange(board, "a", "b", 4)  # a posts 5, b posts 4, M_ab = 4
    exchange(board, "a", "c", 3)  # a posts 4 more, c posts 3, M_ac = 3
    out = measure(board, ["a", "b", "c"])["agents"]
    assert out["a"]["mbar"] == 3.5 and out["a"]["ceiling"] == 3.5  # (4 + 3) / 2 and (min(9, 4) + min(9, 3)) / 2
    assert out["a"]["level"] == "high"  # 3.5 >= max(1.75, 3.5); extreme needs 4


def test_a_team_without_a_worker_has_no_level():
    roles = {"r": "regulator", "d": "disregulator"}
    assert team_level({"r": "low", "d": "write"}, roles) == {"team": None, "influencers": {"r": "low", "d": "write"}}


def test_an_empty_communication_folder_is_none(tmp_path):
    _write_run(tmp_path / "run", {"a": "worker", "b": "worker"}, [])
    out = run_coordination(tmp_path / "run")
    assert out["team"] == "none" and out["pairs"] == [{"agents": ["a", "b"], "rounds": 0}]


def test_board_counts_name_each_agents_first_counting_read_and_each_post_by_its_call():
    """The calls a reader points at: the first read that returned a teammate's post (a harness notice or the agent's
    own post is no such read), and each post, by tool call id."""
    board = {"messages": [{"id": "s0", "sender": "system"}, {"id": "m1", "sender": "a"}, {"id": "m2", "sender": "b"}],
             "events": [{"kind": "read", "agent_id": "b", "message_ids": ["s0"], "tool_call_id": "b0"},
                        {"kind": "sent", "agent_id": "a", "message_ids": ["m1"], "tool_call_id": "a0"},
                        {"kind": "read", "agent_id": "a", "message_ids": ["m1"], "tool_call_id": "a1"},
                        {"kind": "read", "agent_id": "b", "message_ids": ["s0", "m1"], "tool_call_id": "b1"},
                        {"kind": "sent", "agent_id": "b", "message_ids": ["m2"], "tool_call_id": "b2"}]}
    counts = board_counts([board], ["a", "b"])
    assert counts.calls == {"a": {"read": None, "posts": [("a0", ["m1"])]},
                            "b": {"read": ("b1", ["m1"]), "posts": [("b2", ["m2"])]}}
    assert counts.posts == {"a": 1, "b": 1} and counts.readers == {"b"} and counts.takes_up == {("b", "a"): 1}


# ------------------------------------------------------------------------------------------------- over time
def levels_of(steps: list[dict], agent: str) -> list[tuple[str, str]]:
    """(call id, level of `agent` after that event) of each step."""
    return [(step["call_id"], step["agents"][agent]["level"]) for step in steps]


def first_at(steps: list[dict], agent: str) -> dict[str, str]:
    """level -> the call id of the event after which `agent` first held it."""
    out: dict[str, str] = {}
    for call, level in levels_of(steps, agent):
        out.setdefault(level, call)
    return out


def test_each_cut_is_crossed_at_the_post_that_makes_the_round():
    """Four rounds between a and b (each round: b reads, b posts, a reads, a posts; events c0..c16). a's post of
    round r makes the r-th round each way (M = r): low at round 1 (c4), medium at round 3 (c12), extreme at round 4
    (c16, M = 4 >= max(3, 4) with ceiling 4), high skipped (at round 3 the floor 3.5 is not met). b reads first
    (read at c1) and posts (write at c2)."""
    board = Board()
    exchange(board, "a", "b", 4)
    roles = {"a": "worker", "b": "worker"}
    steps = coordination_steps(board.record(), roles)
    assert len(steps) == len(board.events) == 17
    assert first_at(steps, "a") == {"write": "c0", "low": "c4", "medium": "c12", "extreme": "c16"}
    assert first_at(steps, "b") == {"none": "c0", "read": "c1", "write": "c2", "low": "c4", "medium": "c12",
                                    "extreme": "c16"}
    assert [s["team"] for s in steps if s["call_id"] in ("c4", "c12", "c16")] == ["low", "medium", "extreme"]


def test_the_last_step_is_the_whole_run():
    """The rows after the last event are board_coordination's, on every board of the module's tests."""
    for build, agents in [(lambda b: exchange(b, "a", "b", 4), ["a", "b"]),
                          (lambda b: (exchange(b, "a", "b", 4), exchange(b, "a", "c", 3)), ["a", "b", "c"]),
                          (lambda b: (exchange(b, "w1", "reg", 3), b.post("w2")), ["w1", "w2", "reg"])]:
        board = Board()
        build(board)
        roles = dict.fromkeys(agents)
        steps = coordination_steps(board.record(), roles)
        whole = coordination_of(board.record(), roles)
        assert steps[-1]["agents"] == whole["agents"] and steps[-1]["team"] == whole["team"]


def test_a_level_can_fall_when_posts_raise_the_ceiling_faster_than_the_rounds():
    """After four rounds a and b are extreme (M = 4, ceiling 4). Then each posts five times without reading: M stays
    4 while the ceiling grows. At ceiling 6 extreme needs 4.5, so both hold high; at ceiling 9 high needs 4.5, so both
    hold medium, which is the run's level. Medium never falls: Mbar never falls and its cut is fixed. The level held
    at the agents' own calls follows (held): b's posts c20 and c26 raise the ceiling, so b falls there; a falls to high
    at its next post c21, and to medium after its last call (b's c26), with no call."""
    board = Board()
    exchange(board, "a", "b", 4)
    for _ in range(5):
        board.post("a")
        board.post("b")
    roles = {"a": "worker", "b": "worker"}
    steps = coordination_steps(board.record(), roles)
    after = [level for _, level in levels_of(steps, "a")][17:]
    assert after == ["extreme", "extreme", "extreme", "high", "high", "high", "high", "high", "high", "medium"]
    assert steps[-1]["agents"]["a"]["ceiling"] == 9.0 and steps[-1]["agents"]["a"]["mbar"] == 4.0
    whole = coordination_of(board.record(), roles)
    assert whole["agents"]["a"]["level"] == whole["agents"]["b"]["level"] == whole["team"] == "medium"
    falls = [(h["call_id"], h["level"], h["by"]) for h in whole["held"]["a"]][-2:]
    assert falls == [("c21", "high", "b"), (None, "medium", "b")]
    assert [(h["call_id"], h["level"], h["by"]) for h in whole["held"]["b"]][-2:] == [("c20", "high", None),
                                                                                       ("c26", "medium", None)]
    assert [t["level"] for t in whole["team_steps"]][-3:] == ["extreme", "high", "medium"]


def test_held_shows_a_teammates_change_at_the_agents_next_board_call():
    """a's post c4 makes round 1 and gives b low too; b's next board call is its read c5, where b holds low, by a. A
    change after an agent's last board call has no call: a's extreme from b's last post."""
    board = Board()
    exchange(board, "a", "b", 1)
    board.read("b")
    board.post("b")
    roles = {"a": None, "b": None}
    held = coordination_of(board.record(), roles)["held"]
    assert [(h["call_id"], h["level"], h["by"]) for h in held["b"]] == [
        ("c1", "read", None), ("c2", "write", None), ("c5", "low", "a")]
    assert [(h["call_id"], h["level"], h["by"]) for h in held["a"]] == [("c0", "write", None), ("c4", "low", None)]
    assert held["b"][-1] | {"by": None} == {"call_id": "c5", "level": "low", "posts": 1, "mbar": 1.0, "ceiling": 1.0,
                                            "by": None}


def test_a_change_after_the_last_board_call_has_no_call():
    board = Board()
    board.post("a")
    board.read("b")
    board.post("b")
    board.read("a")
    board.post("a")   # round 1: a and b low; b never reads or posts again
    held = coordination_of(board.record(), {"a": None, "b": None})["held"]
    assert held["b"][-1]["call_id"] is None and held["b"][-1]["level"] == "low" and held["b"][-1]["by"] == "a"


def test_a_one_agent_run_has_no_steps_of_level():
    board = Board()
    board.post("a")
    result = coordination_of(board.record(), {"a": None})
    assert result["held"] == {"a": []} and result["team_steps"] == []
    assert coordination_steps(board.record(), {"a": None})[0]["agents"]["a"]["level"] is None
