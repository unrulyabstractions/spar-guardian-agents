"""What the unit tests share: ordered agent names in place of the draw, SWE's image names with no Docker, no tracer for
the network log, the proc-hiding profile's name with no Docker, and the stub domain registered with the code's evidence
as a fixture."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.domains import BY_TASK
from src.domains.swe import swe_sandbox, swe_task
from src.measures.ladder_domain import Domain
from src.sandbox.network_log import network_monitor
from src.sandbox.proc_hiding import proc_hiding
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


@pytest.fixture(autouse=True)
def no_profile_load(monkeypatch):
    """A sample's compose file names the proc-hiding profile without loading it: ensure_profile asks Docker and runs a
    privileged container."""
    monkeypatch.setattr(proc_hiding, "ensure_profile", lambda: proc_hiding.PROFILE)


@pytest.fixture
def key_task(monkeypatch):
    """The stub domain registered as TASK; the fixture's value swaps in another Domain class."""
    def use(domain: type[Domain]) -> None:
        monkeypatch.setitem(BY_TASK, TASK, SimpleNamespace(ladder=domain))
    use(KeyDomain)
    return use
