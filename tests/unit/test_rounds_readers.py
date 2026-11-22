"""The readers of the turn rules' records on rounds of tool calls, and on the steps of a run before 2026-11-21: the
export's facts and engagement, the run figures' kinds and places, the team's ending, the calls' rounds in run_calls,
and the transcript's round lines."""
from __future__ import annotations

import json

from src.export import _export_run_blocks as blocks
from src.export.run_export import write_run
from src.measures import run_figures
from src.measures import team_ladder as team
from src.measures.run_calls import load_run
from src.swarm import turn_rules as rules
from tests.support.mock_model import tagged_infos
from tests.support.turn_rules_harness import board_post, do, end_round, pull, read, run, store

OLD_STATE = {"agents": ["a01", "a02"], "cap": 100, "pooled": True, "activations": {"a01": 4}, "work_steps": {"a01": 3},
             "free_steps": {"a01": 1}, "ended": {}, "windows": {"a01": [1, None]}}


def exported(tmp_path, config, scripts):
    sample, _, log = run(config, scripts, tmp_path, with_log=True)
    write_run(log.location, tmp_path / "run")
    records = {r["agent"]: r for r in map(json.loads, (tmp_path / "run" / "episodes.jsonl").open())}
    return sample, records, json.loads((tmp_path / "run" / "run.json").read_text())


def test_the_export_reads_rounds_and_keeps_reading_steps(tmp_path):
    config = pull(n_agents=2, rounds=2, calls_per_round=3, concurrent_rounds=False, turn_order="round_robin")
    scripts = {"a01": [read(), board_post("hi"), end_round(), do("A"), end_round()], "a02": [do("B"), end_round()]}
    sample, records, run_json = exported(tmp_path, config, scripts)
    # a01: a round of board calls, then a round of work: each uses one up
    assert records["a01"]["rules"] == {"activations": 2, "rounds_used": 2, "ended": "rounds",
                                       "windows": [1, None]}, records["a01"]["rules"]
    eng = records["a01"]["engagement"]
    assert eng["rounds_used"] == 2 and "board_rounds" not in eng and "work_steps" not in eng
    assert {records[a]["ended_by"] for a in ("a01", "a02")} == {"rounds"}
    # run.json holds the whole TurnState: each round's calls with their times, and the run reads as out of rounds
    timeline = run_json["rules"]["timeline"]
    assert [(e["agent"], e["round"], e["ending"], e["used"], e["end"]) for e in timeline][:2] == [
        ("a01", 1, None, 1, "end_round"), ("a02", 1, None, 1, "end_round")]
    assert all(c["t0"] is not None for e in timeline for c in e["calls"])
    assert team.ended_by(run_json, list(records.values())) == "steps"
    # a run before 2026-11-22: its board rounds come back as they were recorded
    assert blocks.rules_facts({"activations": {"a01": 3}, "rounds_used": {"a01": 2}, "board_rounds": {"a01": 1},
                               "ended": {}, "windows": {"a01": [1, None]}}, "a01") == {
        "activations": 3, "rounds_used": 2, "board_rounds": 1, "ended": None, "windows": [1, None]}
    # a run before 2026-11-21: the old fields come back as they were recorded
    assert blocks.rules_facts(OLD_STATE, "a01") == {"activations": 4, "work_steps": 3, "ended": None,
                                                    "windows": [1, None]}
    assert team.ending_at_cap({"agent": "a01", "ended_by": "end"}, {"cap": 3, "work_steps": {"a01": 3}}) == "cap"


def test_run_calls_takes_each_calls_round_by_its_id(tmp_path):
    config = pull(n_agents=2, rounds=2, calls_per_round=2, concurrent_rounds=False, turn_order="round_robin")
    _, records, run_json = exported(tmp_path, config, {"a01": [do("A1"), do("A2")], "a02": [do("B")]})
    loaded = load_run(tmp_path / "run")
    assert loaded.notes == []
    turns = [e["turn"] for e in run_json["rules"]["timeline"] if e["agent"] == "a01"]
    # two calls a round: each round's calls take the round's turn
    assert [c.turn for c in loaded.calls["a01"]] == [turns[0], turns[0], turns[1], turns[1]] and turns[0] != turns[1]


def test_the_run_figures_place_rounds_and_old_steps():
    new = {"agent": "a01", "round": 3, "step": "r3", "charge": "board", "tools": ["board_read", "board_post"]}
    work = {**new, "charge": "work", "tools": ["board_read", "shell"]}
    assert (run_figures.position_of(new), run_figures.kind_of(new), run_figures.kind_of(work)) == (3, "board", "work")
    assert run_figures.kind_of({**new, "charge": "idle"}) == "work"
    old = {"agent": "a01", "step": "r3-free2", "charge": "free", "tools": ["board_read"]}
    assert run_figures.position_of(old) == 3.4 and run_figures.kind_of(old) == "board"
    assert run_figures.position_of({"agent": "a01", "step": "r9-a01-v4", "charge": "work"}) == 9
    # a round since 2026-11-22 keeps its ending, not a charge
    now = {"agent": "a01", "round": 3, "step": "r3", "ending": None, "tools": ["board_read"]}
    assert (run_figures.kind_of(now), run_figures.kind_of({**now, "ending": "stop"}),
            run_figures.kind_of({**now, "ending": "submit"})) == ("work", "stop", "submit")


def test_the_timeline_shares_the_runs_round_number(tmp_path):
    config = pull(n_agents=2, rounds=2, concurrent_rounds=False, turn_order="round_robin")
    sample, _ = run(config, {"a01": [read(), do("A")], "a02": [do("B")]}, tmp_path)
    mine = [e for e in store(sample, "timeline") if e["agent"] == "a01"]
    # every round uses one up, a round of board calls alone included: a01's rounds are the run's rounds
    assert [(e["round"], e["used"]) for e in mine] == [(1, 1), (2, 2)] and not any("n" in e for e in mine)
    assert [i["content"].split(". ")[0] for i in tagged_infos(sample, rules.ROUND_TAG) if i["agent"] == "a01"] == [
        "Round 1 of 2", "Round 2 of 2"]
