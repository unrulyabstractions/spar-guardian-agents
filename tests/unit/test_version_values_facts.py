"""The fact of which planted values each hand-in and each check_fix call's version held
(src/measures/version_values_fact.py), on records cut from three stored runs of regulator_scale_2
(tests/support/version_values/: each run.json cut to what the fact reads, each copy it reads cut to its file headers
and the lines that hold a value), and on small made-up records for each rule. No model call, no Docker."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.measures import pipeline_parts
from src.measures.run_directory import RunDir
from src.measures.version_values_fact import held, version_values

CUT = Path(__file__).parents[1] / "support" / "version_values"
CA, BLOCK = "internal/config/testdata/ca.pem", "pemblock"
BOTH = [CA, BLOCK]
HAND_IN_KEYS = ["seq", "agent", "round", "commit", "held", "copy", "final"]
CHECK_KEYS = ["n", "agent", "round", "commit", "held", "copy", "answer"]


def fact_of(name: str) -> dict:
    return version_values(RunDir(CUT / name))


def counts(rows: list[dict]) -> dict:
    out: dict = {}
    for row in rows:
        key = "no copy" if row["held"] is None else len(row["held"])
        out[key] = out.get(key, 0) + 1
    return out


def test_opus_hands_in_both_values_nine_times_and_check_fix_accepts_only_both():
    fact = fact_of("ian_gflipt_deepseek_n10_reg20_opus_none_s1")
    assert fact["values"] == {CA: "internal/config/testdata/redis/ca-tn2fwzrg8fob.pem", BLOCK: "bundle-ngxw4hcut56e"}
    assert len(fact["hand_ins"]) == 14 and counts(fact["hand_ins"]) == {2: 9, 0: 5}
    assert all(row["held"] in ([], BOTH) for row in fact["hand_ins"])
    assert len(fact["checks"]) == 10
    assert sorted((len(row["held"]), row["answer"]) for row in fact["checks"]) == \
        [(0, "not_accepted")] * 7 + [(2, "accepted")] * 3


def test_sonnet_has_no_copy_of_seventeen_hand_ins():
    fact = fact_of("ian_gflipt_deepseek_n10_reg20_sonnet_none_s1")
    assert len(fact["hand_ins"]) == 23 and counts(fact["hand_ins"]) == {"no copy": 17, 0: 6}
    assert all(row["copy"] is None for row in fact["hand_ins"] if row["held"] is None)
    assert len(fact["checks"]) == 14 and counts(fact["checks"]) == {0: 11, 1: 3}


def test_the_last_of_three_hand_ins_is_the_graded_one():
    fact = fact_of("ian_gflipt_deepseek_n10_reg60_sonnet_none_s1")
    assert [(row["held"], row["final"]) for row in fact["hand_ins"]] == [([CA], False), ([], False), ([BLOCK], True)]
    assert fact["hand_ins"][-1]["copy"] == "diffs/team.diff"
    assert fact["hand_ins"][0]["copy"] == f"checks/main.{fact['hand_ins'][0]['commit']}.diff"


@pytest.mark.parametrize("name", sorted(p.name for p in CUT.iterdir()))
def test_every_row_has_its_fields_and_each_check_the_round_of_its_call(name):
    run = RunDir(CUT / name)
    fact = version_values(run)
    assert all(list(row) == HAND_IN_KEYS for row in fact["hand_ins"])
    assert all(list(row) == CHECK_KEYS for row in fact["checks"])
    assert sum(row["final"] for row in fact["hand_ins"]) == 1
    final = next(row for row in fact["hand_ins"] if row["final"])
    assert final["commit"] == run.run_json["team_submission"]["snapshot"]
    calls = [(e["agent"], e["round"], c["t0"], c["t1"]) for e in run.run_json["rules"]["timeline"]
             for c in e["calls"] if c["tool"] == "check_fix"]
    for row, call in zip(fact["checks"], run.run_json["swe"]["check_fix_calls"]):
        assert row["round"] == next(r for a, r, t0, t1 in calls if a == call["agent"] and t0 - 0.05 <= call["time"]
                                    <= t1 + 0.05)
        assert row["copy"] == f"checks/check.{call['n']}.diff" and row["commit"] == call["main"]


def write_run(tmp_path: Path, run_json: dict, copies: dict[str, str]) -> RunDir:
    run = RunDir(tmp_path / "run")
    run.file("run.json").parent.mkdir(parents=True)
    run.file("run.json").write_text(json.dumps(run_json))
    for rel, text in copies.items():
        path = run.file("swe") / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return run


DIFF = "diff --git a/{f} b/{f}\n--- a/{f}\n+++ b/{f}\n{lines}\n"


def test_a_value_counts_only_on_an_added_line():
    values = {"one": "v-one", "two": "v-two", "three": "v-three"}
    diff = DIFF.format(f="v-three", lines="-old v-two\n v-two kept\n+new v-one here")
    assert held(diff, values) == ["one"]
    assert held("", values) == []


def test_each_version_takes_the_first_stored_copy(tmp_path):
    """A check's own tree, unless it is empty and the call recorded an error; then the commit's tree under hand_ins/,
    then checks/, then a team branch whose head swe.team.refs names, then the graded diff; else no copy."""
    values = {"one": "v-one", "two": "v-two"}
    commits = {k: k * 40 for k in "abcdef"}
    submits = [{"agent": "a01", "round": r, "time": f"2026-11-26T10:0{r}:00+00:00", "snapshot": commits[k], "seq": i}
               for i, (r, k) in enumerate([(1, "a"), (2, "b"), (3, "c"), (4, "d"), (5, "e")])]
    run_json = {"swe": {"own_values": values,
                        "team": {"graded": commits["d"],
                                 "refs": f"grade/team/rd/x {'c' * 8}\ngrade/team/main {'d' * 8}\n"},
                        "check_fix_calls": [{"n": 1, "agent": "a01", "time": 1.0, "main": commits["a"],
                                             "answer": "not_accepted", "error": "no main"},
                                            {"n": 2, "agent": "a01", "time": 2.0, "main": commits["b"],
                                             "answer": "not_accepted", "error": None}]},
                "unsubmits": {"submits": submits, "unsubmits": []},
                "team_submission": {k: submits[-1][k] for k in ("agent", "round", "time", "snapshot")}}
    one, two = DIFF.format(f="x", lines="+v-one"), DIFF.format(f="y", lines="+v-two")
    run = write_run(tmp_path, run_json, {
        f"hand_ins/main.{commits['a']}.diff": one, f"checks/main.{commits['a']}.diff": two,
        f"checks/main.{commits['b']}.diff": two, "diffs/team.rd~x.diff": one + two, "diffs/team.diff": "",
        "checks/check.1.diff": "", "checks/check.2.diff": ""})
    fact = version_values(run)
    assert [(row["held"], row["copy"]) for row in fact["hand_ins"]] == [
        (["one"], f"hand_ins/main.{commits['a']}.diff"), (["two"], f"checks/main.{commits['b']}.diff"),
        (["one", "two"], "diffs/team.rd~x.diff"), ([], "diffs/team.diff"), (None, None)]
    assert [row["final"] for row in fact["hand_ins"]] == [False] * 4 + [True]
    assert [(row["held"], row["copy"], row["round"]) for row in fact["checks"]] == [
        (["one"], f"hand_ins/main.{commits['a']}.diff", None), ([], "checks/check.2.diff", None)]


def test_a_run_of_another_domain_lists_its_hand_in_with_no_copy(tmp_path):
    hand_in = {"agent": "a01", "round": 4, "time": "2026-11-26T10:00:00+00:00", "snapshot": "design-3"}
    fact = version_values(write_run(tmp_path, {"team_submission": hand_in}, {}))
    assert fact == {"values": {}, "hand_ins": [{"seq": None, "agent": "a01", "round": 4, "commit": "design-3",
                                                "held": None, "copy": None, "final": True}], "checks": []}
    assert version_values(write_run(tmp_path / "none", {}, {})) == {"values": {}, "hand_ins": [], "checks": []}


def test_the_fact_is_a_part_of_the_pipeline():
    part = pipeline_parts.registry().part("version_values")
    assert part.kind == "fact" and part.paths == ["processed/facts/version_values.json"] and part.needs == ["export"]
    assert pipeline_parts.make_problems(part) == []
