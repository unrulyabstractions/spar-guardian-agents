"""The LaTeX paper in paper/ keeps step with the code: each file under paper/generated/ equals what
scripts/paper/paper_sources.py renders from the code now, every figure the paper includes is named in FIGURES and
exists, and the level tables carry every level the instruction ladders define."""
from __future__ import annotations

import re

import pytest
import yaml

from scripts.paper import paper_sources

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


def test_escape_covers_latex_specials():
    assert paper_sources.escape("50% of a_b & {c} #1 $2") == r"50\% of a\_b \& \{c\} \#1 \$2"
    assert paper_sources.with_chips("use {prior_work} now") == r"use \placeholder{\{prior\_work\}} now"


@pytest.mark.parametrize("module", ["control_tables", "protein_regulator_table", "swe_regulator_table",
                                    "disregulator_tables", "baseline_tables"])
def test_results_table_scripts_import(module):
    """The results tables' scripts (make results-tables) import without the runs on disk and expose main."""
    mod = __import__(f"scripts.paper.{module}", fromlist=["main"])
    assert callable(mod.main)


def test_every_results_figure_has_a_script_in_the_repository():
    for name, fig in paper_sources.FIGURES.items():
        if fig.kind == "generated" and "results-tables" in fig.note:
            script = fig.note.split("(")[1].rstrip(")")
            assert (paper_sources.ROOT / script).is_file(), f"{name}: {script} is missing"
