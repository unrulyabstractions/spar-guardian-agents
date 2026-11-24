"""src/domains/swe/harness/: our copy of Lucy's split and pieces, which the SWE task reads, not her repository."""
import pytest

from src.configuration import settings
from src.domains.swe import swe_inputs
from src.domains.swe.swe_settings import HARNESS

THEIRS = settings.ROOT / "submodules" / "lucy" / "experiments" / "orbit_cheatbench"


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for kind in ("splits", "pieces") for p in (root / kind).rglob("*")
            if p.is_file()}


def test_the_swe_task_reads_its_own_copy_of_the_split():
    """No path of the SWE task's inputs lies under submodules/: a run needs no checkout of Lucy's repository."""
    assert HARNESS == settings.ROOT / "src" / "domains" / "swe" / "harness"
    assert "lucy" not in settings.EXPECTED_SUBMODULE_HEADS and not hasattr(settings, "LUCY")
    assert swe_inputs.issues() == ["flipt-02e2", "openlibrary-3aee", "vuls-1832"]
    assert len(files(HARNESS)) == 21
    assert swe_inputs.load_split("flipt-02e2") and swe_inputs.piece_patch("flipt-02e2", "A")


@pytest.mark.skipif(not (THEIRS / "splits").is_dir(), reason="Lucy's repository is not checked out")
def test_the_copy_equals_lucys_files_where_her_repository_is_checked_out():
    assert files(HARNESS) == files(THEIRS)
