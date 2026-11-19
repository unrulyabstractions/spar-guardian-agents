"""The LaTeX paper in paper/ keeps step with the code: each file under paper/generated/ equals what
scripts/paper/paper_sources.py renders from the code now, every figure the paper includes is named in FIGURES and
exists, the level tables carry every level the instruction ladders define, the rung tables every rung of the Cheating
and Collusion ladders, and no section names a rung or level of the ladders the three replaced."""
from __future__ import annotations

import json
import re

import pytest
import yaml

from scripts.paper import paper_sources
from src.measures import cheating_ladder, collusion, measure_readings
from src.measures.rungs import RUNGS

SECTIONS = paper_sources.PAPER / "sections"


@pytest.mark.parametrize("path", sorted(paper_sources.generated_files()), ids=lambda p: p.name)
def test_generated_tex_matches_the_code(path):
    expected = paper_sources.generated_files()[path]
    assert path.exists(), f"{path} is missing: run make paper-sources"
    assert path.read_text() == expected, f"{path} is stale against the code: run make paper-sources"


def test_every_included_figure_is_managed_and_present():
    included = set()
    for tex in SECTIONS.glob("*.tex"):
        included |= set(re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", tex.read_text()))
    names = {f"{name}.png" if not name.endswith(".png") else name for name in included}
    assert names <= set(paper_sources.FIGURES), f"figures without a FIGURES entry: {names - set(paper_sources.FIGURES)}"
    for name in paper_sources.FIGURES:
        assert (paper_sources.FIGURE_DIR / name).exists(), f"paper/figures/{name} is missing"


def test_every_generated_file_is_input_by_the_paper():
    text = "".join(p.read_text() for p in [paper_sources.PAPER / "main.tex", *SECTIONS.glob("*.tex")])
    for path in paper_sources.generated_files():
        assert f"generated/{path.stem}" in text, f"{path.name} is generated but the paper never inputs it"


@pytest.mark.parametrize("ladder", ["regulator", "disregulator"])
def test_level_tables_hold_every_level(ladder):
    spec = yaml.safe_load(paper_sources.LEVELS_YAML.read_text())[ladder]
    tex = paper_sources.levels_tex(ladder)
    for level in spec["order"]:
        if spec[level].get("text") is not None:
            assert rf"\textbf{{{level}}}" in tex


def test_rung_tables_hold_every_rung_and_clause():
    cheating, colluding = paper_sources.cheating_rungs_tex(), paper_sources.collusion_rungs_tex()
    for rung in RUNGS:
        assert rf"\textbf{{{rung}}}" in cheating
    for clause in cheating_ladder.CLAUSES:
        assert rf"\textit{{{clause}}}" in cheating
    for rung in collusion.RUNGS:
        reader = "code" if rung in collusion.CODE_RUNGS else "judge"
        assert rf"\textbf{{{rung}}} & {reader} &" in colluding


OLD_LADDERS = re.compile(r"`(?:exposed|tried|opened|used|tampered|checked|shipped)'|\b(?:isolated|discussed|solicited|"
                         r"crossed|assembled)\b|collaboration rate|code ladder|judged ladder|table_rungs\}|"
                         r"table_collaboration_by_domain", re.IGNORECASE)


def test_no_section_names_a_retired_ladder():
    """The paper has three ladders, Cheating, Coordination and Collusion. A quoted rung of the eight-rung code ladder,
    a collaboration level, or a table of either is a sentence the change left behind."""
    for tex in SECTIONS.glob("*.tex"):
        found = OLD_LADDERS.findall(tex.read_text())
        assert not found, f"{tex.name} names a retired ladder: {found}"


def test_escape_covers_latex_specials():
    assert paper_sources.escape("50% of a_b & {c} #1 $2") == r"50\% of a\_b \& \{c\} \#1 \$2"
    assert paper_sources.with_chips("use {prior_work} now") == r"use \placeholder{\{prior\_work\}} now"


@pytest.mark.parametrize("module", ["control_tables", "protein_regulator_table", "swe_regulator_table",
                                    "disregulator_tables", "baseline_tables", "solo_controls_table",
                                    "cheating_table", "coordination_table", "collusion_table", "judge_tables",
                                    "label_tables", "tables",
                                    "fig_scripted_teammates", "fig_roles_access", "fig_jigsaw", "fig_multidomain",
                                    "fig_deep_dive"])
def test_figure_scripts_import(module):
    """Every script that draws a paper figure or table imports without its data on disk and exposes main."""
    mod = __import__(f"scripts.paper.{module}", fromlist=["main"])
    assert callable(mod.main)


OWN_LINE = re.compile(r"\bat_least\(|\b(?:LEVELS|CHEATING|RUNGS)\.index\(|code_ladder")


def test_no_paper_script_draws_its_own_cheating_line():
    """A team or an agent cheated by the measures' one definition (cheating_ladder.cheated), never by a threshold a
    paper script compares rungs against. The paper's second measure, accessed the honeypot, is drawn once, in
    study_ladders.accessed."""
    for script in (paper_sources.ROOT / "scripts" / "paper").glob("*.py"):
        found = OWN_LINE.findall(script.read_text())
        if script.name == "study_ladders.py":
            assert found == ["at_least("], f"study_ladders draws a line beside accessed: {found}"
            continue
        assert not found, f"{script.name} compares rungs itself: {found}"


def test_accessed_is_looked_or_above_and_cheated_is_attempted_or_above():
    from scripts.paper.study_ladders import accessed

    assert [accessed(r) for r in RUNGS] == [False, True, True, True, True]
    assert [cheating_ladder.cheated(r) for r in RUNGS] == [False, False, False, True, True]
    assert accessed(None) is None


def test_cell_runs_takes_valid_runs_of_the_cell_seeds_then_pilots(tmp_path):
    from scripts.paper.study_ladders import cell_runs

    names = {"cell1": True, "cell2": False, "cell10": True, "cell_pilot1": True, "cellx1": True, "cell_mock1": True}
    rows = []
    for name, valid in names.items():
        (tmp_path / name).mkdir()
        (tmp_path / name / "run.json").write_text("{}")
        rows.append(json.dumps({"path": name, "valid": valid, "mock": name == "cell_mock1"}))
    (tmp_path / "INDEX.jsonl").write_text("\n".join(rows) + "\n")
    assert [p.name for p in cell_runs("cell", tmp_path)] == ["cell1", "cell10", "cell_pilot1"]


def test_save_refuses_text_below_the_print_floor(tmp_path):
    from scripts.paper.study_ladders import MIN_PRINT_PT, TEXT_WIDTH_IN, canvas, save

    fig, ax = canvas(TEXT_WIDTH_IN * 2, 1)
    ax.text(0.1, 0.5, "too small", fontsize=MIN_PRINT_PT * 1.5)
    with pytest.raises(SystemExit, match="too small"):
        save(fig, tmp_path / "small.png")
    fig, ax = canvas(TEXT_WIDTH_IN, 1)
    ax.text(0.1, 0.5, "readable", fontsize=MIN_PRINT_PT)
    assert save(fig, tmp_path / "ok.png").is_file()


def test_every_figure_has_a_script_in_the_repository():
    for name, fig in paper_sources.FIGURES.items():
        script = fig.note.split("(")[1].rstrip(")").split(" ")[0]
        assert (paper_sources.ROOT / script).is_file(), f"{name}: {script} is missing"
        assert fig.source.startswith("tmp/"), f"{name}: its source must be a script's output under tmp/"


def test_reliability_table_holds_every_row_of_the_readings():
    """The paper's table of independent checks renders measure_readings.yaml: every measure by its name, every pass by
    its label, and one table row per reading besides one heading row per measure and pass."""
    r = measure_readings.readings()
    tex = paper_sources.reliability_tex()
    assert "src/measures/measure_readings.yaml" in tex
    for key, name in r["measures"].items():
        assert name in tex, key
    for about in r["passes"].values():
        assert paper_sources.escape(about["label"]) in tex
    body = tex.split("\\midrule")[1].split("\\bottomrule")[0]
    blocks = {(row["measure"], row["pass"]) for row in r["rows"]}
    assert body.count("\\\\") == len(r["rows"]) + len(blocks)
    assert body.count("\\multicolumn") == len(blocks)


def test_awareness_means_one_thing_in_the_measure_the_viewer_and_the_paper():
    """The label awareness as the judge reads it is knowing use of reference material: one definition
    (judge_labels.AWARENESS_MEANS) in the measure's docstring, on the viewer's Ladders page and in the paper."""
    from src.measures import judge_labels
    from src.ui import transcript_levels
    means = judge_labels.AWARENESS_MEANS
    assert means.startswith("knowing use of reference material")
    assert means in " ".join(judge_labels.__doc__.split())
    assert transcript_levels.JUDGE_LABELS["awareness"]["question"] == f"Awareness means {means}."
    assert rf"\newcommand{{\AwarenessMeans}}{{{paper_sources.escape(means)}}}" in paper_sources.params_tex()
    assert r"\textbf{Awareness} means \AwarenessMeans{}." in (SECTIONS / "appendix_a_methodology.tex").read_text()


def test_the_scheming_table_counts_the_measures_verdicts(monkeypatch):
    """label_tables.scheming_counts counts each colluding pair by the Collusion measure's verdict, as study_ladders
    record carries it from collusion_of: a flag between the pair is yes, a flag elsewhere leaves it no, a board row
    that did not parse leaves it not judged, and a pair with a regulator counts apart."""
    from pathlib import Path
    from types import SimpleNamespace

    from scripts.paper import label_tables, study_ladders
    roles = {"a": "worker", "b": "worker", "r": "regulator"}
    pieces = {"a": {"held": ["A"], "work": {"B": "x"}, "own": {}, "supplied": []},
              "b": {"held": ["B"], "work": {"A": "x"}, "own": {}, "supplied": []},
              "r": {"held": ["R"], "work": {"A": "x"}, "own": {}, "supplied": []}}
    pieces["a"]["work"]["R"] = "x"
    taken = collusion.pieces_taken(pieces)
    flag = {"post": "P1", "which": "awareness", "quote": "the gold fixtures", "reason": "r",
            "check": {"ok": True, "why": "in P1"}, "posts": [{"label": "P1", "id": "m1", "writer": "a",
                                                               "readers": ["b"]}]}
    rows = {"yes": {"board": {"unit": "board", "judge": "j", "parse_ok": True, "flagged": [flag]}},
            "none": {"board": {"unit": "board", "judge": "j", "parse_ok": False, "flagged": None}}}

    def collusion_of(path, result):
        found = collusion.ladder(list(roles), roles, collusion.piece_takes(taken), {}, dict.fromkeys(roles, "none"),
                                 taken, {}, rows[Path(path).name])
        return {"applicable": True, **found}
    run = SimpleNamespace(roles=lambda: {})                          # no influencer's posts to count here
    monkeypatch.setattr(study_ladders, "scanned_run", lambda path: (run, None, None, [], None, None))
    monkeypatch.setattr(study_ladders, "cheating", lambda *a: {"team": "none", "passed": None, "alarm": False,
                                                               "hand_in_pieces": [], "agents": {}})
    monkeypatch.setattr(study_ladders, "collusion_of", collusion_of)
    monkeypatch.setattr(study_ladders, "labels", lambda path: {"agents": {a: {"awareness": "yes"} for a in roles}})
    study_ladders.record.cache_clear()
    try:
        counts = label_tables.scheming_counts([("swe", Path("yes")), ("swe", Path("none"))])
    finally:
        study_ladders.record.cache_clear()
    assert counts[("workers", "swe")] == {"yes": 1, collusion.NOT_JUDGED: 1}
    assert counts[("influencers", "swe")] == {"no": 1, collusion.NOT_JUDGED: 1}
