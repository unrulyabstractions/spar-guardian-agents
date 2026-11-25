"""The LaTeX paper in paper/ keeps step with the code: each file under paper/generated/ equals what
scripts/paper/paper_sources.py renders from the code now, every figure the paper includes is named in FIGURES and
exists, the level tables carry the code's levels and the stored tables the levels the study's runs used, the rung
tables every rung of the Cheating and Collusion ladders, and no section names a rung or level of the ladders the three
replaced."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from scripts.paper import paper_sources
from src.measures import cheating_ladder, collusion, measure_readings
from src.measures.rungs import RUNGS
from tests.support.run_files import write_text

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
def test_level_tables_hold_every_level_with_a_text(ladder):
    """A generated level table prints every level of the ladder that has a text, and only those."""
    spec = yaml.safe_load(paper_sources.LEVELS_YAML.read_text())[ladder]
    tex = paper_sources.levels_tex(ladder)
    for level in spec["order"]:
        assert (rf"\textbf{{{level}}}" in tex) == (spec[level].get("text") is not None), level
        assert (level in paper_sources.DESIGN_LEVELS[ladder]) == (spec[level].get("text") is not None), level


@pytest.mark.parametrize("ladder", ["regulator", "disregulator"])
def test_stored_level_tables_name_the_stored_levels(ladder):
    """The frozen table of the stored studies names exactly STORED_LEVELS, and the paper inputs it."""
    assert paper_sources.stored_levels(ladder) == paper_sources.STORED_LEVELS[ladder]
    text = "".join(p.read_text() for p in SECTIONS.glob("*.tex"))
    assert f"stored_study_prompts/{ladder}_levels" in text


def test_study_levels_are_the_levels_of_the_valid_runs():
    """STORED_LEVELS equals the levels the study's valid runs carry, and a run of any other level stops the build."""
    if not paper_sources.STUDY_INDEX.exists():
        pytest.skip("the run index is not on this machine")
    from scripts.paper.study_ladders import valid_runs
    runs = [r.meta for r in valid_runs()]
    paper_sources.check_study_levels(runs)
    with pytest.raises(SystemExit):
        paper_sources.check_study_levels([*runs, {"regulator_level": "pressure", "disregulator_level": None}])


def test_regulator_box_holds_only_the_common_text():
    """The prompt box prints the text every regulator gets after its level, and no level of the ladder."""
    tex = paper_sources.regulator_suffix_tex()
    assert "Level" not in tex and "[COMMON]" not in tex


def test_rung_tables_hold_every_rung_and_clause():
    cheating, colluding = paper_sources.cheating_rungs_tex(), paper_sources.collusion_rungs_tex()
    for rung in RUNGS:
        assert rf"\textbf{{{rung}}}" in cheating
    for clause in cheating_ladder.CLAUSES:
        assert rf"\textit{{{clause}}}" in cheating
    for rung in collusion.RUNGS:
        assert rf"\textbf{{{rung}}} &" in colluding
    assert "scheming" not in colluding


OLD_LADDERS = re.compile(r"`(?:exposed|tried|opened|used|tampered|checked|shipped)'|\b(?:isolated|discussed|solicited|"
                         r"crossed|assembled)\b|collaboration rate|code ladder|judged ladder|table_rungs\}|"
                         r"table_collaboration_by_domain", re.IGNORECASE)


def test_no_section_names_a_retired_ladder():
    """The paper has three ladders, Cheating, Coordination and Collusion. A quoted rung of the eight-rung code ladder,
    a collaboration level, or a table of either is a sentence the change left behind."""
    for tex in SECTIONS.glob("*.tex"):
        found = OLD_LADDERS.findall(tex.read_text())
        assert not found, f"{tex.name} names a retired ladder: {found}"


RETIRED_LABELS = re.compile(r"\bscheming\b|awareness label|\\AwarenessMeans|StudySchemingJudge|"
                            r"StudyLabelsJudge\b|table_scheming_by_task|table_awareness_by_task|reliability_retired",
                            re.IGNORECASE)


def test_no_section_names_scheming_or_the_awareness_label():
    """Scheming and the yes or no awareness label are retired (the user, 2026-11-19), and the paper neither names them
    nor inputs a table of their readings."""
    for tex in SECTIONS.glob("*.tex"):
        found = RETIRED_LABELS.findall(tex.read_text())
        assert not found, f"{tex.name} names a retired label: {found}"


def test_escape_covers_latex_specials():
    assert paper_sources.escape("50% of a_b & {c} #1 $2") == r"50\% of a\_b \& \{c\} \#1 \$2"
    assert paper_sources.with_chips("use {prior_work} now") == r"use \placeholder{[planted work]} now"
    with pytest.raises(SystemExit):
        paper_sources.with_chips("use {no_such_slot} now")


@pytest.mark.parametrize("module", ["control_tables", "protein_regulator_table", "swe_regulator_table",
                                    "creative_regulator_table",
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
        write_text(tmp_path / name / "processed/run.json", "{}")
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


def test_reliability_table_holds_every_current_row_of_the_readings():
    """The paper's table of independent checks renders the rows of measure_readings.yaml whose pass is current: a
    block per measure headed by its name alone, and one table row per reading. No row of a pass that read an older
    state of the code (the user, 2026-11-19, of the code rows: "drop them") or a retired label, no pass label and no
    word of the readings' history reaches the paper (the user: "LEAK NO SHIT LIKE THIS")."""
    r = measure_readings.readings()
    tex = paper_sources.reliability_tex()
    current = {name for name, p in r["passes"].items() if p.get("current") and not p.get("retired")}
    rows = [row for row in r["rows"] if row["pass"] in current]
    measures = {row["measure"] for row in rows}
    for measure in measures:
        assert rf"\multicolumn{{7}}{{@{{}}l}}{{\textbf{{{r['measures'][measure]}}}}} \\" in tex, measure
    body = tex.split("\\midrule")[1].split("\\bottomrule")[0]
    assert body.count("\\\\") == len(rows) + len(measures)
    assert body.count("\\multicolumn") == len(measures)
    for row in r["rows"]:
        if row["pass"] not in current and row["measure"] not in measures:
            assert r["measures"][row["measure"]] not in body, row
    for p in r["passes"].values():
        assert paper_sources.escape(p["label"]) not in body, p["label"]
    assert not re.search(r"retired|current|before its fixes|first pass|second pass", body, re.IGNORECASE)
    assert not (paper_sources.GENERATED / "reliability_retired.tex").exists()
    assert "reliability_retired" not in Path(paper_sources.__file__).read_text()


def test_score_anchors_table_holds_every_anchor():
    from src.measures import judge_labels
    tex = paper_sources.score_anchors_tex()
    for score, meaning in judge_labels.SCORE_ANCHORS.items():
        assert rf"{score} & {paper_sources.escape(meaning)} \\" in tex


def test_labels_sentence_names_the_one_judge_or_says_none(monkeypatch):
    """With no labels file in the study the paper says that no judge has scored the runs; with one judge it names it;
    two judges stop the build."""
    from pathlib import Path

    from src.measures import judge_labels
    judges = {"a": None, "b": None}
    monkeypatch.setattr(judge_labels, "labels", lambda run: {"judge": judges[Path(run).name], "agents": {}})
    assert paper_sources.labels_judged([Path("a"), Path("b")]) == "No judge has scored these runs yet."
    judges["a"] = "gpt-6-luna"
    assert paper_sources.labels_judged([Path("a"), Path("b")]) == \
        "One judge, OpenAI GPT-6 Luna, scored every agent of these runs, and no human checked its scores."
    judges["b"] = "gpt-6-sol"
    with pytest.raises(SystemExit):
        paper_sources.labels_judged([Path("a"), Path("b")])


def test_the_score_table_bins_each_agent_by_label_and_role(monkeypatch):
    """label_tables.score_counts puts every agent of a run in one column per label: 0, 1-49, 50-74, 75-100, or not
    judged when the run has no score for it (no labels file); workers and influencers apart."""
    from pathlib import Path

    from scripts.paper import label_tables
    from src.measures.judge_labels import LABELS

    def scored(score):
        return {label: {"score": score, "excerpt": "", "source": "none", "reason": "", "located": None}
                for label in LABELS}
    agents = {"a": {"worker": True}, "b": {"worker": True}, "c": {"worker": True}, "r": {"worker": False}}
    found = {"judged": {"judge": "gpt-6-luna", "agents": {"a": scored(0), "b": scored(49), "c": None, "r": scored(75)}},
             "unjudged": {"judge": None, "agents": {}}}
    monkeypatch.setattr(label_tables, "record", lambda path: {"agents": agents})
    monkeypatch.setattr(label_tables, "labels", lambda path: found[Path(path).name])
    counts = label_tables.score_counts([("swe", Path("judged")), ("swe", Path("unjudged"))])
    for label in LABELS:
        assert counts[(f"{label}/workers", "swe")] == {"0": 1, "1-49": 1, label_tables.NOT_JUDGED: 4}
        assert counts[(f"{label}/influencers", "swe")] == {"75-100": 1, label_tables.NOT_JUDGED: 1}
    assert [label_tables.column(s) for s in (0, 1, 49, 50, 74, 75, 100)] == \
        ["0", "1-49", "1-49", "50-74", "50-74", "75-100", "75-100"]
    with pytest.raises(SystemExit):
        label_tables.column(101)
