"""The index of a study's runs (src/measures/run_index.py): a summary, and with --write its INDEX.jsonl.

    uv run python -m scripts.study.study_index runs/<study>            # a summary; writes nothing
    uv run python -m scripts.study.study_index runs/<study> --write    # also runs/<study>/INDEX.jsonl

--write writes <folder>/INDEX.jsonl. It refuses a folder that holds a run.json, a group, and a folder whose parent is
not a runs/ folder, so a mistyped path never writes into the data folders. Without --write nothing is written.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.measures.run_index import build, index_folder_problem, summary, write


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path, help="a study's runs folder, such as runs/regulator_scale_1")
    parser.add_argument("--write", action="store_true",
                        help="write <folder>/INDEX.jsonl; without it nothing is written")
    args = parser.parse_args(argv)
    if not args.folder.is_dir():
        parser.error(f"no folder {args.folder}")
    problem = index_folder_problem(args.folder) if args.write else None
    if problem:
        parser.error(f"--write refused: {problem}")
    records, notes = build(args.folder)
    print(summary(args.folder, records, notes))
    if args.write:
        print(f"== wrote {write(args.folder, records)} ({len(records)} records)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
