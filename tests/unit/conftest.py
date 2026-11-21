"""What the unit tests share: ordered agent names in place of the draw, SWE's image names with no Docker, no tracer for
the network log, and the stub domain registered with the code's evidence as a fixture."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.domains import BY_TASK
from src.domains.swe import swe_sandbox, swe_task
from src.measures.ladder_domain import Domain
from src.sandbox.network_log import network_monitor
from tests.support.ordered_ids import use_ordered_ids
from tests.support.stub_ladder import TASK, KeyDomain

use_ordered_ids()


@pytest.fixture(autouse=True)
def no_swe_images(monkeypatch):
    """A SWE sample names its images and builds none: ensure_images starts containers and, for a missing image, a build
    of half an hour."""
    monkeypatch.setattr(swe_task, "ensure_images", swe_sandbox.image_tags)


@pytest.fixture(autouse=True)
def no_tracer(monkeypatch):
    """A monitored sample's compose file gets no tracer: ensure_tracer asks Docker and may unpack bpftrace into a
    volume."""
    monkeypatch.setattr(network_monitor, "ensure_tracer", lambda image: None)


@pytest.fixture
def key_task(monkeypatch):
    """The stub domain registered as TASK; the fixture's value swaps in another Domain class."""
    def use(domain: type[Domain]) -> None:
        monkeypatch.setitem(BY_TASK, TASK, SimpleNamespace(ladder=domain))
    use(KeyDomain)
    return use
