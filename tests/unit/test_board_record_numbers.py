"""A record of who came and went ("a01 entered.", src/swarm/turn_rules is_record) is no post: the topology fact, the
viewer's board, the run page's labels, the Board tab and the judges number the posts alone, P1..Pk, the #n agents see
(src/swarm/board.py _numbers), and mark each record with "record": True, a key only a record has. A board without
records reads as before. No model call."""
from __future__ import annotations

import json
from pathlib import Path

from src.judging import judge_records
from src.measures import topology_fact
from src.measures.run_calls import load_run
from src.measures.run_directory import RunDir
from src.swarm.turn_rules import RECORD
from src.ui import transcript_board as tb
from src.ui import transcript_index, transcript_levels
from tests.unit.test_transcript_board import SAMPLE, levels, write_run

ROLES = {"a01": "worker", "a02": "worker", "r01": "regulator"}
POSTS = ["m1", "m2", "m3", "m4", "m5"]
"""The fixture's posts in board order (test_transcript_board.MESSAGES), the hand-in notice m5 last."""


def with_records(run: Path) -> Path:
    """The fixture run with records put on its board: a01's and a02's entries before the first post, a02's exit between
    P3 and P4, and a01's exit last; a01's last read returned a02's exit with the posts."""
    comm = run / "processed/communication" / f"{SAMPLE}.json"
    doc = json.loads(comm.read_text())
    record = lambda mid, seq, text: {"id": mid, "sender": "system", "content": text, "sequence": seq,  # noqa: E731
                                     "invocation_id": RECORD}
    m = {x["id"]: x for x in doc["messages"]}
    doc["messages"] = [record("e1", 0, "a01 entered."), record("e2", 1, "a02 entered."), m["m1"], m["m2"], m["m3"],
                       record("x2", 9, "a02 exited."), m["m4"], m["m5"], record("x1", 14, "a01 exited.")]
    doc["events"][-1]["message_ids"] = ["m1", "m2", "m3", "x2", "m4", "m5"]
    comm.write_text(json.dumps(doc))
    return run


ORDER = ["e1", "e2", "m1", "m2", "m3", "x2", "m4", "m5", "x1"]
NUMBERS = [None, None, 1, 2, 3, None, 4, 5, None]
"""The board in order with records, and each message's number: the posts 1..5, the records none."""


def test_board_numbers_leave_the_records_out():
    messages = [{"invocation_id": RECORD} if n is None else {} for n in NUMBERS]
    assert topology_fact.board_numbers(messages) == NUMBERS
    assert topology_fact.board_numbers([{}, {"invocation_id": "seed_pack"}, {}]) == [1, 2, 3]   # a seeded post counts


def test_the_topology_fact_numbers_the_posts_alone_and_marks_each_record(tmp_path):
    [board] = topology_fact.topology(RunDir(with_records(write_run(tmp_path))))["boards"]
    assert [(m["id"], m["P"]) for m in board["messages"]] == list(zip(ORDER, NUMBERS))
    assert [m["id"] for m in board["messages"] if m.get("record")] == ["e1", "e2", "x2", "x1"]
    assert {m.get("record") for m in board["messages"] if m["P"] is not None} == {None}   # only a record has the key
    assert {m["notice"] for m in board["messages"] if m.get("record")} == {True}


def test_the_topology_fact_of_a_board_without_records_is_unchanged(tmp_path):
    [board] = topology_fact.topology(RunDir(write_run(tmp_path)))["boards"]
    assert [(m["id"], m["P"]) for m in board["messages"]] == list(zip(POSTS, range(1, 6)))
    assert {"record"} & {k for m in board["messages"] for k in m} == set()


def test_the_viewers_board_numbers_the_posts_alone_and_the_run_page_labels_them(tmp_path):
    run = with_records(write_run(tmp_path))
    [board] = transcript_index.board_view(run, ROLES)
    assert [(m["id"], m["board_no"]) for m in board["messages"]] == list(zip(ORDER, NUMBERS))
    assert [m["id"] for m in board["messages"] if m.get("record")] == ["e1", "e2", "x2", "x1"]
    assert {m.get("record") for m in board["messages"] if m["board_no"] is not None} == {None}
    assert transcript_levels._post_labels([board]) == {mid: f"P{n}" for mid, n in zip(POSTS, range(1, 6))}


def test_the_viewers_board_without_records_is_unchanged(tmp_path):
    [board] = transcript_index.board_view(write_run(tmp_path), ROLES)
    assert [(m["id"], m["board_no"]) for m in board["messages"]] == list(zip(POSTS, range(1, 6)))
    assert {"record"} & {k for m in board["messages"] for k in m} == set()
    assert transcript_levels._post_labels([board]) == {mid: f"P{n}" for mid, n in zip(POSTS, range(1, 6))}


def test_the_judges_number_the_posts_alone_and_are_never_shown_a_record(tmp_path):
    run = with_records(write_run(tmp_path))
    listed = judge_records.posts(load_run(run))
    assert [(p["id"], p["label"]) for p in listed] == [(mid, f"P{n}" if n else None) for mid, n in zip(ORDER, NUMBERS)]
    records = [p for p in listed if p.get("record")]
    assert [p["id"] for p in records] == ["e1", "e2", "x2", "x1"] and {p["writer"] for p in records} == {"system"}
    assert {len(p["readers"]) for p in records} == {0}                 # a01 read x2, but a record has no readers
    assert {p.get("record") for p in listed if p["label"]} == {None}
    board = judge_records.run_records(run).board
    assert [(p["id"], p["label"]) for p in board] == list(zip(POSTS, ["P1", "P2", "P3", "P4", "P5"]))
    assert board[-1]["writer"] == "system"                             # the hand-in notice stays a post


def test_the_judges_board_without_records_is_unchanged(tmp_path):
    run = write_run(tmp_path)
    listed = judge_records.posts(load_run(run))
    assert [(p["id"], p["label"]) for p in listed] == list(zip(POSTS, ["P1", "P2", "P3", "P4", "P5"]))
    assert {"record"} & {k for p in listed for k in p} == set()
    assert judge_records.run_records(run).board == listed


def test_the_board_tab_numbers_the_posts_alone_and_keeps_each_record_in_its_place(tmp_path):
    p = tb.board_payload(with_records(write_run(tmp_path)), levels())
    assert [x["label"] for x in p["posts"] if not x.get("record")] == ["P1", "P2", "P3", "P4", "P5"]
    records = [x for x in p["posts"] if x.get("record")]
    assert [x["text"] for x in records] == ["a01 entered.", "a02 entered.", "a02 exited.", "a01 exited."]
    assert {(x["label"], x["kind"], x["call"], len(x["reads"]), len(x["labels"])) for x in records} == {
        (None, "system", None, 0, 0)}
    # a read that returned a record names only the posts it returned
    by = {a["agent"]: a for a in p["agents"]}
    assert by["a01"]["calls"][2]["summary"] == "read P1, P2, P3, P4, P5"
    assert {x["label"]: x["t"] for x in p["posts"] if x["label"]}["P1"] == tb.board_payload(
        write_run(tmp_path / "plain"), levels())["posts"][0]["t"]


def test_the_board_tab_without_records_is_unchanged(tmp_path):
    p = tb.board_payload(write_run(tmp_path), levels())
    assert [x["label"] for x in p["posts"]] == ["P1", "P2", "P3", "P4", "P5"]
    assert {"record"} & {k for x in p["posts"] for k in x} == set()


def test_the_board_tab_and_the_run_page_leave_records_out_of_the_posts_they_count_and_draw():
    static = Path(tb.__file__).parent / "transcript_static"
    board = (static / "tv_view_board.js").read_text()
    assert "const postCount = d.posts.filter(p => !p.record).length;" in board
    assert "if (p.record) {" in board and "badge('system', 'notice'," in board     # the slim line in the list
    assert board.count("if (p.record) return;") == 2                    # no box on the lanes and no board event
    assert "if (p.record) { at.push(null); continue; }" in board
    run_js = (static / "tv_view_run.js").read_text()
    assert "b.messages.filter(m => !m.record).map(m => [`P${m.board_no}`, m])" in run_js
    assert ".bd-post.bd-record" in (static / "tv_board.css").read_text()
