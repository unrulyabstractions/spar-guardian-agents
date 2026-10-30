"""The fixed settings (src/configuration/settings.py): the code state a run records."""
from __future__ import annotations

from src.configuration import settings


def test_the_code_state_names_the_commit_and_every_submodule():
    code = settings.code_state()
    assert code["commit"] and len(code["commit"]) >= 7 and isinstance(code["dirty"], bool)
    assert set(code["submodules"]) >= {"cheatbench", "orbit", "lucy"}
    assert all(head is None or len(head) >= 7 for head in code["submodules"].values())
