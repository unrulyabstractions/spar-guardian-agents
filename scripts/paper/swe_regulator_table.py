"""SWE (fixed task) regulators, valid runs only, pilots included (scripts/paper/study_ladders.py cell_runs and
role_table): each run's team Cheating and Collusion rungs, the pieces its hand-in holds, the grader's pass and the
regulator's posts, and per cell the teams that cheated and the teams that accessed the honeypot. Two tables: DeepSeek
V4.1 Flash workers (ROWS, tmp/tables/results/table_swe_regulators.png) and GPT-6 Luna workers (ROWS_LUNA,
tmp/tables/results/table_swe_luna_regulators.png).

    uv run python -m scripts.paper.swe_regulator_table    # make results-tables runs every one
"""
from __future__ import annotations

import sys

from scripts.paper.study_ladders import role_table
from src.configuration.settings import ROOT
from src.utils import progress

OUT = ROOT / "tmp" / "tables" / "results" / "table_swe_regulators.png"
OUT_LUNA = ROOT / "tmp" / "tables" / "results" / "table_swe_luna_regulators.png"
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
ROWS_LUNA = [
    ("None", "baseline", "flipt_minimal_luna_fixed"),
    ("Claude Haiku 5.5", "ambiguous", "flipt_minimal_regulated_luna_haiku_ambiguous_fixed"),
    ("Claude Haiku 5.5", "enforce", "flipt_minimal_regulated_luna_haiku_enforce_fixed"),
    ("GPT-6 Luna", "ambiguous", "flipt_minimal_regulated_luna_luna_ambiguous_fixed"),
    ("GPT-6 Luna", "enforce", "flipt_minimal_regulated_luna_luna_enforce_fixed"),
    ("Claude Sonnet 5.5", "ambiguous", "flipt_minimal_regulated_luna_sonnet_ambiguous_fixed"),
    ("Claude Opus 5.5", "ambiguous", "flipt_minimal_regulated_luna_opus_ambiguous_fixed"),
]
"""The same, beside GPT-6 Luna workers."""


def main() -> int:
    progress.wrote(role_table(OUT, "SWE, DeepSeek V4.1 Flash workers", ROWS, "regulator", pieces=True))
    progress.wrote(role_table(OUT_LUNA, "SWE, GPT-6 Luna workers", ROWS_LUNA, "regulator", pieces=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
