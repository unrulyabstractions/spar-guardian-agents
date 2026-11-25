"""Each domain's delivery predicate (Benchmark.delivered) on a fake sandbox or a fake store. No Docker."""
from __future__ import annotations

import asyncio

from src.configuration.presets import build_config, with_overrides
from src.configuration.swarm_config import SwarmConfig
from src.domains.protein import ProteinDesign, _protein_scoring
from src.domains.swe import SWE, _swe_scoring
from src.domains.swe.swe_inputs import load_split, task_config
from src.sandbox import task_tool_store as tool_store
from tests.support.fakes import Sandboxes

TEAM = build_config("default", "small")                   # three agents, shared_task, access=split
SOLO = with_overrides(TEAM, ["mode=solo_task"])
LONE = SwarmConfig()


def ask(predicate) -> set[str]:
    return asyncio.run(predicate())


def test_swe_reads_one_rev_list_per_repository(monkeypatch):
    """A team with the team remote: the grader's `git rev-list` of base..main on the remote, for every agent. A lone
    agent and a solo_task agent: base..its own branch in its own machine. Empty output is no delivery."""
    boxes = Sandboxes(by_user=True)
    monkeypatch.setattr(_swe_scoring, "sandbox", boxes)
    swe = SWE("flipt-02e2", "as_released")
    base = task_config(load_split("flipt-02e2")["task"])["base_commit"]

    def own(agent: str) -> tuple:
        return agent, None, ("git", "-C", "/app", "rev-list", "-n", "1", f"{base}..refs/heads/{agent}")

    lone = swe.delivered(swe.plan(LONE))
    boxes.answers[own("a01")] = (0, "")
    assert ask(lone) == set() and boxes.calls == [own("a01")]
    boxes.answers[own("a01")] = (0, "4f1c2d9e\n")
    assert ask(lone) == {"a01"}
    boxes.calls.clear()
    main = (None, None, ("git", "--git-dir=/var/lib/team_view.git", "rev-list", "-n", "1", f"{base}..refs/heads/main"))
    shared = swe.delivered(swe.plan(TEAM))
    assert ask(shared) == set() and boxes.calls == [main]
    boxes.answers[main] = (0, "9a8b7c6d\n")
    assert ask(shared) == set(TEAM.names)
    boxes.calls.clear()
    assert ask(swe.delivered(swe.plan(SOLO))) == {"a01"}
    assert boxes.calls == [own(a) for a in TEAM.names]


def test_protein_reads_the_owners_accepted_answer(monkeypatch):
    """The owner's state in the sample store: the team's in shared_task (for every agent), else the agent's own. A
    protein owner has an answer once a design was accepted, and a protein team once main of its team remote also holds
    a binder (test_protein.py reads the remote)."""
    states: dict[str, dict] = {}
    monkeypatch.setattr(tool_store, "store", lambda: {tool_store.STATES: states})
    on_main = [False]

    async def binder_on_main(seat, work, staged) -> bool:
        return on_main[0]

    monkeypatch.setattr(_protein_scoring, "binder_on_main", binder_on_main)

    def delivered(benchmark, config):
        return benchmark.delivered(benchmark.plan(config))

    assert ask(delivered(ProteinDesign(), LONE)) == set()
    states["a01"] = {"accepted": 1}
    assert ask(delivered(ProteinDesign(), LONE)) == {"a01"}
    states.clear()
    states.update({"a01": {"accepted": 0}, "a02": {"accepted": 2}})
    assert ask(delivered(ProteinDesign(), SOLO)) == {"a02"}
    assert ask(delivered(ProteinDesign(), TEAM)) == set()
    states["team"] = {"accepted": 1}
    assert ask(delivered(ProteinDesign(), TEAM)) == set()
    on_main[0] = True
    assert ask(delivered(ProteinDesign(), TEAM)) == set(TEAM.names)

