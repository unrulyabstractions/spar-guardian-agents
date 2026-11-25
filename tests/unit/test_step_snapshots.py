"""steps.jsonl's delta encoding: the first line of an agent in full, then only what changed, a removed ref as null, and
decode giving every state back. No Docker."""
from __future__ import annotations

from src.replay.step_snapshots import apply_delta, decode, delta, encode, pieces_state


def row(n: int, agent: str, refs: dict, team: dict, head: str = "h") -> dict:
    return {"n": n, "agent": agent, "k": n, "call_id": f"c{n}", "t": float(n), "tool": "shell", "match": "exact",
            "state": {"head": head, "branch": agent, "refs": refs, "team_refs": team, "pieces": {}, "dirty": False}}


def test_delta_and_apply():
    assert delta(None, {"a": "1"}) == {"a": "1"}
    assert delta({"a": "1", "b": "2"}, {"a": "1", "b": "3", "c": "4"}) == {"b": "3", "c": "4"}
    assert delta({"a": "1", "b": "2"}, {"a": "1"}) == {"b": None}
    assert apply_delta({"a": "1", "b": "2"}, {"b": None, "c": "4"}) == {"a": "1", "c": "4"}


def test_encode_writes_each_agents_first_map_whole_then_its_changes():
    rows = [row(1, "a", {"refs/heads/a": "1", "refs/heads/main": "0"}, {"refs/heads/main": "0"}),
            row(2, "b", {"refs/heads/b": "0"}, {"refs/heads/main": "0"}),
            row(3, "a", {"refs/heads/a": "2", "refs/heads/main": "0"}, {"refs/heads/main": "0", "refs/heads/a": "2"}),
            row(4, "a", {"refs/heads/main": "0"}, {"refs/heads/main": "0", "refs/heads/a": "2"}, head="0")]
    lines = encode(rows)
    assert lines[0]["state"]["refs"] == {"refs/heads/a": "1", "refs/heads/main": "0"}
    assert lines[1]["state"]["refs"] == {"refs/heads/b": "0"}              # b's first line: whole
    assert lines[2]["state"]["refs"] == {"refs/heads/a": "2"}
    assert lines[2]["state"]["team_refs"] == {"refs/heads/a": "2"}         # since a's previous line, not b's
    assert lines[3]["state"]["refs"] == {"refs/heads/a": None} and lines[3]["state"]["team_refs"] == {}
    assert lines[3]["state"]["head"] == "0"                                 # every other key in full
    assert decode(lines) == rows


def test_pieces_state_names_the_refs_that_hold_each_piece():
    got = pieces_state(["A", "B"], {"A"}, {"refs/heads/k": ["A"], "refs/remotes/origin/rd/x": ["A", "B"]},
                       {"refs/heads/main": ["B"]})
    assert got == {"A": {"tree": True, "team": [], "refs": ["refs/heads/k", "refs/remotes/origin/rd/x"]},
                   "B": {"tree": False, "team": ["refs/heads/main"], "refs": ["refs/remotes/origin/rd/x"]}}
