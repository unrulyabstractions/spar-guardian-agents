"""Each agent's rounds for the transcript viewer (src/ui/transcript_rounds.py): what /api/run carries as "rounds" and
/api/board as each agent's "rounds" and each call's round and place, from synthetic run.json records, the Board tab's
synthetic run, and, when this checkout has them, the rounds smoke's real run and a run before 2026-11-21."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from src.configuration.settings import ROOT
from src.measures.run_calls import Call
from src.ui import transcript_rounds as tr
from tests.unit.test_transcript_board import cid, payload, write_run


def call_entry(i: str, tool: str, k: int, t0: float, t1: float, refused: bool = False) -> dict:
    return {"id": i, "tool": tool, "k": k, "free": tool.startswith("board_"), "error": False, "refused": refused,
            "t0": t0, "t1": t1}


def entry(turn: int, agent: str, rnd: int, n: int, end: str | None, charge: str, calls: list[dict], t0: float,
          t1: float) -> dict:
    return {"turn": turn, "agent": agent, "activation": rnd, "round": rnd, "n": n, "step": f"r{rnd}-{agent}",
            "charge": charge, "charged": charge in ("work", "idle"), "used": n, "tools": [c["tool"] for c in calls],
            "calls": calls, "end": end, "t0": t0, "t1": t1, "ended": None, "entered": rnd == 1, "left": False}


TIMELINE = [
    entry(0, "a01", 1, 1, "calls", "work", [call_entry("p", "board_post", 1, 100.1, 100.2),
                                            call_entry("s", "shell", 2, 100.3, 100.9),
                                            call_entry("x", "shell", 3, 101.0, 101.0, refused=True)], 100.0, 101.0),
    entry(1, "a02", 1, 1, "no_tool_call", "idle", [], 101.1, 101.2),
    entry(2, "a01", 2, 2, "end_round", "board", [call_entry("r", "board_read", 1, 101.5, 101.6),
                                                 call_entry("e", "end_round", 2, 101.7, 101.7)], 101.4, 101.8),
]
RUN_JSON = {"agents": ["a01", "a02"], "rules": {"round_budget": 30, "calls_per_round": 10, "timeline": TIMELINE}}


def call(agent: str, k: int, i: str, start: float | None = None, end: float | None = None) -> Call:
    return Call(agent, k, None, start, end, "shell", i, "", {}, "")


def test_rounds_keep_each_entrys_round_count_end_and_charge_and_each_calls_place():
    r = tr.rounds(RUN_JSON)
    assert (r["rounds"], r["calls_per_round"]) == (30, 10)
    assert list(r["agents"]) == ["a01", "a02"]
    first, second = r["agents"]["a01"]
    assert {k: first[k] for k in ("turn", "round", "n", "end", "charge", "charged")} == {
        "turn": 0, "round": 1, "n": 1, "end": "calls", "charge": "work", "charged": True}
    assert [(c["id"], c["tool"], c["pos"], c["refused"]) for c in first["calls"]] == [
        ("p", "board_post", 1, False), ("s", "shell", 2, False), ("x", "shell", 3, True)]
    assert set(first["calls"][0]) == {*tr.CALL_KEYS, "pos"}
    assert (second["end"], second["charge"], second["charged"]) == ("end_round", "board", False)
    # end_round never counts toward N, so it has no place in its round
    assert [(c["tool"], c["pos"]) for c in second["calls"]] == [("board_read", 1), ("end_round", None)]
    assert tr.places([{"tool": "end_round"}, {"tool": "shell"}, {"tool": "end_round"}, {"tool": "shell"}]) == [
        None, 1, None, 2]
    assert r["agents"]["a02"] == [{**{k: TIMELINE[1].get(k) for k in tr.ROUND_KEYS}, "calls": []}]


def test_a_round_since_2026_11_22_keeps_its_ending_and_no_charge():
    """Every round uses one up: an entry holds its ending (the tool that ended the agent's turns, or None) and no
    charge, own count or free calls; the viewer reads those as None."""
    new = {k: v for k, v in TIMELINE[0].items() if k not in ("n", "charge", "charged")}
    new = {**new, "ending": None, "calls": [{k: v for k, v in c.items() if k != "free"} for c in new["calls"]]}
    stop = {**new, "turn": 1, "round": 2, "ending": "stop", "end": "ending", "used": 2, "calls": []}
    r = tr.rounds({"agents": ["a01"], "rules": {"round_budget": 30, "calls_per_round": 10, "timeline": [new, stop]}})
    first, second = r["agents"]["a01"]
    assert (first["ending"], first["n"], first["charge"], first["used"]) == (None, None, None, 1)
    assert (second["ending"], second["end"]) == ("stop", "ending") and first["calls"][0]["free"] is None
    assert [x["ending"] for x in tr.from_start(r, 100.0)["a01"]] == [None, "stop"]


def test_a_run_before_rounds_of_calls_has_none():
    old = [{"turn": 0, "agent": "a01", "activation": 1, "working": True, "free": 0, "charge": "work", "step": "s",
            "used": 1, "tools": ["shell"], "ended": None, "entered": True, "left": False}]
    assert tr.rounds({"rules": {"timeline": old, "budget": 100, "steps_per_agent": 50}}) is None
    assert tr.rounds({}) is None
    assert tr.on_clock(None, {}, {}, True) == ({}, {})
    # a run of rounds whose timeline is still empty is a run of rounds
    assert tr.rounds({"rules": {"round_budget": 3, "calls_per_round": 2, "timeline": []}}) == {
        "rounds": 3, "calls_per_round": 2, "agents": {}}


def test_on_the_eval_logs_clock_a_round_spans_its_own_times_from_the_first_calls_start():
    calls = {"a01": [call("a01", 0, "p", 100.1, 100.2), call("a01", 1, "s", 100.3, 100.9),
                     call("a01", 2, "x", 101.0, 101.0), call("a01", 3, "r", 101.5, 101.6),
                     call("a01", 4, "e", 101.7, 101.7)],
             "a02": []}
    at = {(c.agent, c.k): (c.start - 100.1, c.end - c.start) for c in calls["a01"]}
    lanes, where = tr.on_clock(tr.rounds(RUN_JSON), calls, at, True)
    a01 = lanes["a01"]
    assert [(x["round"], x["n"], x["end"], x["charge"], x["calls"]) for x in a01] == [
        (1, 1, "calls", "work", 3), (2, 2, "end_round", "board", 2)]
    assert a01[0]["t"] == 0.0 and a01[0]["dur"] == pytest.approx(0.9)     # opened before the first call: from 0
    assert (a01[1]["t"], a01[1]["dur"]) == pytest.approx((1.3, 0.4))
    assert lanes["a02"][0]["t"] == pytest.approx(1.0) and lanes["a02"][0]["calls"] == 0
    assert where == {("a01", 0): {"round": 1, "pos": 1}, ("a01", 1): {"round": 1, "pos": 2},
                     ("a01", 2): {"round": 1, "pos": 3}, ("a01", 3): {"round": 2, "pos": 1},
                     ("a01", 4): {"round": 2, "pos": None}}     # end_round never counts toward N: no place


def test_on_the_call_order_clock_a_round_spans_its_calls_and_an_empty_one_sits_after_the_last():
    run = {"rules": {"round_budget": 3, "calls_per_round": 2, "timeline": [
        entry(0, "a01", 1, 1, "calls", "work", [call_entry("a", "shell", 1, 1, 2), call_entry("b", "shell", 2, 2, 3)],
              1, 3),
        entry(1, "a01", 2, 2, "no_tool_call", "idle", [], 3, 4),
        entry(2, "a01", 3, 3, "end_round", "work", [call_entry("c", "shell", 1, 4, 5)], 4, 5)]}}
    calls = {"a01": [call("a01", 0, "a"), call("a01", 1, "b"), call("a01", 2, "c"), call("a01", 3, "lost")]}
    at = {("a01", k): (float(k), 1.0) for k in range(4)}
    lanes, where = tr.on_clock(tr.rounds(run), calls, at, False)
    assert [(x["round"], x["t"], x["dur"]) for x in lanes["a01"]] == [(1, 0.0, 2.0), (2, 2.0, 0.0), (3, 2.0, 1.0)]
    assert ("a01", 3) not in where                                       # a call in no entry has no round


def test_the_board_tab_gives_each_call_its_round_and_place_and_each_agent_its_rounds(tmp_path):
    run = write_run(tmp_path)
    rj = json.loads((run / "run.json").read_text())
    a01 = [cid("a01", k) for k in range(3)]
    rj["rules"] = {"round_budget": 5, "calls_per_round": 2, "timeline": [
        entry(0, "a01", 1, 1, "calls", "work", [call_entry(a01[0], "shell", 1, 0, 0),
                                                call_entry(a01[1], "board_post", 2, 0, 0)], None, None),
        entry(1, "a01", 2, 2, "ending", "stop", [call_entry(a01[2], "board_read", 1, 0, 0)], None, None)]}
    (run / "run.json").write_text(json.dumps(rj))
    p = payload(run)
    assert p["calls_per_round"] == 2
    lanes = {a["agent"]: a for a in p["agents"]}
    assert [(c["k"], c["round"], c["pos"]) for c in lanes["a01"]["calls"]] == [(0, 1, 1), (1, 1, 2), (2, 2, 1)]
    assert [(x["round"], x["end"], x["calls"], x["t"]) for x in lanes["a01"]["rounds"]] == [
        (1, "calls", 2, 0.0), (2, "ending", 1, 30.0)]                      # no times recorded: placed by its calls
    assert all(c["round"] is None and c["pos"] is None for c in lanes["a02"]["calls"])
    assert lanes["a02"]["rounds"] == []


def test_the_board_tab_of_a_run_before_rounds_of_calls_has_no_rounds(tmp_path):
    p = payload(write_run(tmp_path))
    assert p["calls_per_round"] is None
    assert all(a["rounds"] == [] and all(c["round"] is None for c in a["calls"]) for a in p["agents"])


SMOKE = ROOT / "runs" / "study_smoke" / "smoke_openmath" / "20261121T131247Z" / "rounds"
OLD = ROOT / "OLD" / "runs" / "ian_baseline_explorations_1" / "flipt_minimal_cheat_dsflash_fixed3"
STATIC = Path(tr.__file__).parent / "transcript_static"


@pytest.mark.skipif(not SMOKE.is_dir(), reason="the rounds smoke's run is not in this checkout")
def test_the_rounds_smoke_reads_on_the_run_page_and_the_board():
    """The real run (tests/support/round_scenario.py): 3 rounds of up to 3 calls; a01 fills round 1 and is refused its
    4th call in round 2, a02 ends rounds with end_round and has a round of board calls only, a03 replies with no tool
    call in round 1, the regulator a04 runs out of rounds."""
    from src.ui import transcript_board as tb
    from src.ui.transcript_index import RunFiles
    rf = RunFiles.read(SMOKE)
    r = tr.rounds(rf.run_json)
    assert (r["rounds"], r["calls_per_round"]) == (3, 3)
    assert [(x["round"], x["n"], x["end"], x["charge"]) for x in r["agents"]["a02"]] == [
        (1, 1, "end_round", "work"), (2, 2, "end_round", "board"), (3, 2, "end_round", "work"),
        (4, 3, "ending", "stop")]
    assert r["agents"]["a03"][0]["end"] == "no_tool_call" and r["agents"]["a03"][0]["calls"] == []
    assert [c["refused"] for c in r["agents"]["a01"][1]["calls"]] == [False, False, False, True]
    board = tb.board_payload(SMOKE, rf.levels(rf.measured()))
    lanes = {a["agent"]: a for a in board["agents"]}
    assert [(c["round"], c["pos"]) for c in lanes["a01"]["calls"]] == [
        (1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3), (2, 4), (3, 1)]
    assert [x["round"] for x in lanes["a02"]["rounds"]] == [1, 2, 3, 4]
    for lane in lanes.values():   # every call of a round lies inside the round's span on the clock
        spans = {x["round"]: (x["t"], x["t"] + x["dur"]) for x in lane["rounds"]}
        for c in lane["calls"]:
            lo, hi = spans[c["round"]]
            assert lo - 1e-3 <= c["t"] and c["t"] + c["dur"] <= hi + 1e-3, (lane["agent"], c)


@pytest.mark.skipif(not OLD.is_dir(), reason="the run before 2026-11-21 is not in this checkout")
def test_a_run_before_rounds_of_calls_reads_with_no_rounds():
    from src.ui.transcript_index import RunFiles
    assert tr.rounds(RunFiles.read(OLD).run_json) is None


def test_from_start_puts_each_round_in_seconds_from_the_samples_start():
    got = tr.from_start(tr.rounds(RUN_JSON), 100.0)
    assert [(x["round"], x["end"], x["charge"]) for x in got["a01"]] == [
        (1, "calls", "work"), (2, "end_round", "board")]
    assert (got["a01"][0]["t"], got["a01"][0]["t_end"]) == pytest.approx((0.0, 1.0))
    assert (got["a02"][0]["t"], got["a02"][0]["t_end"]) == pytest.approx((1.1, 1.2))
    assert tr.from_start(None, 0.0) is None
    untimed = {"rules": {"calls_per_round": 2, "timeline": [entry(0, "a01", 1, 1, None, "idle", [], None, None)]}}
    assert tr.from_start(tr.rounds(untimed), 5.0)["a01"][0] == {
        "round": 1, "n": 1, "end": None, "ending": None, "charge": "idle", "t": None, "t_end": None}


@pytest.mark.skipif(not SMOKE.is_dir(), reason="the rounds smoke's run is not in this checkout")
def test_the_results_browsers_timeline_carries_the_rounds_and_an_old_run_none():
    from src.ui._eval_log_views import LogViews
    from src.ui._run_store import Store
    tl = LogViews(Store(SMOKE.parent)).timeline(SMOKE.name)
    assert [x["round"] for x in tl["rounds"]["a02"]] == [1, 2, 3, 4]
    models = sorted(e["t"] for e in tl["events"] if e["kind"] == "model" and e["agent"] == "a01")
    first = tl["rounds"]["a01"][0]
    assert first["t"] <= models[0] <= first["t_end"]       # the round's first model call lies inside it
    if OLD.is_dir():
        assert LogViews(Store(OLD.parent)).timeline(OLD.name)["rounds"] is None


@pytest.mark.skipif(shutil.which("node") is None, reason="node runs the page's own function")
def test_a_rounds_end_reads_as_a_word_or_two_and_an_ending_names_its_tool():
    source = (STATIC / "tv_case_view.js").read_text()
    consts = "\n".join(re.search(rf"^const {name} = .*?;$", source, flags=re.S | re.M).group(0)
                       for name in ("END_WORDS", "ENDING_TOOL"))
    func = re.search(r"^export function roundEndWord\(.*?^}$", source, flags=re.S | re.M).group(0)
    cases = [{"end": e, "charge": c} for e, c in [("end_round", "work"), ("calls", "work"), ("no_tool_call", "idle"),
                                                  ("ending", "stop"), ("ending", "submit"), ("ending", "alert"),
                                                  ("time", "work"), ("context", "work"), (None, "work")]]
    script = (f"{consts}\n{func.removeprefix('export ')}\n"
              f"console.log(JSON.stringify({json.dumps(cases)}.map(roundEndWord)));")
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == ["end_round", "N reached", "no tool call", "stop_working", "submit",
                                       "alert_humans", "time limit", "context limit", None]
    assert set(tr.ROUND_KEYS) >= {"end", "charge"}
