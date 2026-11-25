"""The runs the tools tests read: generated once per session (tests/support/sample_runs.py) and put where a run's name
resolves, so a clean clone needs no run and no test touches a real one."""
from __future__ import annotations

import pytest

from src.configuration import settings
from tests.support.sample_runs import copy_run, write_binder_run, write_lone_run


@pytest.fixture(scope="session")
def sample_runs(tmp_path_factory):
    """A runs/ folder holding binder_minimal and smoke/complete."""
    root = tmp_path_factory.mktemp("runs")
    write_binder_run(root / "binder_minimal")
    (root / "smoke").mkdir()
    write_lone_run(root / "smoke" / "complete")
    return root


@pytest.fixture(autouse=True)
def runs_folder(sample_runs, monkeypatch):
    """settings.RUNS is the generated folder."""
    monkeypatch.setattr(settings, "RUNS", sample_runs)
    return sample_runs


@pytest.fixture
def binder_copy(sample_runs, tmp_path):
    """binder_minimal copied without any tool's outputs, for a test that writes into the run."""
    return copy_run(sample_runs / "binder_minimal", tmp_path / "binder_minimal")
