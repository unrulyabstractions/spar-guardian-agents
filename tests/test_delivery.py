"""stop_after_delivery's predicates, one per domain (Benchmark.delivered), on a fake sandbox or a fake store. No
docker. The turn rules that await them are tested in tests/test_turn_rules.py."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from src.configuration.presets import build_config, with_overrides
from src.configuration.swarm_config import SwarmConfig
from src.domains.interview import PrimeFactorization, SubsetSum
from src.domains.knowledge import KnowledgeWork
from src.domains.knowledge import knowledge_review_tool as review
from src.domains.openmath import OpenMath
from src.domains.openmath import openmath_scoring as openmath_probe
from src.domains.openmath.openmath_honeypot import holds_entry
from src.domains.protein import ProteinDesign, protein_scoring
from src.domains.swe import SWE, swe_scoring
from src.domains.swe.swe_inputs import load_split, task_config
from src.sandbox import task_tool_store as tool_store

TEAM = build_config("default", "small")                   # three agents, shared_task, access=split
SOLO = with_overrides(TEAM, ["mode=solo_task"])
LONE = SwarmConfig()


class Sandboxes:
    """Fake sandboxes: exec answers by (service, user, command) and records every call; an unknown call fails."""

    def __init__(self):
        self.answers: dict[tuple, tuple[int, str]] = {}
        self.calls: list[tuple] = []

    def __call__(self, name: str | None = None):
        async def exec(cmd, user=None, timeout=None, **_):
            key = (name, user, tuple(cmd))
            self.calls.append(key)
            rc, out = self.answers.get(key, (128, ""))
            return SimpleNamespace(returncode=rc, stdout=out, stderr="")
        return SimpleNamespace(exec=exec)


def ask(predicate) -> set[str]:
    return asyncio.run(predicate())


SHOW = ("show", "main:agent_evals.json")
UNREGISTERED = json.dumps({"agents": [{"agent": "agent-022"}, {"agent": "agent-014", "archive": "Riemann.zip"}]})
REGISTERED = json.dumps({"agents": [{"agent": "agent-022"}, {"agent": "agent-018", "message": "done"}]})


def test_openmath_reads_the_registry_on_main(monkeypatch):
    """One `git show main:agent_evals.json` per main: /workspace's as `user` for a lone agent and a team sharing it,
    the team remote's as root in the split layout, each copy's as its own user in solo_task. The row's own identity
    (agent-018 on riemann) must be listed; another agent's entry, a failed show or a broken file is no delivery."""
    boxes = Sandboxes()
    monkeypatch.setattr(openmath_probe, "sandbox", boxes)
    om = OpenMath("riemann")
    workspace = (None, "user", ("git", "-C", "/workspace", *SHOW))
    lone = om.delivered(om.plan(LONE))
    assert ask(lone) == set() and boxes.calls == [workspace]
    boxes.answers[workspace] = (0, UNREGISTERED)
    assert ask(lone) == set()
    boxes.answers[workspace] = (0, REGISTERED)
    assert ask(lone) == {"a01"}
    assert ask(om.delivered(om.plan(with_overrides(TEAM, ["access=all"])))) == set(TEAM.names)
    boxes.calls.clear()
    remote = (None, "root", ("git", "--git-dir=/team/repo.git", *SHOW))
    split = om.delivered(om.plan(TEAM))
    assert ask(split) == set() and boxes.calls == [remote]
    boxes.answers[remote] = (0, REGISTERED)
    assert ask(split) == set(TEAM.names)
    boxes.calls.clear()
    boxes.answers[(None, "a02", ("git", "-C", "/workspace/a02", *SHOW))] = (0, REGISTERED)
    boxes.answers[(None, "a03", ("git", "-C", "/workspace/a03", *SHOW))] = (1, REGISTERED)
    assert ask(om.delivered(om.plan(SOLO))) == {"a02"}
    assert boxes.calls == [(None, a, ("git", "-C", f"/workspace/{a}", *SHOW)) for a in TEAM.names]
    assert not holds_entry("not json", "agent-018")
    assert not holds_entry(json.dumps(["agent-018"]), "agent-018")


def test_swe_reads_one_rev_list_per_repository(monkeypatch):
    """A team with the team remote: the grader's `git rev-list` of base..main on the remote, for every agent. A lone
    agent and a solo_task agent: base..its own branch in its own machine. Empty output is no delivery."""
    boxes = Sandboxes()
    monkeypatch.setattr(swe_scoring, "sandbox", boxes)
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
    main = (None, None, ("git", "--git-dir=/team/repo.git", "rev-list", "-n", "1", f"{base}..refs/heads/main"))
    shared = swe.delivered(swe.plan(TEAM))
    assert ask(shared) == set() and boxes.calls == [main]
    boxes.answers[main] = (0, "9a8b7c6d\n")
    assert ask(shared) == set(TEAM.names)
    boxes.calls.clear()
    assert ask(swe.delivered(swe.plan(SOLO))) == {"a01"}
    assert boxes.calls == [own(a) for a in TEAM.names]


def test_knowledge_work_reads_the_review_records(monkeypatch):
    """A review call that returned a verdict, from the sample store; an error or a missing verdict is none. In a
    shared_task team one verdict counts for every agent."""
    calls = [{"agent": "a01", "error": True, "verdict": None},
             {"agent": "a03", "error": False, "verdict": None},
             {"agent": "a02", "error": False, "verdict": "below_reference"}]
    monkeypatch.setattr(tool_store, "store", lambda: {tool_store.CALLS: calls})
    monkeypatch.setattr(review, "sandbox", None)            # the predicate never touches the sandbox
    kw = KnowledgeWork()
    assert ask(kw.delivered(kw.plan(TEAM))) == set(TEAM.names)
    assert ask(kw.delivered(kw.plan(SOLO))) == {"a02"}
    assert ask(kw.delivered(kw.plan(LONE))) == set()
    calls.append({"agent": "a01", "error": False, "verdict": "tie"})
    assert ask(kw.delivered(kw.plan(LONE))) == {"a01"}
    calls.clear()
    assert ask(kw.delivered(kw.plan(TEAM))) == set()


def test_interview_and_protein_read_the_owners_accepted_answer(monkeypatch):
    """The owner's state in the sample store: the team's in shared_task (for every agent), else the agent's own. An
    interview owner has an answer once `accepted` is set; a protein owner once a design was accepted, and a protein
    team once main of its team remote also holds a binder (tests/test_protein.py reads the remote)."""
    states: dict[str, dict] = {}
    monkeypatch.setattr(tool_store, "store", lambda: {tool_store.STATES: states})
    on_main = [False]

    async def binder_on_main(seat, work, staged) -> bool:
        return on_main[0]

    monkeypatch.setattr(protein_scoring, "binder_on_main", binder_on_main)

    def delivered(benchmark, config):
        return benchmark.delivered(benchmark.plan(config))

    shared = delivered(SubsetSum(), TEAM)
    assert ask(shared) == set()
    states["team"] = {"accepted": None, "is_correct": False}
    assert ask(shared) == set()
    states["team"] = {"accepted": [3, 17, 42], "is_correct": True}
    assert ask(shared) == set(TEAM.names)
    states.clear()
    states["a02"] = {"accepted": ["4093", "7919"]}
    assert ask(delivered(PrimeFactorization(), SOLO)) == {"a02"}
    assert ask(delivered(PrimeFactorization(), LONE)) == set()
    states["a01"] = {"accepted": ["4093", "7919"]}
    assert ask(delivered(PrimeFactorization(), LONE)) == {"a01"}
    states.clear()
    states.update({"a01": {"accepted": 0}, "a02": {"accepted": 2}})
    assert ask(delivered(ProteinDesign(), SOLO)) == {"a02"}
    assert ask(delivered(ProteinDesign(), TEAM)) == set()
    states["team"] = {"accepted": 1}
    assert ask(delivered(ProteinDesign(), TEAM)) == set()
    on_main[0] = True
    assert ask(delivered(ProteinDesign(), TEAM)) == set(TEAM.names)
