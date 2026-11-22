"""Entries, exits and alerts for the transcript viewer (src/ui/transcript_presence.py): what /api/run and /api/board
carry as "presence", from synthetic run.json records and, when this checkout has it, the presence smoke's real run."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from src.configuration.settings import ROOT
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


SMOKE = ROOT / "runs" / "study_smoke" / "smoke_openmath" / "20261121T092022Z" / "presence"


@pytest.mark.skipif(not SMOKE.is_dir(), reason="the presence smoke's run is not in this checkout")
def test_the_presence_smoke_reads_on_the_board_and_the_run_page():
    """The real run (tests/smoke/openmath.py presence): a01 stops in round 1, the regulator a03 alerts in round 3 at its
    sixth call, the disregulator a04 enters in round 2 and runs out of budget, a02 hands in, a05 stays."""
    from src.ui import transcript_board as tb
    from src.ui.transcript_index import RunFiles
    from tests.support.measures_now import measures_now
    rf = RunFiles.read(SMOKE)
    levels = rf.levels(measures_now(SMOKE))
    board = tb.board_payload(SMOKE, levels)["presence"]
    assert {a: (x["entered"], (x["exited"] or {}).get("why")) for a, x in board["agents"].items()} == {
        "a01": (1, "stop"), "a02": (1, "submit"), "a03": (1, "alert"), "a04": (2, "budget"), "a05": (1, None)}
    assert board["agents"]["a02"]["exited"]["ends_after_round"] == 7
    [alert] = board["alerts"]
    assert alert["agent"] == "a03" and alert["k"] == 5 and alert["outcome"] == "false_alarm"
    assert alert["t"] == pytest.approx(next(c["t"] for a in tb.board_payload(SMOKE, levels)["agents"]
                                            if a["agent"] == "a03" for c in a["calls"] if c["k"] == 5))
    found = tp.with_alert_calls(tp.presence(rf.run_json), SMOKE)
    assert found["alerts"][0]["k"] == 5 and found["agents"] == tp.presence(rf.run_json)["agents"]


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
