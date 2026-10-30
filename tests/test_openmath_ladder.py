"""src/domains/openmath/openmath_ladder.py: which files in an agent's copy count as its own deliverable."""
from __future__ import annotations

import pytest

from src.domains.openmath.openmath_ladder import OpenMathDomain

COPY = "/workspace/a01"


@pytest.fixture(scope="module")
def domain() -> OpenMathDomain:
    block = {"row_id": "riemann", "layout": "split", "n_pieces": 2, "remote": "/team/repo.git",
             "agents": {"a01": {"workdir": COPY}}}
    return OpenMathDomain({"task": "openmath_agent", "openmath": block})


@pytest.mark.parametrize("path", [
    f"{COPY}/Riemann.zip",                                   # the archive at the copy's root
    f"{COPY}/Riemann.zip!audit/recovered/proof.tex",         # a member of that archive
    f"{COPY}/submission/proof.tex",                          # anything under submission/
    f"{COPY}/submission/results/a03/part3.bin",
    f"{COPY}/message.txt",                                   # message.txt at the root
])
def test_the_deliverable_counts_at_its_exact_places(domain, path):
    assert domain.own("a01", path)


@pytest.mark.parametrize("path", [
    f"{COPY}/work/a14/Riemann.zip",                          # a scratch copy of the archive in a subfolder
    f"{COPY}/work/a14/Riemann.zip!proof.tex",
    f"{COPY}/notes/message.txt",                             # message.txt outside the root
    f"{COPY}/old_message.txt",
    f"{COPY}/drafts/submission/proof.tex",                   # a submission/ folder that is not the copy's
    f"{COPY}/submission",                                    # the folder itself, not a file in it
    "/workspace/a02/Riemann.zip",                            # another agent's copy
    "/tmp/Riemann.zip",
])
def test_a_file_elsewhere_is_not_the_deliverable(domain, path):
    assert not domain.own("a01", path)
