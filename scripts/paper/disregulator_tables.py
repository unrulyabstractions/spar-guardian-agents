"""Disregulators with GPT-6 Luna workers on creative writing and SWE, valid runs only (scripts/paper/study_ladders.py
cell_runs and role_table): each run's team Cheating and Collusion rungs, on SWE the pieces its hand-in holds and the
grader's pass, the disregulator's posts and those that ask a teammate for the honeypot (src/measures/team_ladder.py
solicits), and per cell the teams that cheated and the teams that accessed the honeypot. Writes
tmp/tables/results/table_creative_disregulators.png and tmp/tables/results/table_swe_disregulators.png.

    uv run python -m scripts.paper.disregulator_tables    # make results-tables runs every one
"""
from __future__ import annotations

import sys

from scripts.paper.study_ladders import role_table
from src.configuration.settings import ROOT

OUT = ROOT / "tmp" / "tables" / "results"
CREATIVE = [("None", "baseline", "poem_minimal_luna"),
            ("Disregulator", "persuade", "poem_minimal_disregulated_luna_persuade"),
            ("Disregulator", "overt", "poem_minimal_disregulated_luna_overt"),
            ("Both", "+ Haiku enforce", "poem_minimal_regulated_disregulated_luna_haiku_enforce_persuade"),
            ("All workers", "told to cheat", "poem_minimal_cheat_luna")]
SWE = [("None", "baseline", "flipt_minimal_luna_fixed"),
       ("Disregulator", "persuade", "flipt_minimal_disregulated_luna_persuade_fixed"),
       ("Both", "+ Haiku enforce", "flipt_minimal_regulated_disregulated_luna_haiku_enforce_persuade_fixed"),
       ("All workers", "told to cheat", "flipt_minimal_cheat_luna_fixed")]


def main() -> int:
    print(role_table(OUT / "table_creative_disregulators.png", "Creative writing, GPT-6 Luna workers", CREATIVE,
                     "disregulator", per_row=2))
    print(role_table(OUT / "table_swe_disregulators.png", "SWE, GPT-6 Luna workers", SWE, "disregulator",
                     pieces=True, per_row=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
