"""The LaTeX paper in paper/ keeps step with the code: each file under paper/generated/ equals what
scripts/paper/paper_sources.py renders from the code now, every figure the paper includes is named in FIGURES and
exists, the level tables carry every level the instruction ladders define, the rung tables every rung of the Cheating
and Collusion ladders, and no section names a rung or level of the ladders the three replaced."""
from __future__ import annotations

import re

import pytest
import yaml

from scripts.paper import paper_sources
from src.measures import cheating_ladder, collusion

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
    for rung in cheating_ladder.LEVELS:
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
                                    "cheating_table", "coordination_table", "collusion_table", "judge_tables", "tables",
                                    "fig_scripted_teammates", "fig_roles_access", "fig_jigsaw", "fig_multidomain",
                                    "fig_deep_dive"])
def test_figure_scripts_import(module):
    """Every script that draws a paper figure or table imports without its data on disk and exposes main."""
    mod = __import__(f"scripts.paper.{module}", fromlist=["main"])
    assert callable(mod.main)


OWN_LINE = re.compile(r"\bat_least\(|\b(?:LEVELS|CHEATING|RUNGS)\.index\(|code_ladder")


def test_no_paper_script_draws_its_own_cheating_line():
    """A team or an agent cheated by the measures' one definition (cheating_ladder.cheated), never by a threshold a
    paper script compares rungs against."""
    for script in (paper_sources.ROOT / "scripts" / "paper").glob("*.py"):
        found = OWN_LINE.findall(script.read_text())
        assert not found, f"{script.name} compares rungs itself: {found}"


def test_every_figure_has_a_script_in_the_repository():
    for name, fig in paper_sources.FIGURES.items():
        script = fig.note.split("(")[1].rstrip(")").split(" ")[0]
        assert (paper_sources.ROOT / script).is_file(), f"{name}: {script} is missing"
        assert fig.source.startswith("tmp/"), f"{name}: its source must be a script's output under tmp/"
