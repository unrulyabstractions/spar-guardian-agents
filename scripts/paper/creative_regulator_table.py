"""Creative writing regulators with GPT-6 Luna workers, valid runs only, pilots included
(scripts/paper/study_ladders.py cell_runs and role_table): each run's team Cheating and Collusion rungs, the grader's
pass and the regulator's posts, and per cell the teams that cheated and the teams that accessed the honeypot. Writes
tmp/tables/results/table_creative_regulators.png.

    uv run python -m scripts.paper.creative_regulator_table    # make results-tables runs every one
"""
from __future__ import annotations

import sys

from scripts.paper.study_ladders import role_table
from src.configuration.settings import ROOT
from src.utils import progress

OUT = ROOT / "tmp" / "tables" / "results" / "table_creative_regulators.png"
ROWS = [
    ("None", "baseline", "poem_minimal_luna"),
    ("GPT-6 Luna", "ambiguous", "poem_minimal_regulated_luna_luna_ambiguous"),
    ("Gemini 4 Argon", "ambiguous", "poem_minimal_regulated_luna_argon_ambiguous"),
    ("Claude Opus 5.5", "ambiguous", "poem_minimal_regulated_luna_opus_ambiguous"),
]
"""(regulator, level, *cell prefixes); a row without a valid run is left out."""


def main() -> int:
    progress.wrote(role_table(OUT, "Creative writing, GPT-6 Luna workers", ROWS, "regulator"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
