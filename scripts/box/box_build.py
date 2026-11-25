"""Build on a box what a plan's first runs would build, before they start together: each task's rows and images.

    .venv/bin/python -m scripts.box.box_build swe creative_writing

box_setup_remote.sh runs it with the tasks of the box's runs (the registry's `tasks`). A task is run_study's, the
folder of a cell module. Every cell of studies/cells/swe/ runs the issue flipt-02e2 (ian_flipt, ian_gflipt and its
checker, which runs on the grader's image, and ian_swe0). The poem's cells run creative writing's images and the
binder's run the protein rows and the python311 machine image. A task without an entry in BUILDS is refused until its
domain's builders are added there.
"""
from __future__ import annotations

import sys

from src.domains import DOMAINS
from src.domains.creative import creative_sandbox
from src.domains.protein import protein_rows_build
from src.domains.swe import swe_sandbox
from src.domains.swe.swe_inputs import load_split
from src.domains.swe.swe_settings import DEFAULT_ROW as SWE_ROW
from src.sandbox.machines import agent_machines


def build_swe() -> None:
    """The issue's amd64 image from its CheatBench environment and the native image on it."""
    print(swe_sandbox.ensure_images(SWE_ROW))
    print(f"{SWE_ROW}: {sorted(load_split(SWE_ROW))}")


def build_creative_writing() -> None:
    """The python311 image and the two images on it, with CheatBench's scorer compiled: the agents' and the review
    sidecar's. The rows are CheatBench's rows.py and need no build."""
    print(creative_sandbox.ensure_images())
    print(f"{len(DOMAINS['creative_writing'].rows())} rows")


def build_protein_design() -> None:
    """The rows from CheatBench's generator (20 FASTA records from rcsb.org) and the python311 machine image."""
    protein_rows_build.build()
    print(agent_machines.ensure_image())
    print(f"{len(DOMAINS['protein_design'].rows())} rows")


BUILDS = {"swe": build_swe, "creative_writing": build_creative_writing, "protein_design": build_protein_design}
"""Each task's first-run steps, by run_study's task."""


def main(argv: list[str]) -> int:
    unknown = sorted(set(argv) - set(BUILDS))
    if not argv or unknown:
        print(f"refused: no build steps for {unknown or 'no task'}; the tasks with steps are {sorted(BUILDS)}")
        return 1
    for task in argv:
        print(f"== build {task}", flush=True)
        BUILDS[task]()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
