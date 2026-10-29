"""The run directories the tools tests read: runs/riemann_minimal, the real three-agent openmath run every tool is
checked against, and a copy of it without earlier tool outputs, so a test never writes into the real run.

    @needs_riemann
    def test_something(tmp_path):
        copy = copy_run(RIEMANN, tmp_path / "riemann_minimal")

The tests skip when the run is not on disk (runs/ is gitignored).
"""
from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "runs"
RIEMANN = RUNS_DIR / "riemann_minimal"
LONE = RUNS_DIR / "smoke" / "complete"          # the openmath smoke suite's lone-agent run
needs_riemann = pytest.mark.skipif(not (RIEMANN / "run.json").is_file(), reason="runs/riemann_minimal is not on disk")


def copy_run(src: Path, dst: Path, *, ignore: Iterable[str] = ("tools",), keep: Iterable[str] = ()) -> Path:
    """Copy a run directory without the directories named in `ignore` (every tool's outputs by default), then put
    back the files of tools/ named in `keep` (the shared reader's caches, say)."""
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*ignore))
    for name in keep:
        if (src / "tools" / name).is_file():
            (dst / "tools").mkdir(exist_ok=True)
            shutil.copy2(src / "tools" / name, dst / "tools" / name)
    return dst
