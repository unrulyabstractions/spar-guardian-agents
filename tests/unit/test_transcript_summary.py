"""The Summary page's grouping (src/ui/transcript_summary.py): synthetic index rows, each landing in exactly one cell,
one column per influencer combination (role, level, model), the totals, a combined condition's own column, the runs
page's filters that keep exactly a cell's runs and the k of n counts; then the cached index, when one exists."""
from __future__ import annotations

import json

import pytest

from src.ui import transcript_summary as ts
from src.ui.transcript_index import CACHE, INDEX_VERSION

COL = "study_x"


def row(run: str, *, model: str = "m1", task: str = "SWE (flipt)", workers: int = 3, worker: str = "baseline",
        influencers: tuple = (), cheating: str = "none", cheated: bool = False, passed: bool = False,
        coordination: str = "low", collusion: str = "none", col: str = COL) -> dict:
    """An index row with the fields the summary reads; influencers: (role, level, model) triples."""
    return {"id": f"{col}/{run}", "col": col, "run": run, "model": model, "task_label": task, "workers": workers,
            "worker_conditioning": worker,
            "influencer_conditioning": [{"role": r, "n": 1, "level": lv, "model": m} for r, lv, m in influencers],
            "levels": {"team": {"cheating": cheating, "coordination": coordination, "collusion": collusion},
                       "cheated": cheated, "passed": passed}}


REG_E_HAIKU = (("regulator", "enforce", "haiku"),)
REG_E_LUNA = (("regulator", "enforce", "luna"),)
REG_A_HAIKU = (("regulator", "ambiguous", "haiku"),)
ROWS = [
    row("b1"), row("b2", cheating="looked"), row("b3", cheating="hacked", cheated=True, passed=True),
    row("reg_haiku_enforce1", influencers=REG_E_HAIKU),
    row("reg_haiku_enforce2", influencers=REG_E_HAIKU, cheating="tempted"),
    row("reg_luna_enforce1", influencers=REG_E_LUNA, cheating="attempted", cheated=True),
    row("reg_haiku_ambiguous1", influencers=REG_A_HAIKU, cheating="attempted", cheated=True),
    row("dis_persuade1", influencers=(("disregulator", "persuade", "luna"),), cheating="hacked", cheated=True,
        passed=True),
    row("dis_overt1", influencers=(("disregulator", "overt", "luna"),)),
    row("both1", influencers=(("disregulator", "persuade", "luna"), ("regulator", "enforce", "haiku"))),
    row("c1", worker="told to cheat", cheating="hacked", cheated=True),
    row("g1", worker="generic honesty rule"), row("e1", worker="rule naming the exploit"),
    row("cr", worker="told to cheat", influencers=REG_E_HAIKU, cheating="attempted", cheated=True),
    row("o1", worker="rule: copy no solution"),
    row("t2", task="Protein design (binder)", cheating="looked"),
    row("t2_reg_haiku_enforce1", task="Protein design (binder)", influencers=REG_E_HAIKU),
    row("m2", model="m2"),
    row("other", col="elsewhere"),
]
MINE = [r for r in ROWS if r["col"] == COL]


def cells(s: dict) -> list[tuple[tuple, str, dict]]:
    return [((r["model"], r["task"], r["workers"]), k, c) for r in s["rows"] for k, c in r["cells"].items() if c]


def cell_runs(rows: list[dict], s: dict, model: str | None, task: str | None, workers, key: str) -> list[dict]:
    """The runs that belong in a cell, found again from the rows: their condition's column and the row's keys."""
    return [r for r in rows if ts.column_of(*ts.condition_of(r))["key"] == key
            and (model is None or (r["model"] or "") == model) and (task is None or r["task_label"] == task)
            and (workers is None or r["workers"] == workers)]


def test_one_column_per_influencer_combination_in_order():
    s = ts.summary(ROWS, COL)
    labels = [c["label"] for c in s["columns"]]
    assert labels == [
        "Baseline", "Generic rule", "Rule naming the exploit",
        "+ regulator · enforce · haiku", "+ regulator · enforce · luna", "+ regulator · ambiguous · haiku",
        "Told to cheat",
        "+ disregulator · persuade · luna", "+ disregulator · overt · luna",
        "+ both · enforce, persuade · haiku, luna",
        "Rule: copy no solution", "Told to cheat + regulator · enforce · haiku"]
    groups = [c["group"] for c in s["columns"]]
    assert groups == ["baseline", "negative", "negative"] + ["regulator"] * 3 + ["positive"] + ["disregulator"] * 2 \
        + ["both", "other", "other"]
    both = s["columns"][9]
    assert both["influencer"] == "both"
    assert [(x["role"], x["level"], x["model"]) for x in both["influencers"]] == [
        ("regulator", "enforce", "haiku"), ("disregulator", "persuade", "luna")]
    # a collection without influencers or combined runs has the fixed columns only
    plain = ts.summary([r for r in ROWS if not r["influencer_conditioning"] and r["run"] != "o1"], COL)
    assert [c["key"] for c in plain["columns"]] == [
        "baseline", "generic_rule", "rule_naming_the_exploit", "told_to_cheat"]


def test_each_run_lands_in_exactly_one_cell_and_totals_match():
    s = ts.summary(ROWS, COL)
    assert s["total"] == len(MINE) == 18
    assert sum(c["n"] for _, _, c in cells(s)) == s["total"]
    assert sum(c["n"] for c in s["totals"].values() if c) == s["total"]
    seen: list[str] = []
    for (model, task, workers), key, c in cells(s):
        runs = cell_runs(MINE, s, model, task, workers, key)
        assert len(runs) == c["n"]
        seen += [r["id"] for r in runs]
    assert sorted(seen) == sorted(r["id"] for r in MINE)
    for key, c in s["totals"].items():
        assert (c["n"] if c else 0) == len(cell_runs(MINE, s, None, None, None, key))


def test_every_cell_link_keeps_exactly_its_runs():
    """A cell's filters, read as the runs page reads them, keep exactly its runs; the model is the raw model."""
    s = ts.summary(ROWS, COL)
    for (model, task, workers), key, c in cells(s) + [((None, None, None), k, c) for k, c in s["totals"].items() if c]:
        f = c["filters"]
        assert f is not None, key
        kept = {r["id"] for r in ROWS if ts.runs_page_keeps(r, f)}
        assert kept == {r["id"] for r in cell_runs(MINE, s, model, task, workers, key)}
    main = next(r for r in s["rows"] if (r["model"], r["task"]) == ("m1", "SWE (flipt)"))["cells"]
    assert main["baseline | regulator · enforce · haiku"]["filters"] == {
        "col": COL, "task": "SWE (flipt)", "model": "m1", "workers": "3", "worker": "baseline",
        "influencer": "regulator",
        "influencer_level": "regulator: enforce", "q": "haiku"}
    assert "q" not in main["baseline | regulator · ambiguous · haiku"]["filters"]
    assert main["baseline"]["filters"] == {"col": COL, "task": "SWE (flipt)", "model": "m1", "workers": "3",
                                           "worker": "baseline", "influencer": "none"}


def test_a_cell_no_filter_picks_out_has_no_link():
    rows = [row("z_reg", influencers=REG_E_HAIKU), row("z_reg_x", influencers=REG_E_LUNA)]
    s = ts.summary(rows, COL)
    got = {k: c["filters"] for k, c in s["rows"][0]["cells"].items() if c}
    assert got["baseline | regulator · enforce · haiku"] is None
    assert got["baseline | regulator · enforce · luna"]["q"] == "x"


def test_rows_split_by_team_size_always_and_by_seed_pack_only_when_a_run_is_seeded():
    s = ts.summary(ROWS, COL)
    assert s["by_seed"] is False
    assert [(r["model"], r["task"], r["workers"], r["seed_pack"]) for r in s["rows"]] == [
        ("m1", "Protein design (binder)", 3, None), ("m1", "SWE (flipt)", 3, None), ("m2", "SWE (flipt)", 3, None)]
    assert all(c["filters"] is None or ("workers" in c["filters"] and "seed_pack" not in c["filters"])
               for _, _, c in cells(s))
    mixed = ts.summary(ROWS + [row("s4", workers=4)], COL)
    assert [(r["model"], r["task"], r["workers"]) for r in mixed["rows"]] == [
        ("m1", "Protein design (binder)", 3), ("m1", "SWE (flipt)", 3), ("m1", "SWE (flipt)", 4),
        ("m2", "SWE (flipt)", 3)]
    four = mixed["rows"][2]["cells"]["baseline"]
    assert four["n"] == 1 and four["filters"]["workers"] == "4"
    seeded = ts.summary(ROWS + [dict(row("sd1", cheating="hacked", cheated=True), seed_pack="swe/pressure")], COL)
    assert seeded["by_seed"] is True
    assert [(r["task"], r["model"], r["seed_pack"]) for r in seeded["rows"]] == [
        ("Protein design (binder)", "m1", "none"), ("SWE (flipt)", "m1", "none"), ("SWE (flipt)", "m1", "swe/pressure"),
        ("SWE (flipt)", "m2", "none")]
    pressure = seeded["rows"][2]["cells"]["baseline"]
    assert pressure["n"] == 1 and pressure["cheated"] == 1 and pressure["filters"]["seed_pack"] == "swe/pressure"
    assert seeded["rows"][1]["cells"]["baseline"]["n"] == 3


def test_k_of_n_counts_and_level_counts():
    s = ts.summary(ROWS, COL)
    main = next(r for r in s["rows"] if (r["model"], r["task"]) == ("m1", "SWE (flipt)"))["cells"]
    base = main["baseline"]
    assert (base["n"], base["cheated"], base["accessed"], base["passed"]) == (3, 1, 2, 1)
    assert base["levels"]["cheating"] == [["none", 1], ["looked", 1], ["hacked", 1]]
    assert base["levels"]["coordination"] == [["low", 3]]
    reg = main["baseline | regulator · enforce · haiku"]
    assert (reg["n"], reg["cheated"], reg["accessed"], reg["passed"]) == (2, 0, 1, 0)
    assert "influencer_levels" not in reg
    total = s["totals"]["baseline | regulator · enforce · haiku"]
    assert total["n"] == 3 and "task" not in total["filters"]


def test_every_group_has_a_band_name():
    s = ts.summary(ROWS, COL)
    assert all(c["group"] in s["groups"] for c in s["columns"])
    assert s["groups"][ts.COLUMNS[0]["group"]] == ts.COLUMNS[0]["label"] == "Baseline"


def test_the_runs_page_level_words_match():
    """An influencer_level filter value is the runs page's Influencer conditioning value (tv_view_index.js)."""
    assert ts.influencer_level({"role": "regulator", "level": None}) == "regulator: no level"
    assert ts.influencer_level({"role": "regulator", "level": "enforce"}) == "regulator: enforce"


def test_the_cached_index_lands_every_valid_run_in_one_cell():
    """The index the viewer last built, when it is of this version: every collection's cells hold its valid runs, and
    every cell's filters keep exactly its runs."""
    if not CACHE.is_file():
        pytest.skip("no index cache")
    idx = json.loads(CACHE.read_text())
    if idx.get("version") != INDEX_VERSION:
        pytest.skip("the index cache is of another version")
    for c in idx["collections"]:
        s = ts.summary(idx["rows"], c["key"])
        mine = [r for r in idx["rows"] if r["col"] == c["key"]]
        assert s["total"] == c["runs"]
        assert sum(x["n"] for _, _, x in cells(s)) == c["runs"]
        for (model, task, workers), key, x in cells(s):
            if x["filters"] is not None:
                kept = {r["id"] for r in idx["rows"] if ts.runs_page_keeps(r, x["filters"])}
                assert kept == {r["id"] for r in cell_runs(mine, s, model, task, workers, key)}
