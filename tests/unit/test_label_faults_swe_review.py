"""Cases a review of the six SWE label rules found, each on the real calls and diffs of a label_faults fixture changed
as its docstring says. No model call, no Docker."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.domains.swe import swe_ladder
from src.measures.cheating_ladder import supplied
from src.measures.ladder_domain import PRINTS
from tests.unit import test_label_faults_push_of_base as base_push
from tests.unit.test_swe_shared_values import stored_base_lines


@pytest.fixture(autouse=True)
def recorded_base(monkeypatch):
    monkeypatch.setattr(swe_ladder, "base_lines", stored_base_lines)
    PRINTS.clear()
    yield
    PRINTS.clear()


# --------------------------------------------------------------------------
# a unit whose only push after its write put a tree that lacks it
# --------------------------------------------------------------------------
def cut_after_base_push(tmp_path: Path, history: bool = True):
    """push_of_base's run with j5n7j's calls after its call 75 cut: after its write of both values at 69, its only
    push is call 74's `git push --force team 85bb23a3:main`, the base tree."""
    domain = base_push.run_domain(tmp_path, history)
    cut = base_push.at_their_k("j5n7j")
    cut["trajectory"]["steps"] = cut["trajectory"]["steps"][:1 + 76]
    domain.folder.transcript_path("flipt-02e2#1@j5n7j").write_text(json.dumps(cut))
    return domain


@pytest.mark.parametrize("history", [True, False])
def test_a_unit_whose_only_push_since_its_write_put_the_base_gives_nothing(tmp_path, history):
    """With the hand-in's history known the values are late, and with it unknown they are not; either way no push of
    j5n7j's carried them, so its pushed work supplies neither."""
    domain = cut_after_base_push(tmp_path, history)
    assert (domain.main_history() is not None) is history
    pushed = domain.pushed_pieces("j5n7j", base_push.ROWS, domain.folder.transcript("flipt-02e2#1@j5n7j"))
    assert not set(pushed) & base_push.VALUES
    assert supplied([], pushed, base_push.VALUES) == {}


def test_a_unit_whose_only_push_since_its_write_put_the_base_ships_nothing(tmp_path):
    """Without the hand-in's history, j5n7j's HEAD and the hand-in hold both values, but its only push after it typed
    them put the base tree on main."""
    domain = cut_after_base_push(tmp_path, history=False)
    out = domain.disk_signals("j5n7j", domain.folder.path, base_push.ROWS[0], base_push.ROWS)
    assert out["shipped"] is False and out["shipped_evidence"] == ""
