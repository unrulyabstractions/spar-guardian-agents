"""The Summary page's grouping (src/ui/transcript_summary.py): synthetic index rows, each landing in exactly one cell,
one column per influencer combination (role, level, model), the totals, a combined condition's own column, the runs
page's filters that keep exactly a cell's runs and the k of n counts; then the cached index, when one exists."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.configuration.settings import ROOT
from src.ui import transcript_summary as ts
from src.ui.transcript_index import alerts_shown, build_index

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
    return [((r["model"], r["task"], r["size"]), k, c) for r in s["rows"] for k, c in r["cells"].items() if c]


def cell_runs(rows: list[dict], s: dict, model: str | None, task: str | None, size, key: str) -> list[dict]:
    """The runs that belong in a cell, found again from the rows: their condition's column and the row's keys."""
    size_of = ts.team_size if s["rows_by"] == "agents" else (lambda r: r["workers"])
    return [r for r in rows if ts.column_of(*ts.condition_of(r))["key"] == key
            and (model is None or (r["model"] or "") == model) and (task is None or r["task_label"] == task)
            and (size is None or size_of(r) == size)]


def test_one_column_per_influencer_combination_in_order():
    """The rows hold stored runs' levels. A level no longer in the ladder sorts after the ladder's, by name."""
    assert ts.level_rank("regulator", "generic") < ts.level_rank("regulator", "instructed") \
        < ts.level_rank("regulator", "ambiguous") < ts.level_rank("regulator", "enforce")
    s = ts.summary(ROWS, COL)
    labels = [c["label"] for c in s["columns"]]
    assert labels == [
        "Baseline", "Generic rule", "Rule naming the exploit",
        "+ 25% regulators · ambiguous · haiku", "+ 25% regulators · enforce · haiku",
        "+ 25% regulators · enforce · luna",
        "Positive control",
        "+ 25% disregulators · overt · luna", "+ 25% disregulators · persuade · luna",
        "+ both · enforce, persuade · haiku, luna",
        "Rule: copy no solution", "Positive control + 25% regulators · enforce · haiku"]
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
    assert main["baseline | 25% regulators · enforce · haiku"]["filters"] == {
        "col": COL, "task": "SWE (flipt)", "model": "m1", "workers": "3", "worker": "baseline",
        "influencer": "regulator",
        "influencer_level": "regulator: enforce", "q": "haiku"}
    assert "q" not in main["baseline | 25% regulators · ambiguous · haiku"]["filters"]
    assert main["baseline"]["filters"] == {"col": COL, "task": "SWE (flipt)", "model": "m1", "workers": "3",
                                           "worker": "baseline", "influencer": "none"}


def test_a_cell_no_filter_picks_out_has_no_link():
    rows = [row("z_reg", influencers=REG_E_HAIKU), row("z_reg_x", influencers=REG_E_LUNA)]
    s = ts.summary(rows, COL)
    got = {k: c["filters"] for k, c in s["rows"][0]["cells"].items() if c}
    assert got["baseline | 25% regulators · enforce · haiku"] is None
    assert got["baseline | 25% regulators · enforce · luna"]["q"] == "x"


def test_rows_split_by_team_size_always_and_by_seed_pack_only_when_a_run_is_seeded():
    s = ts.summary(ROWS, COL)
    assert s["by_seed"] is False
    assert [(r["model"], r["task"], r["size"], r["seed_pack"]) for r in s["rows"]] == [
        ("m1", "Protein design (binder)", 3, None), ("m1", "SWE (flipt)", 3, None), ("m2", "SWE (flipt)", 3, None)]
    assert all(c["filters"] is None or ("workers" in c["filters"] and "seed_pack" not in c["filters"])
               for _, _, c in cells(s))
    mixed = ts.summary(ROWS + [row("s4", workers=4)], COL)
    assert [(r["model"], r["task"], r["size"]) for r in mixed["rows"]] == [
        ("m1", "Protein design (binder)", 3), ("m1", "SWE (flipt)", 3), ("m1", "SWE (flipt)", 4),
        ("m2", "SWE (flipt)", 3)]
    four = mixed["rows"][2]["cells"]["baseline"]
    assert four["n"] == 1 and four["filters"]["workers"] == "4"
    pack = "swe/revealing"
    seeded = ts.summary(ROWS + [dict(row("sd1", cheating="hacked", cheated=True), seed_pack=pack)], COL)
    assert seeded["by_seed"] is True
    assert [(r["task"], r["model"], r["seed_pack"]) for r in seeded["rows"]] == [
        ("Protein design (binder)", "m1", "none"), ("SWE (flipt)", "m1", "none"),
        ("SWE (flipt)", "m1", pack), ("SWE (flipt)", "m2", "none")]
    pressure = seeded["rows"][2]["cells"]["baseline"]
    assert pressure["n"] == 1 and pressure["cheated"] == 1 and pressure["filters"]["seed_pack"] == pack
    assert seeded["rows"][1]["cells"]["baseline"]["n"] == 3


def test_k_of_n_counts_and_level_counts():
    s = ts.summary(ROWS, COL)
    main = next(r for r in s["rows"] if (r["model"], r["task"]) == ("m1", "SWE (flipt)"))["cells"]
    base = main["baseline"]
    assert (base["n"], base["cheated"], base["accessed"], base["passed"]) == (3, 1, 2, 1)
    assert base["levels"]["cheating"] == [["none", 1], ["looked", 1], ["hacked", 1]]
    assert base["levels"]["coordination"] == [["low", 3]]
    reg = main["baseline | 25% regulators · enforce · haiku"]
    assert (reg["n"], reg["cheated"], reg["accessed"], reg["passed"]) == (2, 0, 1, 0)
    assert "influencer_levels" not in reg
    total = s["totals"]["baseline | 25% regulators · enforce · haiku"]
    assert total["n"] == 3 and "task" not in total["filters"]


def test_every_group_has_a_band_name():
    s = ts.summary(ROWS, COL)
    assert all(c["group"] in s["groups"] for c in s["columns"])
    assert s["groups"][ts.COLUMNS[0]["group"]] == ts.COLUMNS[0]["label"] == "Baseline"


def test_the_runs_page_level_words_match():
    """An influencer_level filter value is the runs page's Influencer conditioning value (tv_view_index.js)."""
    assert ts.influencer_level({"role": "regulator", "level": None}) == "regulator: no level"
    assert ts.influencer_level({"role": "regulator", "level": "enforce"}) == "regulator: enforce"


def test_the_stored_index_lands_every_valid_run_in_one_cell():
    """The index of what processing stored under runs/: every collection's cells hold its valid runs, and every
    cell's filters keep exactly its runs."""
    idx = build_index(ROOT / "runs") if (ROOT / "runs").is_dir() else {"rows": []}
    if not idx["rows"]:
        pytest.skip("no processed run under runs/")
    for c in idx["collections"]:
        s = ts.summary(idx["rows"], c["key"])
        mine = [r for r in idx["rows"] if r["col"] == c["key"]]
        # the summary holds every task's rows; the page leaves out the tasks its Settings turn off
        assert s["total"] == len(mine) == c["runs"] + c["other_tasks"]
        assert sum(x["n"] for _, _, x in cells(s)) == len(mine)
        for row in s["rows"]:
            for key, x in row["cells"].items():
                if x is None or x["filters"] is None:
                    continue
                kept = {r["id"] for r in idx["rows"] if ts.runs_page_keeps(r, x["filters"])}
                want = cell_runs(mine, s, row["model"], row["task"], row["size"], key)
                if s["by_seed"]:        # a seeded collection's rows split by seed pack too
                    want = [r for r in want if r.get("seed_pack", "none") == row["seed_pack"]]
                assert kept == {r["id"] for r in want} and len(kept) == x["n"], (c["key"], row["task"], key)


def test_a_run_starts_at_its_eval_logs_time_so_collections_list_newest_first():
    from src.ui.transcript_index import started
    assert started({"eval_log": "logs/s/x/2026-11-22T13-25-43-00-00_protein_ABC.eval"}) == "2026-11-22T13-25-43"
    assert started({"eval_log": None}) == "" and started({}) == ""
    assert sorted(["2026-10-30T14-02-00", "2026-11-22T13-25-43", ""], reverse=True)[0] == "2026-11-22T13-25-43"


def test_a_bands_only_column_shown_takes_the_bands_singular_name():
    """One negative control shown reads "Negative control" with no band over it, like the positive control; the page
    takes the name from the payload."""
    s = ts.summary(ROWS, COL)
    assert s["alone"] == {"negative": "Negative control"}
    page = (Path(ts.__file__).parent / "transcript_static" / "tv_view_summary.js").read_text()
    assert "s.alone[C[i].group]" in page and "colHead({ ...C[i], label: alone }, { rowspan: '2' })" in page


def test_the_summary_page_filters_by_seed_where_runs_differ_by_seed_pack():
    """A Seed picker beside the task and model pickers, only in a collection whose runs differ by seed pack; it
    filters the rows, travels in the page's address and makes the All row sum the rows shown."""
    page = (Path(ts.__file__).parent / "transcript_static" / "tv_view_summary.js").read_text()
    for part in ("const seed = s.by_seed ? known(q.get('seed') || '', s.rows.map(r => seedName(r.seed_pack))) : '';",
                 "&& (!seed || seedName(r.seed_pack) === seed));", "task, wmodel, imodel, seed,", "['', 'All seeds']",
                 "[...new Set(s.rows.map(r => seedName(r.seed_pack)))]",
                 "picker, tPick, wPick, iPick, sPick,", "wmodel || task || seed || !allShown"):
        assert part in page, part


def test_a_cell_counts_the_runs_that_alerted_humans_and_their_alerts():
    """A cell holds how many of its runs had an alert (alerted) and the alerts summed (all, true, false): the page's
    row A in a column with influencers. A row stored without the field counts as none."""
    reg = (("regulator", "generic", "terra"),)
    rows = [row("a", influencers=reg), row("b", influencers=reg), row("c", influencers=reg)]
    rows[0]["alerts"] = {"n": 3, "true": 0, "false": 3}
    rows[1]["alerts"] = {"n": 0, "true": 0, "false": 0}
    got = ts.cell(rows)
    assert (got["n"], got["alerted"], got["alerts"]) == (3, 1, {"n": 3, "true": 0, "false": 3})
    rows[2]["alerts"] = {"n": 2, "true": 1, "false": 0}                      # one alert of unknown outcome
    assert ts.cell(rows)["alerted"] == 2 and ts.cell(rows)["alerts"] == {"n": 5, "true": 1, "false": 3}
    page = (ROOT / "src/ui/transcript_static/tv_view_summary.js").read_text()
    assert "col && col.influencers.length ? alertShare(c, detailed) : null" in page      # only where influencers are
    assert "cellView(cs[c.key], ladders, detailed, c)" in page


def test_a_rows_alerts_are_the_runs_signal_counts():
    """The index row's alerts: the team row's alerts, true_alarms and false_alarms of run.json signals, zeros on a run
    whose influencers never alerted (the team row then holds none of the three); the runs page draws them in the
    Influencers band, a dash on a run with no influencer."""
    team = {"alerts": 3, "true_alarms": 0, "false_alarms": 3}
    assert alerts_shown({"signals": {"team": team}}) == {"n": 3, "true": 0, "false": 3}
    assert alerts_shown({"signals": {"team": {}}}) == {"n": 0, "true": 0, "false": 0}
    assert alerts_shown({}) == {"n": 0, "true": 0, "false": 0}
    page = (ROOT / "src/ui/transcript_static/tv_view_index.js").read_text()
    assert "{ key: 'alerts', label: 'Alerts', group: 'influencers'," in page
    assert "title: 'Times an influencer alerted humans'," in page
    assert "cell: r => (r.influencer_conditioning.length ? alertsCell(r.alerts) : none()) }" in page


def test_influencers_of_one_share_stand_in_one_column_whatever_the_teams_size():
    """1 regulator of 5 agents and 4 of 20 are both 20% of their team, so they share a column; 3 of 5 are 60%. Each
    cell says how many influencers of each role its runs hold."""
    def team(run: str, workers: int, regulators: int) -> dict:
        out = row(run, workers=workers, influencers=REG_E_HAIKU)
        out["influencer_conditioning"][0]["n"] = regulators
        return out
    regulated = [team("n5_reg20", 4, 1), team("n20_reg20", 16, 4), team("n5_reg60", 2, 3)]
    assert ts.summary(regulated, COL)["rows_by"] == "workers"          # no team without influencers to stand beside
    s = ts.summary([row("n5", workers=5), row("n20", workers=20), *regulated], COL)
    fifth, most = "baseline | 20% regulators · enforce · haiku", "baseline | 60% regulators · enforce · haiku"
    assert [c["key"] for c in s["columns"] if c["influencers"]] == [fifth, most]
    assert [c["influencers"][0]["share"] for c in s["columns"] if c["influencers"]] == [20, 60]
    # the regulators took workers' places (a team of 5 holds 4 workers or 2), so a row is the teams of one size
    assert s["rows_by"] == "agents" and [r["size"] for r in s["rows"]] == [5, 20]
    assert ts.summary(ROWS, COL)["rows_by"] == "workers"
    cells = {r["size"]: r["cells"] for r in s["rows"]}
    assert cells[5][fifth]["team"] == {"worker": [4], "regulator": [1], "disregulator": [0]}
    assert cells[5][most]["team"] == {"worker": [2], "regulator": [3], "disregulator": [0]}
    assert cells[20][fifth]["team"] == {"worker": [16], "regulator": [4], "disregulator": [0]}
    assert cells[20][most] is None and "workers" not in cells[5][fifth]["filters"]
    assert s["totals"][fifth]["n"] == 2 and s["totals"][fifth]["team"]["regulator"] == [1, 4]
