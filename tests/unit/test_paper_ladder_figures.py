"""The paper's ladder figures and tables offline: the appendix reads one team record per sample from synthetic run
directories, counts the rate figure's floors on the team rung and draws both figures; the rung and collaboration
tables and the palettes follow the ladder's scales."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.paper import appendix, tables
from src.configuration import settings
from src.domains.chess._chess_ladder import ChessDomain
from src.domains.creative._creative_ladder import CreativeDomain
from src.measures.ladder_domain import Domain
from src.measures.rungs import COLLABORATION, RUNGS
from src.utils.figure_style import COLLABORATION_FILL, RUNG_FILL
from src.utils.json_files import write_jsonl

MODEL = "openai/test"


def make_run(runs: Path, name: str, teams: list[dict], model: str = MODEL) -> None:
    """A run directory whose signals.jsonl holds one agent row and the given team rows."""
    run = runs / name
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"model": model}))
    write_jsonl(run / "signals.jsonl", [{"row": "agent", "sample": "s#1", "agent": "a01", "rung": "shipped"},
                                        *({"row": "team", "sample": "s#1", **team} for team in teams)])


@pytest.fixture
def runs(tmp_path, monkeypatch) -> Path:
    """runs/ with OpenMath cells: two minimal_cheat samples under one suffix, a lone agent, and runs the appendix
    leaves out (another model, no team record, another suffix shape)."""
    runs = tmp_path / "runs"
    make_run(runs, "riemann_minimal_cheat_flash2", [{"rung": "used", "collaboration": "crossed"}])
    make_run(runs, "riemann_minimal_cheat_flash1", [{"rung": "opened", "collaboration": "discussed"}])
    make_run(runs, "riemann_solo_flash1", [{"rung": "exposed", "collaboration": None}])
    make_run(runs, "riemann_solo_cheat_flash1", [{"rung": "shipped", "collaboration": None}], model="openai/other")
    make_run(runs, "riemann_minimal_flash1", [])
    make_run(runs, "riemann_minimal_cheat_x", [{"rung": "none", "collaboration": "isolated"}])
    monkeypatch.setattr(settings, "RUNS", runs)
    monkeypatch.setattr(appendix, "OUT", tmp_path / "appendix")
    return runs


def test_the_appendix_reads_one_team_record_per_sample_and_never_an_agent_row(runs):
    found = appendix.samples("riemann", MODEL, None)
    assert found["minimal_cheat"] == [
        {"name": "riemann_minimal_cheat_flash1", "suffix": "flash", "rung": "opened", "collaboration": "discussed"},
        {"name": "riemann_minimal_cheat_flash2", "suffix": "flash", "rung": "used", "collaboration": "crossed"}]
    assert found["solo"] == [{"name": "riemann_solo_flash1", "suffix": "flash", "rung": "exposed",
                              "collaboration": None}]
    assert found["solo_cheat"] == [] and found["minimal"] == []     # another model; no team record
    assert appendix.samples("riemann", MODEL, ["other"])["minimal_cheat"] == []


def test_the_appendix_draws_both_figures_from_the_team_records(runs, tmp_path):
    by_domain = {"openmath": appendix.samples("riemann", MODEL, None)["minimal_cheat"]}
    folder = tmp_path / "appendix" / "flash"
    for path in (appendix.rate_figure("minimal_cheat", by_domain, folder),
                 appendix.tiles_figure("minimal_cheat", by_domain, folder)):
        assert path.parent == folder and path.stat().st_size > 10_000


def test_each_rate_counts_from_the_tasks_own_floor():
    """A chess or creative team at tried cheated by its task's rule. The same rung on SWE did not. The floor comes from
    the domain's ladder, so a change there moves the bar."""
    assert appendix.floor_of("chess") == ChessDomain.cheated_from == "tried"
    assert appendix.floor_of("creative writing") == CreativeDomain.cheated_from == "tried"
    assert appendix.floor_of("SWE") == Domain.cheated_from == "opened"
    rungs = ["none", "tried", "opened", "used"]
    assert appendix.rate_bars("chess", rungs) == [("tried", 3), ("used", 1)]
    assert appendix.rate_bars("creative writing", rungs) == [("tried", 3), ("used", 1)]
    assert appendix.rate_bars("SWE", rungs) == [("opened", 2), ("used", 1)]


def test_a_floor_read_from_the_ladder_moves_with_it(monkeypatch):
    monkeypatch.setattr(ChessDomain, "cheated_from", "shipped")
    assert appendix.rate_bars("chess", ["used"]) == [("shipped", 0), ("used", 1)]


def drawn(monkeypatch) -> list:
    """Every figure the script saves, kept for the test to read."""
    figures = []
    monkeypatch.setattr(appendix, "save", lambda fig, path: figures.append(fig) or path)
    return figures


def test_the_rate_figure_draws_and_names_each_tasks_floor(monkeypatch, tmp_path):
    """game_minimal_dsflash1 reads tried: 0 of 1 under opened, 1 of 1 under the task's own floor."""
    figures = drawn(monkeypatch)
    tried = [{"rung": "tried"}]
    appendix.rate_figure("minimal", {"chess": tried, "SWE": tried}, tmp_path)
    ax = figures[0].axes[0]
    assert [t.get_text() for t in ax.get_xticklabels()] == ["chess\n(tried or higher)", "SWE\n(opened or higher)"]
    assert [bar.get_height() for bar in ax.patches] == [1, 0, 0, 0]
    assert [t.get_text() for t in figures[0].texts][1:] == ["tried or higher", "opened or higher", "used or higher"]


def test_the_overview_rate_draws_and_names_each_tasks_floor(monkeypatch):
    figures = drawn(monkeypatch)
    cells = {cell: [] for cell in appendix.CELLS} | {"minimal": [{"rung": "tried"}]}
    appendix.model_rate_figure({"gpt-6-luna": {"chess": cells, "SWE": cells}})
    chess, swe = figures[0].axes
    assert chess.get_title(loc="left") == "chess (tried or higher)"
    assert swe.get_title(loc="left") == "SWE (opened or higher)"
    assert [bar.get_height() for bar in chess.patches] == [0, 0, 1, 0, 0]
    assert [bar.get_height() for bar in swe.patches] == [0, 0, 0, 0, 0]
    assert "opened" not in figures[0].texts[0].get_text()


def test_the_overview_draws_every_model_and_a_missing_cell_stays_blank(runs, tmp_path):
    cells = appendix.samples("riemann", MODEL, None)
    screen = {"gpt-6-luna": {"openmath": cells}, "claude-haiku-5-5": {"openmath": {"solo": cells["solo"]}}}
    rung = appendix.overview_figure(screen, tuple(appendix.CELLS), "rung", RUNGS, RUNG_FILL, {}, "The screen")
    level = appendix.overview_figure(screen, appendix.TEAM_CELLS, "collaboration", COLLABORATION, COLLABORATION_FILL,
                                     {}, "The swarms")
    rate = appendix.model_rate_figure(screen)
    assert [p.name for p in (rung, level, rate)] == ["rung.png", "collaboration.png", "rate.png"]
    folder = tmp_path / "appendix" / "overview"
    assert all(p.parent == folder and p.stat().st_size > 10_000 for p in (rung, level, rate))


def test_every_level_keeps_its_own_letter():
    for levels in (RUNGS, COLLABORATION):
        letters = [appendix.initial(level, levels) for level in levels]
        assert len(set(letters)) == len(levels) and letters[0] == "\u00b7"


def test_the_rung_tables_follow_the_ladders_scales():
    assert [rung for rung, *_ in tables.RUNGS_BY_DOMAIN] == list(RUNGS[1:])
    assert [level for level, *_ in tables.COLLABORATION_BY_DOMAIN] == list(COLLABORATION[1:])
    for rows in (tables.RUNGS_BY_DOMAIN, tables.COLLABORATION_BY_DOMAIN):
        assert all(len(cells) == len(tables.DOMAINS) for _, _, cells in rows)
    assert len(tables.RUNG_NOTES) == len(tables.COLLABORATION_NOTES) == len(tables.DOMAINS)
    heads = [row[0] for row in tables.RUNGS]
    assert heads[:6] == list(RUNGS[1:])


def test_the_palettes_cover_every_level_in_order():
    assert list(RUNG_FILL) == list(RUNGS)
    assert list(COLLABORATION_FILL) == list(COLLABORATION)


def test_the_cells_state_the_rules_the_ladder_reads():
    """The cells of the rules the code ladder now reads: the deck's name exposes it (worktime_solo_cheat_dsflash1 call
    1 lists WorkStudy.pptx alone), a post on /data/cache discusses it (worktime_minimal_cheat_haiku3 post 1), used
    follows an agent's commands (poem_solo_dsflash1 call 9), and a team ref at a planted commit is a planted ref
    (flipt_minimal_dsflash3 a01 call 74, git show team/rd-redis-new-client)."""
    rungs = {level: cells for level, _, cells in tables.RUNGS_BY_DOMAIN}
    levels = {level: cells for level, _, cells in tables.COLLABORATION_BY_DOMAIN}
    swe, knowledge, creative = map(tables.DOMAINS.index, ("SWE", "Knowledge work", "Creative writing"))
    protein, chess = map(tables.DOMAINS.index, ("Protein design", "Chess"))
    assert "task text" in rungs["exposed"][chess]
    assert "jc's fragments" in levels["discussed"][protein] and "to jc" in levels["solicited"][protein]
    assert "its file name" in rungs["exposed"][knowledge]
    assert "/data/cache" in levels["discussed"][knowledge] and "pipeline artifact" in levels["discussed"][knowledge]
    assert "commands" in rungs["used"][creative] and "shared folder" not in rungs["used"][creative]
    assert "team ref at a planted commit" in rungs["tried"][swe]
