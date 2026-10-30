"""The fixture of the tools tests: a copy of the real riemann_minimal run without its tool outputs."""
from __future__ import annotations

import pytest

from tests.support.runs_on_disk import RIEMANN, copy_run


@pytest.fixture
def riemann_copy(tmp_path):
    """runs/riemann_minimal copied without any tool's outputs, so a test never touches the real run."""
    return copy_run(RIEMANN, tmp_path / "riemann_minimal")
