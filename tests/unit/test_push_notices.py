"""Push notices (src/swarm/turn_rules/_turn_pushes.py): the harness reads the team's head before and after every
round, and every influencer, never a worker, hears each change once, with its pusher when known."""
from __future__ import annotations

from inspect_ai.util import store_as

from src.configuration.settings import load_prompts
from src.domains import DOMAINS
from src.domains.protein.protein_task import ProteinDesign
from src.swarm.turn_rules import TurnState
from src.swarm.turn_rules._turn_pushes import PUSH_TAG
from src.tasks import team_head
from tests.support.mock_model import tagged_infos
from tests.support.turn_rules_harness import by_agent, do, run, sequential
from tests.unit.test_submit_and_stop import ROLES, TEAM

BASE, FIRST, SECOND = "a" * 40, "b" * 40, "c" * 40


def heads_after(rounds: dict[tuple[str, int], tuple[str, str | None]]):
    """A reader whose head moves once the named agent's round has ended: {(agent, round): (head, by)}."""
    async def read():
        done = {(e["agent"], e["round"]) for e in store_as(TurnState).timeline}
        head = {"head": BASE, "by": None}
        for key, (moved, by) in rounds.items():
            if key in done:
                head = {"head": moved, "by": by}
        return head
    return read


def lines(record: dict) -> list[str]:
    """The presence lines of a provider call's input, each round's message's presence part cut into its lines."""
    return [line for part in record["presence"] for line in part.split("\n")]


def test_every_influencer_hears_each_push_once_and_a_worker_never(tmp_path):
    reader = heads_after({("a01", 1): (FIRST, "a01"), ("a03", 2): (SECOND, None)})
    config = sequential(n_agents=3, regulators=1, rounds=3)
    scripts = {a: [do(f"{a}{r}") for r in range(3)] for a in ("a01", "a02", "a03")}
    roles = {"a01": "worker", "a02": "regulator", "a03": "worker"}
    sample, rec = run(config, scripts, tmp_path, roles=roles, team_head=reader)
    named = f"a01 pushed to main of the team remote, now at {FIRST[:12]}. Read it."
    unnamed = f"Main of the team remote moved to {SECOND[:12]}. Read it."
    regulator = [lines(r) for r in by_agent(rec, "a02")]
    assert named in regulator[0] and unnamed not in regulator[1]
    assert unnamed in regulator[2]
    assert regulator[-1].count(named) == 1 and regulator[-1].count(unnamed) == 1
    assert [e for e in by_agent(rec, "a02")[-1]["events"] if e.startswith("push:")] == ["push:0", "push:1"]
    for worker in ("a01", "a03"):
        assert not any("team remote" in line for r in by_agent(rec, worker) for line in lines(r))
    pushes = sample.store.get("Pushes:pushes")
    assert [(p["head"], p["by"], p["round"]) for p in pushes] == [(FIRST, "a01", 1), (SECOND, None, 2)]
    assert [i["head"] for i in tagged_infos(sample, PUSH_TAG)] == [FIRST, SECOND]


def test_a_run_without_a_reader_records_no_push(tmp_path):
    config = sequential(n_agents=2, regulators=1, rounds=1)
    sample, rec = run(config, {"a01": [do("A")], "a02": [do("B")]}, tmp_path,
                      roles={"a01": "worker", "a02": "regulator"})
    assert sample.store.get("Pushes:pushes") is None


def test_the_domains_with_a_team_remote_give_a_reader_and_a_read_line():
    texts = load_prompts()["presence"]
    assert set(texts["push_read"]) == {"swe", "openmath", "protein_design"}
    given = {}
    for key, cls in DOMAINS.items():
        bench = cls()
        given[key] = bool(team_head(bench, bench.plan(TEAM, ROLES)))
    assert given == {"openmath": True, "swe": True, "protein_design": True, "knowledge_work": False,
                     "subset_sum": False, "prime_factorization": False, "creative_writing": False, "chess": False}
    lone = ProteinDesign()
    assert team_head(lone, lone.plan(TEAM.model_copy(update={"mode": "solo_task"}), ROLES)) == {}
