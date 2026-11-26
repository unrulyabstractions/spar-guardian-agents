"""Entries, exits, alerts and hand-ins for the transcript viewer (src/ui/transcript_presence.py): what /api/run and
/api/board carry as "presence" and "hand_ins", from synthetic run.json records and, when this checkout has it, the
presence smoke's real run."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.measures.run_calls import Call
from src.ui import transcript_presence as tp
from tests.support.run_files import write_text

ALERT = {"agent": "r01", "round": 3, "reason": "a01 read the answer key", "agents": ["a01"], "time": "t"}
OUTCOME = {**ALERT, "n": 7, "team_cheated_by_then": True, "named_cheated_by_then": {"a01": True},
           "outcome": "true_alarm"}
RUN_JSON = {"agents": ["a01", "a02", "r01", "d01"],
            "rules": {"entry_rounds": {"a01": 1, "a02": 1, "r01": 1, "d01": 2},
                      "exits": {"a01": {"why": "stop", "round": 2, "seq": 0},
                                "r01": {"why": "alert", "round": 3, "seq": 1},
                                "d01": {"why": "budget", "round": 3, "seq": 2}}},
            "alerts": [ALERT], "signals": {"team": {"alert_outcomes": [OUTCOME]}}}


def call(agent: str, k: int, tool: str = "shell") -> Call:
    return Call(agent, k, None, None, None, tool, f"{agent}-{k}", "", {}, "")


def test_presence_reads_each_agents_entry_and_exit_and_each_alerts_outcome():
    p = tp.presence(RUN_JSON)
    assert list(p["agents"]) == ["a01", "a02", "r01", "d01"]
    assert p["agents"]["a01"] == {"entered": 1, "exited": {"why": "stop", "round": 2, "seq": 0}}
    assert p["agents"]["a02"] == {"entered": 1, "exited": None}           # present at the end
    assert p["agents"]["d01"]["entered"] == 2 and p["agents"]["d01"]["exited"]["why"] == "budget"
    assert p["alerts"] == [{**ALERT, "outcome": "true_alarm", "team_cheated_by_then": True,
                            "named_cheated_by_then": {"a01": True}}]
    assert set(tp.EXIT_WHY) >= {e["why"] for e in RUN_JSON["rules"]["exits"].values()}


def test_an_alert_takes_no_outcome_from_a_row_of_another_alert_or_from_no_row():
    other = {**OUTCOME, "round": 4}
    assert tp.presence({**RUN_JSON, "signals": {"team": {"alert_outcomes": [other]}}})["alerts"][0]["outcome"] is None
    unmeasured = tp.presence({**RUN_JSON, "signals": {}})["alerts"][0]
    assert {k: unmeasured[k] for k in tp.OUTCOME_KEYS} == dict.fromkeys(tp.OUTCOME_KEYS)


def test_a_run_made_before_presence_was_recorded_has_none():
    assert tp.presence({"agents": ["a01", "a02"], "rules": {}}) == {"agents": {}, "alerts": []}
    assert tp.on_lanes(tp.presence({}), {}, {}) == {"agents": {}, "alerts": []}


def test_on_the_lanes_an_entry_is_the_start_or_the_first_calls_start_an_exit_the_last_calls_end_an_alert_its_call():
    calls = {"a01": [call("a01", 0), call("a01", 1, "stop_working")],
             "a02": [call("a02", 0)],
             "r01": [call("r01", 0, "get_peer_actions"), call("r01", 1, "alert_humans")],
             "d01": []}
    at = {("a01", 0): (1.0, 2.0), ("a01", 1): (5.0, 0.5), ("a02", 0): (0.0, 1.0), ("r01", 0): (2.0, 1.0),
          ("r01", 1): (8.0, 0.25)}
    placed = tp.on_lanes(tp.presence(RUN_JSON), calls, at)
    assert placed["agents"]["a01"]["entry"] == {"k": 0, "t": 0.0, "start": True}    # present from the first round
    assert placed["agents"]["a01"]["exit"] == {"k": 1, "t": 5.5}
    assert placed["agents"]["a02"]["exit"] == {"k": None, "t": None}      # never exited
    assert placed["agents"]["r01"]["exit"] == {"k": 1, "t": 8.25}
    assert placed["agents"]["d01"]["entry"] == {"k": None, "t": None, "start": False}   # no call to place it at
    assert placed["alerts"][0]["k"] == 1 and placed["alerts"][0]["t"] == 8.0
    assert placed["alerts"][0]["reason"] == ALERT["reason"] and placed["alerts"][0]["outcome"] == "true_alarm"
    late = tp.on_lanes(tp.presence(RUN_JSON), {**calls, "d01": [call("d01", 0), call("d01", 1)]},
                       {**at, ("d01", 0): (6.0, 1.0), ("d01", 1): (9.0, 0.5)})["agents"]["d01"]
    assert late["entry"] == {"k": 0, "t": 6.0, "start": False}               # entered in round 2: its first call
    assert late["exit"] == {"k": 1, "t": 9.5}


def test_the_ith_alert_of_an_agent_is_its_ith_alert_humans_call():
    alerts = [{"agent": "r01"}, {"agent": "d01"}, {"agent": "r01"}, {"agent": "r01"}]
    ks = {"r01": [3, 9], "d01": []}
    assert tp.nth_alert_calls(alerts, lambda a: ks[a]) == [3, None, 9, None]


def test_with_alert_calls_numbers_the_call_as_the_transcript_scan_does(tmp_path):
    run = tmp_path / "run"
    folder = run / "processed/trajectories" / "s#1@r01"
    folder.mkdir(parents=True)
    steps = [{"step_id": 1, "source": "system", "message": "You are r01."},
             {"step_id": 2, "source": "user", "message": "a01 entered.",
              "extra": {"swarm_presence": {"id": "entered:a01", "agent": "a01", "kind": "entered", "round": 1}}},
             {"step_id": 3, "source": "agent", "tool_calls": [
                 {"tool_call_id": "c0", "function_name": "board_read", "arguments": {}},
                 {"tool_call_id": "c1", "function_name": "get_peer_actions", "arguments": {"peer": "a01"}}]},
             {"step_id": 4, "source": "agent", "tool_calls": [
                 {"tool_call_id": "c2", "function_name": "alert_humans", "arguments": {"reason": "x"}}]}]
    (folder / "transcript.json").write_text(json.dumps({"trajectory": {"steps": steps}}))
    write_text(run / "processed/episodes.jsonl", json.dumps({"episode": "s#1@r01", "agent": "r01",
                                                             "role": "regulator"}) + "\n")
    write_text(run / "processed/run.json", json.dumps({"agents": ["r01"]}))
    p = tp.with_alert_calls(tp.presence(RUN_JSON), run)
    assert p["alerts"][0]["k"] == 2
    assert tp.with_alert_calls({"agents": {}, "alerts": []}, run) == {"agents": {}, "alerts": []}


STATIC = Path(tp.__file__).parent / "transcript_static"


def js_function(name: str) -> str:
    source = (STATIC / "tv_presence.js").read_text()
    return re.search(rf"^export function {name}\(.*?^}}$", source, flags=re.S | re.M).group(0).removeprefix("export ")


@pytest.mark.skipif(shutil.which("node") is None, reason="node runs the page's own functions")
def test_a_hover_shows_at_most_twelve_words_of_a_reason_and_an_exit_in_a_word_or_two():
    source = (STATIC / "tv_presence.js").read_text()
    consts = "\n".join(re.search(rf"^(export )?const {name} = .*?;$", source, flags=re.S | re.M).group(0)
                       for name in ("REASON_WORDS", "WHY_WORDS"))
    script = "\n".join([consts.replace("export ", ""), js_function("clipWords"), js_function("exitWords"),
                        "console.log(JSON.stringify([clipWords('one two  three'), clipWords(Array.from({length: 20},"
                        " (_, i) => 'w' + i).join(' ')), exitWords({why: 'stop'}), exitWords({why: 'submit', hand_in:"
                        " 'team', ends_after_round: 7}), exitWords({why: 'submit', hand_in: 'solo'})]));"])
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == ["one two three", " ".join(f"w{i}" for i in range(12)) + " …", "stop",
                                       "submit · run ends after round 7", "submit"]
    for why in tp.EXIT_WHY:     # every exit's reason has its word
        assert re.search(rf"\b{why}: '", source), why
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b|rgb\(", source + (STATIC / "tv_presence.css").read_text())


@pytest.mark.skipif(shutil.which("node") is None, reason="node runs the page's own functions")
def test_a_user_steps_presence_events_in_both_shapes():
    """A round's message holds {round, events}; a presence line before 2026-11-22 was one event."""
    event = {"id": "entered:a01", "agent": "a01", "kind": "entered", "why": None, "round": 1}
    script = "\n".join([js_function("presenceEvents"), "console.log(JSON.stringify([" + ", ".join(
        f"presenceEvents({json.dumps(extra)})" for extra in (
            {"swarm_presence": {"round": 2, "events": [event, {**event, "id": "exited:a02", "agent": "a02"}]}},
            {"swarm_presence": event}, {"swarm_round": {"round": 1}}, None)) + "]));"])
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == [[event, {**event, "id": "exited:a02", "agent": "a02"}], [event], [], []]


@pytest.mark.skipif(shutil.which("node") is None, reason="node runs the page's own functions")
def test_agents_that_entered_together_share_one_chip():
    """Four or more agents entering in one round share a chip that counts them and names them on hover (nine names,
    then the rest as a number); fewer keep a chip each, and so does every other event."""
    source = (STATIC / "tv_presence.js").read_text()
    consts = "\n".join(re.search(rf"^(export )?const {name} = .*?;$", source, flags=re.S | re.M).group(0)
                       for name in ("WHY_WORDS", "EVENT_WORDS", "ENTERED_TOGETHER", "HOVER_NAMES"))
    entered = [{"agent": f"a{i:02d}", "kind": "entered", "round": 1} for i in range(1, 13)]
    late = [{"agent": "b01", "kind": "entered", "round": 4}, {"agent": "a03", "kind": "exited", "why": "stop",
                                                              "round": 4}]
    script = "\n".join([
        "const h = (tag, attrs, ...kids) => ({title: attrs && attrs.title, words: kids.filter(k => typeof k === "
        "'string').join('')});",
        consts.replace("export ", ""), js_function("presenceChip"), js_function("presenceChips"),
        f"console.log(JSON.stringify(presenceChips({json.dumps(entered + late)}).filter(Boolean)));"])
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == [
        {"title": "a01, a02, a03, a04, a05, a06, a07, a08, a09 +3", "words": "12 agents entered"},
        {"title": "round 4", "words": "b01 entered"}, {"title": "round 4", "words": "a03 exited · stop"}]


@pytest.mark.skipif(shutil.which("node") is None, reason="node runs the page's own functions")
def test_the_chips_of_a_rounds_message_say_what_the_agent_read():
    """The board chip's hover fits where it stands: "posts on the board" on a first board part, the change since the
    agent was last told later. An entry folded into its exit gets no chip; a notice at the end of a result gets one."""
    source = (STATIC / "tv_presence.js").read_text()
    consts = "\n".join(re.search(rf"^(export )?const {name} = .*?;$", source, flags=re.S | re.M).group(0)
                       for name in ("WHY_WORDS", "EVENT_WORDS"))
    script = "\n".join([
        "const h = (tag, attrs, ...kids) => ({tag, title: attrs && attrs.title, kids});",
        consts.replace("export ", ""), js_function("boardChip"), js_function("presenceChip"),
        js_function("paragraphChip"),
        "console.log(JSON.stringify([boardChip({activation: 2, first: true}).title, boardChip({activation: 2}).title,"
        " boardChip({activation: 1}).title, presenceChip({agent: 'a01', kind: 'entered', folded: true}),"
        " presenceChip({agent: 'a01', kind: 'exited', why: 'stop', round: 1}).kids[0],"
        " paragraphChip({swarm_notice_paragraph: ['The board changed.']}).title, paragraphChip({})]));"])
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == ["posts on the board", "board changed since the agent was last told",
                                       "posts on the board", None, "a01 exited · stop", "The board changed.", None]
    assert "since the last round" not in source


def at_time(seconds: float) -> str:
    """An ISO time, as run.json records a hand-in's, `seconds` after the epoch."""
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


HAND_IN_RUN = {
    "rules": {"timeline": [
        {"agent": "a01", "calls": [{"id": "s1", "tool": "submit", "t0": 100.0, "t1": 100.5},
                                   {"id": "s2", "tool": "submit", "t0": 300.0, "t1": 300.2}]},
        {"agent": "a02", "calls": [{"id": "u1", "tool": "unsubmit", "t0": 200.0, "t1": 200.1},
                                   {"id": "s3", "tool": "submit", "t0": 250.0, "t1": 250.1}]}]},
    "unsubmits": {
        "submits": [{"agent": "a01", "round": 3, "answer": "first", "snapshot": "aaaaaaaa11", "time": at_time(100.2),
                     "seq": 0},
                    {"agent": "a01", "round": 6, "answer": "again", "snapshot": "bbbbbbbb22", "time": at_time(300.1),
                     "seq": 2}],
        "unsubmits": [{"agent": "a02", "round": 4, "reason": "the fixtures are wrong", "time": at_time(200.05),
                       "seq": 1, "submit": {"agent": "a01", "round": 3, "snapshot": "aaaaaaaa11"}}]},
    "team_submission": {"agent": "a01", "round": 6, "answer": "again", "snapshot": "bbbbbbbb22",
                        "time": at_time(300.1), "refused": [{"agent": "a02", "round": 5, "reason": "already in"}]},
}


def test_hand_ins_are_the_runs_submits_and_unsubmits_in_seq_order_each_with_its_call():
    """The take-back sits between the two hand-ins by seq; each event's call is the agent's call of its tool whose
    span holds the record's time; the final is team_submission, and the submit refused while it stood is kept."""
    h = tp.hand_ins(HAND_IN_RUN)
    assert [(e["kind"], e["agent"], e["round"], e["call_id"], e["final"]) for e in h["events"]] == [
        ("submit", "a01", 3, "s1", False), ("unsubmit", "a02", 4, "u1", False), ("submit", "a01", 6, "s2", True)]
    assert h["events"][1]["text"] == "the fixtures are wrong" and h["events"][1]["snapshot"] == "aaaaaaaa11"
    assert h["events"][1]["took_back"] == {"agent": "a01", "round": 3, "snapshot": "aaaaaaaa11"}
    assert h["events"][0]["text"] == "first" and "took_back" not in h["events"][0]
    assert h["final"] == {"agent": "a01", "round": 6, "snapshot": "bbbbbbbb22", "call_id": "s2"}
    assert h["refused"] == [{"agent": "a02", "round": 5, "reason": "already in"}]


def test_a_run_without_the_unsubmit_option_has_its_one_hand_in_and_an_older_run_none():
    alone = {k: v for k, v in HAND_IN_RUN.items() if k != "unsubmits"}
    h = tp.hand_ins(alone)
    assert [(e["kind"], e["agent"], e["call_id"], e["final"]) for e in h["events"]] == [("submit", "a01", "s2", True)]
    assert h["final"]["call_id"] == "s2"
    assert tp.hand_ins({"agents": ["a01"]}) == {"events": [], "final": None, "refused": []}
    taken = {**HAND_IN_RUN, "team_submission": None}         # the last hand-in was taken back: none stood
    assert tp.hand_ins(taken)["final"] is None and not any(e["final"] for e in tp.hand_ins(taken)["events"])


def test_a_record_with_no_time_or_no_call_in_its_span_has_no_call():
    timeline = HAND_IN_RUN["rules"]["timeline"]
    assert tp.call_at(timeline, "a01", "submit", None) is None
    assert tp.call_at(timeline, "a01", "submit", at_time(150.0)) is None          # between its two calls
    assert tp.call_at(timeline, "a02", "submit", at_time(100.2)) is None          # another agent's time
    assert tp.call_at(timeline, "a02", "unsubmit", at_time(200.05)) == "u1"


@pytest.mark.skipif(shutil.which("node") is None, reason="node runs the page's own functions")
def test_a_submit_or_unsubmit_calls_chip_says_what_the_call_did():
    """handInChip: handed in with its commit, taken back with the commit it took back, not handed in and nothing taken
    back for a call that made no event, the tool alone in a run that recorded no hand-in; the star only on the final."""
    h = tp.hand_ins(HAND_IN_RUN)
    source = (STATIC / "tv_presence.js").read_text()
    consts = "\n".join(re.search(rf"^export const {name} = .*?;$", source, flags=re.M).group(0).removeprefix("export ")
                       for name in ("shortSha",))
    stub = ("const h = (tag, attrs, ...kids) => ({tag, attrs, text: kids.flat(9).map(k => k && k.text !== undefined "
            "? k.text : k).filter(k => k != null && k !== false).join('')});")
    calls = [("submit", h["events"][0]), ("submit", h["events"][2]), ("unsubmit", h["events"][1]), ("submit", None),
             ("unsubmit", None)]
    script = "\n".join([stub, "const SHORT_SHA = 7;", consts, js_function("handInChip"), "console.log(JSON.stringify(["
                        + ", ".join(f"handInChip({json.dumps(t)}, {json.dumps(e)}, {json.dumps(h)}).text"
                                    for t, e in calls)
                        + f", handInChip('submit', null, {json.dumps(tp.hand_ins({}))}).text]));"])
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == ["▲handed in · aaaaaaa", "▲★ handed in · bbbbbbb", "▼taken back · aaaaaaa",
                                       "▲not handed in", "▼nothing taken back", "▲submit"]
