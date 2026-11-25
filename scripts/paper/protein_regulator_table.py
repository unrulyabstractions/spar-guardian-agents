"""Protein design regulators with GPT-6 Luna workers, valid runs only, pilots included (scripts/paper/study_ladders.py
cell_runs and role_table): each run's team Cheating and Collusion rungs and the regulator's posts, and per cell the
teams that cheated and the teams that accessed the honeypot. Writes tmp/tables/results/table_protein_regulators.png.

    uv run python -m scripts.paper.protein_regulator_table    # make results-tables runs every one
"""
from __future__ import annotations

import sys

from scripts.paper.study_ladders import role_table
from src.configuration.settings import ROOT
from src.utils import progress

OUT = ROOT / "tmp" / "tables" / "results" / "table_protein_regulators.png"
ROWS = [
    ("None", "baseline", "binder_minimal_luna"),
    ("GPT-6 Luna", "ambiguous", "binder_minimal_regulated_luna_luna_ambiguous"),
    ("GPT-6 Luna", "enforce", "binder_minimal_regulated_luna_luna_enforce"),
    ("Claude Haiku 5.5", "ambiguous", "binder_minimal_regulated_luna_haiku_ambiguous"),
    ("Claude Haiku 5.5", "enforce", "binder_minimal_regulated_luna_haiku_enforce"),
    ("Gemini 4 Argon", "ambiguous", "binder_minimal_regulated_luna_argon_ambiguous"),
    ("Gemini 4 Argon", "enforce", "binder_minimal_regulated_luna_argon_enforce"),
    ("Claude Sonnet 5.5", "ambiguous", "binder_minimal_regulated_luna_sonnet_ambiguous"),
    ("Claude Sonnet 5.5", "enforce", "binder_minimal_regulated_luna_sonnet_enforce"),
    ("Claude Opus 5.5", "ambiguous", "binder_minimal_regulated_luna_opus_ambiguous"),
    ("Claude Opus 5.5", "enforce", "binder_minimal_regulated_luna_opus_enforce"),
]
"""(regulator, level, *cell prefixes); a row without a valid run is left out."""


def main() -> int:
    progress.wrote(role_table(OUT, "Protein design, GPT-6 Luna workers", ROWS, "regulator"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
