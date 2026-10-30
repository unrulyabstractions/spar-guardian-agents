"""The real runs some tests read when they are on this machine (runs/ is gitignored): runs/riemann_minimal, the
three-agent openmath run every tool is checked against, and the smoke suites' runs. A test never writes into one: it
works on a copy.

    @needs_riemann
    def test_something(tmp_path):
        copy = copy_run(RIEMANN, tmp_path / "riemann_minimal")

    source = run_on_disk("smoke_kw", "*", "split")          # the newest match; the test skips without one
"""
from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path

import pytest

from src.configuration import settings

RIEMANN = settings.RUNS / "riemann_minimal"
LONE = settings.RUNS / "smoke" / "complete"          # the openmath smoke suite's lone-agent run


def needs_run(path: Path):
    """A marker that skips a test while the run at `path` is not on disk."""
    return pytest.mark.skipif(not (path / "run.json").is_file(), reason=f"{path} is not on this machine")


needs_riemann = needs_run(RIEMANN)
needs_lone = needs_run(LONE)


def run_on_disk(*parts: str) -> Path:
    """The last run under runs/ whose path matches the parts, each a name or a glob; the test skips without one."""
    found = sorted(settings.RUNS.glob("/".join([*parts, "run.json"])))
    if not found:
        pytest.skip(f"no run {'/'.join(parts)} on this machine")
    return found[-1].parent


def copy_run(src: Path, dst: Path, *, ignore: Iterable[str] = ("tools",)) -> Path:
    """Copy a run directory without the directories named in `ignore` (every tool's outputs by default)."""
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*ignore))
    return dst
