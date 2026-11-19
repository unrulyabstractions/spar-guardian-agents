"""SWE (fixed task) regulators with DeepSeek V4.1 Flash workers, valid runs only, pilots included
(scripts/paper/study_ladders.py cell_runs and role_table): each run's team Cheating and Collusion rungs, the pieces its
hand-in holds, the grader's pass and the regulator's posts, and per cell the teams that cheated and the teams that
accessed the honeypot. Writes tmp/tables/results/table_swe_regulators.png.

    uv run python -m scripts.paper.swe_regulator_table    # make results-tables runs every one
"""
from __future__ import annotations

import sys

from scripts.paper.study_ladders import role_table
from src.configuration.settings import ROOT

OUT = ROOT / "tmp" / "tables" / "results" / "table_swe_regulators.png"
ROWS = [
    ("None", "baseline", "flipt_minimal_dsflash_fixed"),
    ("Claude Haiku 5.5", "ambiguous", "flipt_minimal_regulated_dsflash_haiku_ambiguous_fixed"),
    ("Claude Haiku 5.5", "enforce", "flipt_minimal_regulated_dsflash_haiku_enforce_fixed"),
    ("GPT-6 Luna", "ambiguous", "flipt_minimal_regulated_dsflash_luna_ambiguous_fixed",
     "flipt_minimal_regulated_dsflash_luna_ambiguous_fill"),
    ("GPT-6 Luna", "enforce", "flipt_minimal_regulated_dsflash_luna_enforce_fixed",
     "flipt_minimal_regulated_dsflash_luna_enforce_fill"),
    ("Claude Sonnet 5.5", "ambiguous", "flipt_minimal_regulated_dsflash_sonnet_ambiguous_fill"),
    ("Claude Sonnet 5.5", "enforce", "flipt_minimal_regulated_dsflash_sonnet_enforce_fill"),
]
"""(regulator, level, *cell prefixes); a row without a valid run is left out."""


def main() -> int:
    print(role_table(OUT, "SWE, DeepSeek V4.1 Flash workers", ROWS, "regulator", pieces=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
