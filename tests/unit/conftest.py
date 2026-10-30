"""The fixture the ladder tests share: the stub domain registered with the code ladder."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.measures import code_ladder
from src.measures.ladder_domain import Domain
from tests.support.stub_ladder import TASK, KeyDomain


@pytest.fixture
def key_task(monkeypatch):
    """The stub domain registered as TASK; the fixture's value swaps in another Domain class."""
    def use(domain: type[Domain]) -> None:
        monkeypatch.setitem(code_ladder.BY_TASK, TASK, SimpleNamespace(ladder=domain))
    use(KeyDomain)
    return use
