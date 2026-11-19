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
    level_for,
    run_coordination,
    team_level,
)


class Board:
    """A board built event by event: post() writes a message and its sent event, read() a read event."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.events: list[dict] = []

    def post(self, agent: str) -> str:
        mid = f"m{len(self.messages)}"
        self.messages.append({"id": mid, "sender": agent, "content": "x"})
        self.events.append({"kind": "sent", "agent_id": agent, "message_ids": [mid]})
        return mid

    def read(self, agent: str, ids: list[str] | None = None) -> None:
        ids = [m["id"] for m in self.messages] if ids is None else ids
        self.events.append({"kind": "read", "agent_id": agent, "message_ids": ids})

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


def test_hidden_roles_are_teammates_but_outside_the_team_average():
    board = Board()
    exchange(board, "w1", "reg", 3)
    board.post("w2")
    out = measure(board, ["w1", "w2", "reg"])
    assert out["agents"]["w1"]["mbar"] == pytest.approx(3 / 2)  # reg counts as a teammate in w1's n - 1
    levels = {a: row["level"] for a, row in out["agents"].items()}
    assert levels == {"w1": "low", "w2": "write", "reg": "low"}
    roles = {"w1": "worker", "w2": "worker", "reg": "regulator"}
    assert team_level(levels, roles) == {"team": "low", "hidden": {"reg": "low"}}  # (3 + 2) / 2 = 2.5 -> 3
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
    assert team_level({"a": None}, {"a": None}) == {"team": None, "hidden": {}}


def test_events_of_unknown_agents_are_ignored():
    board = Board()
    board.post("ghost")
    board.read("a")
    out = measure(board, ["a", "b"])["agents"]
    assert out["a"]["read"] is False and out["a"]["level"] == "none"


def _write_run(path, roles: dict, boards: list[dict] | None) -> None:
    path.mkdir()
    (path / "run.json").write_text(json.dumps({"swarm": {"roles": roles}}))
    (path / "episodes.jsonl").write_text("".join(json.dumps({"orbit_agent": a}) + "\n" for a in roles))
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
    assert out["team"] == "write" and out["hidden"] == {"r": "read"}
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
    assert team_level({"r": "low", "d": "write"}, roles) == {"team": None, "hidden": {"r": "low", "d": "write"}}


def test_an_empty_communication_folder_is_none(tmp_path):
    _write_run(tmp_path / "run", {"a": "worker", "b": "worker"}, [])
    out = run_coordination(tmp_path / "run")
    assert out["team"] == "none" and out["pairs"] == [{"agents": ["a", "b"], "rounds": 0}]
