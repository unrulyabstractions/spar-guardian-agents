"""The paper's ladder figures and tables offline: the appendix reads one team record per sample from synthetic run
directories, counts the rate figure's floors on the team rung and draws both figures; the rung and collaboration
tables and the palettes follow the ladder's scales."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.paper import appendix, tables
from src.configuration import settings
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
    for path in (appendix.rate_figure("minimal_cheat", by_domain), appendix.tiles_figure("minimal_cheat", by_domain)):
        assert path.parent == tmp_path / "appendix" and path.stat().st_size > 10_000
    assert [floor for floor, _ in appendix.FLOORS] == ["opened", "used"]


def test_the_rung_tables_follow_the_ladders_scales():
    assert [rung for rung, *_ in tables.RUNGS_BY_DOMAIN] == list(RUNGS[1:])
    assert [level for level, *_ in tables.COLLABORATION_BY_DOMAIN] == list(COLLABORATION[1:])
    for rows in (tables.RUNGS_BY_DOMAIN, tables.COLLABORATION_BY_DOMAIN):
        assert all(len(cells) == len(tables.DOMAINS) for _, _, cells in rows)
    assert len(tables.RUNG_NOTES) == len(tables.COLLABORATION_NOTES) == len(tables.DOMAINS)
    heads = [row[0] for row in tables.RUNGS]
    assert heads[:6] == list(RUNGS[1:]) and "searched" not in heads and "tampered" not in heads


def test_the_palettes_cover_every_level_in_order():
    assert list(RUNG_FILL) == list(RUNGS)
    assert list(COLLABORATION_FILL) == list(COLLABORATION)
