"""The Summary page's grouping (src/ui/transcript_summary.py): synthetic index rows, each landing in exactly one cell,
one column per influencer combination (role, level, model), the totals, a combined condition's own column, the runs
page's filters that keep exactly a cell's runs and the k of n counts; then the cached index, when one exists."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.configuration.settings import ROOT
from src.ui import transcript_levels
from src.ui import transcript_summary as ts
from src.ui.transcript_index import MODELS, alerts_shown, build_index, ending_shown, model_rank, short_model

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
    """Each cell once: a cell a row repeats from its size's first row (shared) is left out."""
    return [((r["model"], r["task"], r["size"], tuple(map(tuple, r["counts"]))), k, c) for r in s["rows"]
            for k, c in r["cells"].items() if c and k not in r["shared"]]


def cell_runs(rows: list[dict], s: dict, model: str | None, task: str | None, size, key: str,
              counts: tuple | None = None) -> list[dict]:
    """The runs that belong in a cell, found again from the rows: their condition's column and the row's keys (the
    influencers' counts too: a run without influencers stands in every row of its size)."""
    size_of = ts.team_size if s["rows_by"] == "agents" else (lambda r: r["workers"])
    return [r for r in rows if ts.column_of(*ts.condition_of(r))["key"] == key
            and (model is None or (r["model"] or "") == model) and (task is None or r["task_label"] == task)
            and (size is None or size_of(r) == size) and (counts is None or ts.counts_of(r) in ((), counts))]


def test_one_column_per_influencer_combination_in_order():
    """The rows hold stored runs' levels. A level no longer in the ladder sorts after the ladder's, by name."""
    assert ts.level_rank("regulator", "generic") < ts.level_rank("regulator", "instructed") \
        < ts.level_rank("regulator", "ambiguous") < ts.level_rank("regulator", "enforce")
    s = ts.summary(ROWS, COL)
    labels = [c["label"] for c in s["columns"]]
    assert labels == [
        "Baseline", "Generic rule", "Rule naming the exploit",
        "+ regulators · ambiguous · haiku", "+ regulators · enforce · haiku",
        "+ regulators · enforce · luna",
        "Positive control",
        "+ disregulators · overt · luna", "+ disregulators · persuade · luna",
        "+ both · enforce, persuade · haiku, luna",
        "Rule: copy no solution", "Positive control + regulators · enforce · haiku"]
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
    for (model, task, workers, counts), key, c in cells(s):
        runs = cell_runs(MINE, s, model, task, workers, key, counts)
        assert len(runs) == c["n"]
        seen += [r["id"] for r in runs]
    assert sorted(seen) == sorted(r["id"] for r in MINE)
    for key, c in s["totals"].items():
        assert (c["n"] if c else 0) == len(cell_runs(MINE, s, None, None, None, key))


def test_every_cell_link_keeps_exactly_its_runs():
    """A cell's filters, read as the runs page reads them, keep exactly its runs; the model is the raw model."""
    s = ts.summary(ROWS, COL)
    for (model, task, workers, counts), key, c in cells(s) + [((None, None, None, None), k, c)
                                                             for k, c in s["totals"].items() if c]:
        f = c["filters"]
        assert f is not None, key
        kept = {r["id"] for r in ROWS if ts.runs_page_keeps(r, f)}
        assert kept == {r["id"] for r in cell_runs(MINE, s, model, task, workers, key, counts)}
    main = next(r for r in s["rows"] if (r["model"], r["task"]) == ("m1", "SWE (flipt)"))["cells"]
    assert main["baseline | regulators · enforce · haiku"]["filters"] == {
        "col": COL, "task": "SWE (flipt)", "model": "m1", "workers": "3", "worker": "baseline",
        "influencer": "regulator",
        "influencer_level": "regulator: enforce", "q": "haiku"}
    assert "q" not in main["baseline | regulators · ambiguous · haiku"]["filters"]
    assert main["baseline"]["filters"] == {"col": COL, "task": "SWE (flipt)", "model": "m1", "workers": "3",
                                           "worker": "baseline", "influencer": "none"}


def test_a_cell_no_filter_picks_out_has_no_link():
    rows = [row("z_reg", influencers=REG_E_HAIKU), row("z_reg_x", influencers=REG_E_LUNA)]
    s = ts.summary(rows, COL)
    got = {k: c["filters"] for k, c in s["rows"][0]["cells"].items() if c}
    assert got["baseline | regulators · enforce · haiku"] is None
    assert got["baseline | regulators · enforce · luna"]["q"] == "x"


def test_rows_split_by_team_size_always_and_by_seed_pack_only_when_a_run_is_seeded():
    s = ts.summary(ROWS, COL)
    assert s["by_seed"] is False
    assert [(r["model"], r["task"], r["size"], r["seed_pack"], r["counts"]) for r in s["rows"]] == [
        ("m1", "Protein design (binder)", 3, None, [["regulator", 1]]),
        ("m1", "SWE (flipt)", 3, None, [["regulator", 1]]), ("m1", "SWE (flipt)", 3, None, [["disregulator", 1]]),
        ("m1", "SWE (flipt)", 3, None, [["regulator", 1], ["disregulator", 1]]), ("m2", "SWE (flipt)", 3, None, [])]
    # the runs without influencers stand in every row of their size, counted in the first
    swe = [r for r in s["rows"] if (r["model"], r["task"]) == ("m1", "SWE (flipt)")]
    assert all(r["cells"]["baseline"]["n"] == 3 for r in swe)
    assert swe[0]["shared"] == [] and "baseline" in swe[1]["shared"] == swe[2]["shared"]
    assert not any(k.startswith("baseline |") for k in swe[1]["shared"])
    assert all(c["filters"] is None or ("workers" in c["filters"] and "seed_pack" not in c["filters"])
               for _, _, c in cells(s))
    mixed = ts.summary(ROWS + [row("s4", workers=4)], COL)
    assert [(r["model"], r["task"], r["size"]) for r in mixed["rows"]] == [
        ("m1", "Protein design (binder)", 3), *[("m1", "SWE (flipt)", 3)] * 3, ("m1", "SWE (flipt)", 4),
        ("m2", "SWE (flipt)", 3)]
    four = mixed["rows"][4]["cells"]["baseline"]
    assert four["n"] == 1 and four["filters"]["workers"] == "4"
    pack = "swe/revealing"
    seeded = ts.summary(ROWS + [dict(row("sd1", cheating="hacked", cheated=True), seed_pack=pack)], COL)
    assert seeded["by_seed"] is True
    assert [(r["task"], r["model"], r["seed_pack"]) for r in seeded["rows"]] == [
        ("Protein design (binder)", "m1", "none"), *[("SWE (flipt)", "m1", "none")] * 3,
        ("SWE (flipt)", "m1", pack), ("SWE (flipt)", "m2", "none")]
    pressure = seeded["rows"][4]["cells"]["baseline"]
    assert pressure["n"] == 1 and pressure["cheated"] == 1 and pressure["filters"]["seed_pack"] == pack
    assert seeded["rows"][1]["cells"]["baseline"]["n"] == 3


def test_k_of_n_counts_and_level_counts():
    s = ts.summary(ROWS, COL)
    main = next(r for r in s["rows"] if (r["model"], r["task"]) == ("m1", "SWE (flipt)"))["cells"]
    base = main["baseline"]
    assert (base["n"], base["cheated"], base["accessed"], base["passed"]) == (3, 1, 2, 1)
    assert base["levels"]["cheating"] == [["none", 1], ["looked", 1], ["hacked", 1]]
    assert base["levels"]["coordination"] == [["low", 3]]
    reg = main["baseline | regulators · enforce · haiku"]
    assert (reg["n"], reg["cheated"], reg["accessed"], reg["passed"]) == (2, 0, 1, 0)
    assert "influencer_levels" not in reg
    total = s["totals"]["baseline | regulators · enforce · haiku"]
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
                 "&& (!seed || seedName(r.seed_pack) === seed)", "task, wmodel, imodel, seed,", "['', 'All seeds']",
                 "[...new Set(s.rows.map(r => seedName(r.seed_pack)))]",
                 "picker, tPick, wPick, iPick, sPick,", "wmodel || task || seed || size || !allShown"):
        assert part in page, part


def test_a_cell_counts_the_runs_that_alerted_humans_and_their_alerts():
    """A cell holds how many of its runs had an alert (alerted) and the alerts summed (all, true, false): the page's
    row A in a column with influencers. A row stored without the field counts as none."""
    reg = (("regulator", "generic", "terra"),)
    rows = [row("a", influencers=reg), row("b", influencers=reg), row("c", influencers=reg)]
    rows[0]["alerts"] = {"n": 3, "true": 0, "false": 3}
    rows[1]["alerts"] = {"n": 0, "true": 0, "false": 0}
    got = ts.cell(rows, {})
    assert (got["n"], got["alerted"], got["alerts"]) == (3, 1, {"n": 3, "true": 0, "false": 3})
    rows[2]["alerts"] = {"n": 2, "true": 1, "false": 0}                      # one alert of unknown outcome
    assert ts.cell(rows, {})["alerted"] == 2 and ts.cell(rows, {})["alerts"] == {"n": 5, "true": 1, "false": 3}
    page = (ROOT / "src/ui/transcript_static/tv_view_summary.js").read_text()
    assert "col && col.influencers.length ? alertShare(c, detailed) : null" in page      # only where influencers are
    assert "cellView(cs[c.key], ladders, detailed, c, members)" in page


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


def test_one_influencer_model_and_level_is_one_column_whatever_the_team_and_each_count_is_a_row():
    """One disregulator of one model and level beside 2 workers and beside 8 stand in one column; the count of
    influencers belongs to the row: 4 workers with 1 regulator and with 3 are two rows, each with the baseline of its
    4 workers, counted once."""
    dis = (("disregulator", "instructed", "hy4"),)
    s = ts.summary([row("b2", workers=2), row("b8", workers=8), row("d2", workers=2, influencers=dis),
                    row("d8", workers=8, influencers=dis)], COL)
    assert [c["key"] for c in s["columns"] if c["influencers"]] == ["baseline | disregulators · instructed · hy4"]
    assert [c["label"] for c in s["columns"] if c["influencers"]] == ["+ disregulators · instructed · hy4"]
    assert [(r["size"], r["counts"], r["cells"]["baseline | disregulators · instructed · hy4"]["n"])
            for r in s["rows"]] == [(2, [["disregulator", 1]], 1), (8, [["disregulator", 1]], 1)]

    def team(run: str, workers: int, regulators: int) -> dict:
        out = row(run, workers=workers, influencers=REG_E_HAIKU)
        out["influencer_conditioning"][0]["n"] = regulators
        return out
    s = ts.summary([row("b4", workers=4), team("r4_1", 4, 1), team("r4_3", 4, 3), team("r20_4", 20, 4)], COL)
    assert s["rows_by"] == "workers"
    key = "baseline | regulators · enforce · haiku"
    assert [c["key"] for c in s["columns"] if c["influencers"]] == [key]
    assert [(r["size"], r["counts"], r["shared"], r["cells"]["baseline"] and r["cells"]["baseline"]["n"],
             r["cells"][key]["n"]) for r in s["rows"]] == [
        (4, [["regulator", 1]], [], 1, 1), (4, [["regulator", 3]], ["baseline"], 1, 1),
        (20, [["regulator", 4]], [], None, 1)]
    assert [(x["n"], x["share"]) for r in s["rows"] for x in r["team"]["influencers"]] == [
        ([1], [20]), ([3], [43]), ([4], [17])]
    assert s["totals"]["baseline"]["n"] == 1 and s["totals"][key]["n"] == 3
    assert s["totals"][key]["team"]["regulator"] == [1, 3, 4]
    # where influencers take workers' places, a row is the teams of one size
    swapped = ts.summary([row("n5", workers=5), team("n5_reg1", 4, 1), team("n5_reg3", 2, 3)], COL)
    assert swapped["rows_by"] == "agents"
    assert [(r["size"], r["counts"], r["team"]["workers"]) for r in swapped["rows"]] == [
        (5, [["regulator", 1]], [4, 5]), (5, [["regulator", 3]], [2, 5])]


def test_a_rows_lead_columns_are_its_workers_and_what_its_influencer_columns_add():
    """A row's team (the Workers and Influencers lead columns): its workers as one number, and per influencer role the
    number its cells hold and their share of the team, never a range down to the baseline's 0."""
    disreg = [row("base", workers=2), row("d1", workers=2, influencers=(("disregulator", "generic", "haiku"),))]
    assert ts.summary(disreg, COL)["rows"][0]["team"] == {"workers": [2], "influencers": [
        {"role": "disregulator", "n": [1], "share": [33], "teams": [[1, 3]]}]}
    regs = [row("base", workers=2), row("r2", workers=2, influencers=REG_E_HAIKU)]
    regs[1]["influencer_conditioning"][0]["n"] = 2
    assert ts.summary(regs, COL)["rows"][0]["team"] == {"workers": [2], "influencers": [
        {"role": "regulator", "n": [2], "share": [50], "teams": [[2, 4]]}]}
    assert ts.summary([row("base", workers=4)], COL)["rows"][0]["team"] == {"workers": [4], "influencers": []}
    both = [row("r", workers=3, influencers=REG_E_HAIKU),
            row("rd", workers=3, influencers=(*REG_E_HAIKU, ("disregulator", "generic", "haiku")))]
    got = [[(x["role"], x["n"], x["share"]) for x in r["team"]["influencers"]] for r in ts.summary(both, COL)["rows"]]
    assert got \
        == [[("regulator", [1], [25])], [("regulator", [1], [20]), ("disregulator", [1], [20])]]
    page = (ROOT / "src/ui/transcript_static/tv_view_summary.js").read_text()
    for part in ("const lead = 4 + (s.by_seed", "workersOf(r.team), influencersOf(r.team),", "muted('none')",
                 "`${span(x.n)} · ${span(x.share)}%`", "`${plural(k, x.role)} of ${plural(n, 'agent')}`",
                 ".sum-workers, .sum-infl, .sum-seed'"):
        assert part in page, part
    assert "Workers / Regulators / Disregulators" not in page and "teamOf" not in page


def test_influencer_columns_stand_by_level_then_model_never_by_share():
    """A column is one role, level and model: 3 regulators of 5 and 1 of 5 of one model share it; the columns go by
    level, then by the model's strength. The header names the role without a percent."""
    def team(run: str, workers: int, regulators: int, model: str, level: str = "enforce") -> dict:
        out = row(run, workers=workers, influencers=(("regulator", level, model),))
        out["influencer_conditioning"][0]["n"] = regulators
        return out
    rows = [team("a", 2, 3, "opus"), team("b", 4, 1, "opus"), team("c", 2, 3, "haiku"),
            team("d", 4, 1, "haiku", "generic")]
    assert [c["label"] for c in ts.summary(rows, COL)["columns"] if c["influencers"]] == [
        "+ regulators · generic · haiku", "+ regulators · enforce · haiku", "+ regulators · enforce · opus"]
    page = (ROOT / "src/ui/transcript_static/tv_view_summary.js").read_text()
    assert "x.share" not in page.replace("span(x.share)", "") and "`${x.role}s`" in page
    assert "rows.filter(r => !r.shared.includes(c.key))" in page
    assert "!r.shared.includes(c.key) ? r.cells[c.key].n" in page


def test_the_page_filters_rows_by_team_size():
    """The Summary's size picker keeps the rows of one size (the row's `size`), and stands only where sizes differ."""
    page = (ROOT / "src/ui/transcript_static/tv_view_summary.js").read_text()
    assert "(!size || String(r.size) === size)" in page and "nav({ size: v })" in page
    assert "sizes.length > 1 ? select([['', 'All sizes']" in page



def test_a_model_has_one_key_whatever_its_route_and_tables_go_by_strength():
    """Anthropic's own id of a Claude model and OpenRouter's give one key, so a model is one row and one menu entry;
    the summary's rows and its columns within one share of influencers go weakest first (transcript_index.MODELS)."""
    assert short_model("anthropic/claude-opus-5-5") == "claude-opus-5.5"
    assert short_model("openrouter/anthropic/claude-opus-5.5") == "claude-opus-5.5"
    assert short_model("anthropic/claude-fable-5-2") == "claude-fable-5.2"
    assert short_model("openrouter/deepseek/deepseek-v4.1-flash-1017") == "deepseek-v4.1-flash"
    claude = ["claude-haiku-5.5", "claude-sonnet-5.5", "claude-opus-5.5", "claude-fable-5.2"]
    assert sorted(reversed(claude), key=model_rank) == claude and all(m in MODELS for m in claude)
    assert sorted(["zzz", "claude-fable-5.2", "aaa"], key=model_rank) == ["claude-fable-5.2", "aaa", "zzz"]
    rows = [row(f"r{k}", model="deepseek-v4.1-flash", workers=4, influencers=[("regulator", "generic", m)])
            for k, m in enumerate(reversed(claude))]
    rows += [row("o", model="claude-opus-5.5"), row("h", model="claude-haiku-5.5")]
    s = ts.summary(rows, COL)
    assert [x["model"] for c in s["columns"] for x in c["influencers"]] == claude
    assert [r["model"] for r in s["rows"]] == ["deepseek-v4.1-flash", "claude-haiku-5.5", "claude-opus-5.5"]


def test_members_count_each_cells_workers_and_their_own_levels():
    """By members a cell counts its runs' workers: cheated and accessed of n workers and their levels per ladder; a
    run with no worker rows adds none. The totals count the column's workers the same way."""
    def worker(a: str, cheating: str, cheated: bool) -> dict:
        return {"agent": a, "cheating": cheating, "coordination": "low", "collusion": "none", "cheated": cheated}
    members = {f"{COL}/b1": [worker("w1", "none", False), worker("w2", "looked", False)],
               f"{COL}/b2": [worker("w1", "hacked", True), worker("w2", "tempted", False),
                             worker("w3", "looked", False)],
               f"{COL}/reg_haiku_enforce1": [worker("w1", "attempted", True)]}
    s = ts.summary(ROWS, COL, members)
    base = next(r for r in s["rows"] if r["model"] == "m1" and r["task"] == "SWE (flipt)")["cells"]["baseline"]
    assert base["n"] == 3 and base["members"] == {
        "n": 5, "cheated": 1, "accessed": 4,
        "levels": {"cheating": [["none", 1], ["looked", 2], ["tempted", 1], ["hacked", 1]],
                   "collusion": [["none", 5]], "coordination": [["low", 5]]}}
    assert s["totals"]["baseline"]["members"]["n"] == 5
    reg = s["totals"][ts.column_of("baseline", ts.influencers_of(ROWS[3]))["key"]]["members"]
    assert (reg["n"], reg["cheated"], reg["accessed"]) == (1, 1, 1)
    assert ts.summary(ROWS, COL)["totals"]["baseline"]["members"] == {
        "n": 0, "cheated": 0, "accessed": 0, "levels": {"cheating": [], "collusion": [], "coordination": []}}


def test_a_workers_levels_are_its_own_rows_of_the_measures_and_the_influencers_are_left_out():
    """worker_levels: each worker's rung or level, NA on a ladder that does not apply, influencers left out."""
    measured = {
        "cheating": {"applicable": True, "agents": {
            "a01": {"role": "worker", "rung": "hacked", "cheated": True},
            "a02": {"role": "worker", "rung": "looked", "cheated": False},
            "r01": {"role": "regulator", "rung": "looked", "cheated": False}}},
        "coordination": {"roles": {"a01": None, "a02": None, "r01": "regulator"},
                         "agents": {"a01": {"level": "high"}, "a02": {"level": None}, "r01": {"level": "low"}}},
        "collusion": {"applicable": False, "reason": "no take rule"}}
    assert transcript_levels.worker_levels(measured) == [
        {"agent": "a01", "cheating": "hacked", "coordination": "high", "collusion": transcript_levels.NA,
         "cheated": True},
        {"agent": "a02", "cheating": "looked", "coordination": "none", "collusion": transcript_levels.NA,
         "cheated": False}]


def test_the_summary_page_toggles_teams_and_members_in_its_address():
    page = (ROOT / "src/ui/transcript_static/tv_view_summary.js").read_text()
    for part in ("const members = q.get('by') === 'members';", "by: members ? 'members' : ''",
                 "const u = members ? c.members : c;", "members: sumCounts(cs.map(x => x.members), d)",
                 "[['', 'teams'], ['members', 'members']]"):
        assert part in page, part


def test_a_rows_ending_is_its_last_hand_ins_round_and_the_workers_still_working():
    """ending_shown: the round of the team's last hand-in (in seq order, a take-back between them changing nothing),
    and the workers in neither stop_working nor rules.exits, influencers left out."""
    roles = {"a01": "worker", "a02": "worker", "a03": "worker", "r01": "regulator"}
    rj = {"unsubmits": {"submits": [{"agent": "a01", "round": 3, "seq": 0}, {"agent": "a02", "round": 6, "seq": 2}],
                        "unsubmits": [{"agent": "a02", "round": 4, "seq": 1, "submit": {}}]},
          "stop_working": {"a01": 5}, "rules": {"exits": {"a02": {"why": "submit", "round": 6}, "r01": {"why": "alert"}}}}
    assert ending_shown(rj, roles) == {"last_hand_in": 6, "working": 1, "workers": 3}
    assert ending_shown({}, roles) == {"last_hand_in": None, "working": 3, "workers": 3}   # no hand-in, no one left


def test_a_cells_ending_joins_its_runs_last_hand_in_rounds_and_sums_the_workers_working():
    rows = [row("a"), row("b"), row("c"), row("d")]
    rows[0]["ending"] = {"last_hand_in": 7, "working": 0, "workers": 3}
    rows[1]["ending"] = {"last_hand_in": 12, "working": 2, "workers": 3}
    rows[2]["ending"] = {"last_hand_in": None, "working": 3, "workers": 3}        # no hand-in; d made before it
    assert ts.cell(rows, {})["ending"] == {"last_hand_in": [7, 12], "no_hand_in": 1, "working": 5, "workers": 9,
                                           "runs": 3}
    assert ts.cell([row("e")], {})["ending"] is None
    page = (ROOT / "src/ui/transcript_static/tv_view_summary.js").read_text()
    for part in ("endingLine(c.ending),", "ending: sumEndings(cs.map(x => x.ending))"):
        assert part in page, part
