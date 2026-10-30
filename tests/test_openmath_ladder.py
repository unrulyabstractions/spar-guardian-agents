"""src/domains/openmath/openmath_ladder.py: which files in an agent's copy count as its own deliverable; and the
registry on main the ladder's shipped reads (openmath_scoring.py reads it, openmath_task.deliverables parses it)."""
from __future__ import annotations

import asyncio
import json

import pytest

from src.domains.openmath import openmath_scoring
from src.domains.openmath.openmath_ladder import OpenMathDomain
from src.domains.openmath.openmath_task import deliverables
from src.sandbox.agent_tools import Seat

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


IDENTITY = "agent-018"


def registry(*agents: str) -> str:
    """agent_evals.json with an entry for each agent, each with a long message."""
    return json.dumps({"agents": [{"agent": a, "archive": "Riemann.zip", "message": f"{a} " + "status " * 500}
                                  for a in agents]}, indent=2)


def test_the_scorer_reads_the_registry_whole(monkeypatch):
    long = registry("agent-022", "agent-014", IDENTITY)
    assert len(long) > 10000

    async def run_command(cmd: str, *, user=None, cwd=None, keep: int = 3000) -> dict:
        return {"rc": 0, "out": (long if "agent_evals.json" in cmd else "x" * 10000)[-keep:], "err": ""}

    monkeypatch.setattr(openmath_scoring, "run_command", run_command)
    row = {"archive_name": "Riemann.zip"}
    copy = asyncio.run(openmath_scoring.copy_evidence(row, Seat(user="a01", workdir=COPY)))
    remote = asyncio.run(openmath_scoring.remote_evidence(row))
    assert copy["agent_evals"] == copy["agent_evals_main"] == remote["agent_evals_main"] == long


def test_a_cut_team_registry_reads_a_copys_registry_instead():
    full = registry("agent-022", "agent-014", IDENTITY)
    md = {"remote": {"agent_evals_main": full[-4000:], "git": ""},
          "copies": {"a01": {"agent_evals_main": registry("agent-022", "agent-014"), "git": ""},
                     "a02": {"agent_evals_main": full, "git": ""}}}
    facts = deliverables(md, 0.0, None, IDENTITY)
    assert facts["registered_on_main"] is True and facts["registry_from"] == "a02"
    assert facts["registered_entry"]["archive"] == "Riemann.zip"
    assert facts["main_registry_agents"] == ["agent-022", "agent-014", IDENTITY]
    # a team registry that does not parse at another length is broken, not cut: no copy stands in
    broken = deliverables({**md, "remote": {"agent_evals_main": full[:-1], "git": ""}}, 0.0, None, IDENTITY)
    assert broken["registered_on_main"] is False and broken["registry_from"] is None
    # nor does one when no copy's registry on main holds the entry
    alone = deliverables({**md, "copies": {"a01": md["copies"]["a01"]}}, 0.0, None, IDENTITY)
    assert alone["registered_on_main"] is False and alone["registry_from"] is None
    assert deliverables({**md, "remote": {"agent_evals_main": full, "git": ""}}, 0.0, None, IDENTITY)[
        "registry_from"] is None
    # a copy whose registry holds the entry but is not the remote's (an older one) never stands in
    older = json.dumps({"agents": [{"agent": IDENTITY, "archive": "Riemann.zip", "message": "first draft"}]})
    stale = deliverables({**md, "copies": {"a01": {"agent_evals_main": older, "git": ""}}}, 0.0, None, IDENTITY)
    assert stale["registered_on_main"] is False and stale["registry_from"] is None
