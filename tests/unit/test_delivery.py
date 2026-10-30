"""stop_after_delivery: each domain's predicate (Benchmark.delivered) on a fake sandbox or a fake store, and the rule
that awaits them (src/swarm/turn_rules/_turn_delivery.py) on the mock provider. No Docker."""
from __future__ import annotations

import asyncio
import json

import pytest
from inspect_ai import eval as inspect_eval
from inspect_ai.log import read_eval_log
from inspect_ai.util import store_as

from src.configuration.presets import build_config, with_overrides
from src.configuration.swarm_config import SwarmConfig, Window
from src.domains.interview import PrimeFactorization, SubsetSum
from src.domains.knowledge import KnowledgeWork
from src.domains.knowledge import _knowledge_review_tool as review
from src.domains.openmath import OpenMath
from src.domains.openmath import _openmath_scoring as openmath_probe
from src.domains.openmath._openmath_honeypot import holds_entry
from src.domains.protein import ProteinDesign, _protein_scoring
from src.domains.swe import SWE, _swe_scoring
from src.domains.swe.swe_inputs import load_split, task_config
from src.export.run_export import write_run
from src.sandbox import task_tool_store as tool_store
from src.swarm import turn_rules as rules
from src.swarm.turn_rules._turn_plan import team_delivery
from src.swarm.turn_rules._turn_state import DELIVERED_TAG
from tests.support.fakes import Sandboxes
from tests.support.mock_model import calls, tagged_infos
from tests.support.turn_rules_harness import (
    WorkLog,
    board_post,
    build_task,
    do,
    pull,
    run,
    scripted_model,
    step_ids,
    stop,
    store,
    submit,
)

TEAM = build_config("default", "small")                   # three agents, shared_task, access=split
SOLO = with_overrides(TEAM, ["mode=solo_task"])
LONE = SwarmConfig()


def ask(predicate) -> set[str]:
    return asyncio.run(predicate())


SHOW = ("show", "main:agent_evals.json")
UNREGISTERED = json.dumps({"agents": [{"agent": "agent-022"}, {"agent": "agent-014", "archive": "Riemann.zip"}]})
REGISTERED = json.dumps({"agents": [{"agent": "agent-022"}, {"agent": "agent-018", "message": "done"}]})


def test_openmath_reads_the_registry_on_main(monkeypatch):
    """One `git show main:agent_evals.json` per main: /workspace's as `user` for a lone agent and a team sharing it,
    the team remote's as root in the split layout, each copy's as its own user in solo_task. The row's own identity
    (agent-018 on riemann) must be listed; another agent's entry, a failed show or a broken file is no delivery."""
    boxes = Sandboxes(by_user=True)
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
    team once main of its team remote also holds a binder (test_protein.py reads the remote)."""
    states: dict[str, dict] = {}
    monkeypatch.setattr(tool_store, "store", lambda: {tool_store.STATES: states})
    on_main = [False]

    async def binder_on_main(seat, work, staged) -> bool:
        return on_main[0]

    monkeypatch.setattr(_protein_scoring, "binder_on_main", binder_on_main)

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


def shipped(team: list[str] | None = None):
    """A delivery predicate over the work log: an agent's deliverable exists once it worked on "ship"; with `team`,
    one agent's counts for the whole team."""
    async def delivered() -> set[str]:
        done = {e["agent"] for e in store_as(WorkLog).executed if e["item"] == "ship"}
        return set(team) if team and done else done
    return delivered


def delivery(sample, field: str):
    return sample.store.get(f"Delivered:{field}")


def delivery_infos(sample) -> list[tuple]:
    return [(i["kind"], i["round"], i["agents"]) for i in tagged_infos(sample, DELIVERED_TAG)]


LONE_SHIP = {"a01": [do("read"), do("ship"), do("after 1"), do("after 2"), do("after 3"), do("after 4")]}


@pytest.mark.parametrize("after", [0, 1, 2])
def test_a_lone_agent_stops_n_steps_after_its_deliverable(tmp_path, after):
    """The predicate is awaited at the end of each round until the deliverable shows, here at the end of round 2
    after two working steps; the agent then takes N more and is ended "delivered"."""
    config = pull(n_agents=1, steps_per_agent=6, stop_after_delivery=after)
    sample, _ = run(config, LONE_SHIP, tmp_path, delivered=shipped())
    assert step_ids(sample, "a01") == [f"r{r}-a01" for r in range(1, 3 + after)]
    assert store(sample, "work_steps") == {"a01": 2 + after} and store(sample, "ended") == {"a01": "delivered"}
    items = [e["item"] for e in sample.store.get("WorkLog:executed")]
    assert items == ["read", "ship", "after 1", "after 2"][:2 + after]
    assert delivery(sample, "seen") == {"a01": {"round": 2, "turn": 2}}
    assert (delivery(sample, "checks"), delivery(sample, "team"), delivery(sample, "after")) == (2, False, after)
    assert delivery_infos(sample) == [("seen", 2, ["a01"]), ("ended", 2 + after, ["a01"])]


def test_the_export_says_delivered(tmp_path):
    """The run directory of a rule-ended run: the episode's ended_by and first sighting, run.json's delivery block and
    the rules' ended reason."""
    config = pull(steps_per_agent=4, mode="solo_task", stop_after_delivery=1)
    scripts = {"a01": [do("ship"), do("x")], "a02": [do("a")], "a03": [do("b")]}
    sample, _, log = run(config, scripts, tmp_path, delivered=shipped(), with_log=True)
    write_run(log.location, tmp_path / "run")
    records = {r["orbit_agent"]: r for r in map(json.loads, (tmp_path / "run" / "episodes.jsonl").open())}
    assert records["a01"]["ended_by"] == "delivered" and records["a01"]["delivery"] == {"round": 1, "turn": 1}
    assert records["a01"]["rules"]["ended"] == "delivered" and records["a01"]["rules"]["work_steps"] == 2
    assert [records[a]["ended_by"] for a in ("a02", "a03")] == ["end", "end"]
    assert "delivery" not in records["a02"]
    run_json = json.loads((tmp_path / "run" / "run.json").read_text())
    assert run_json["delivery"]["ended"] == ["a01"] and run_json["delivery"]["seen"] == {"a01": {"round": 1, "turn": 1}}
    assert run_json["rules"]["ended"] == {"a01": "delivered"}


def test_an_episode_the_rule_ended_keeps_clean_when_a_limit_ends_the_others(tmp_path):
    """A solo_task agent the rule ended in round 1 finished its own episode, as an agent that submitted did: when a
    message limit later ends the sample, its record keeps no error and says "delivered"; the others say "limit"."""
    config = pull(steps_per_agent=40, mode="solo_task", stop_after_delivery=0)
    scripts = {"a01": [do("ship")], "a02": [do("a")], "a03": [do("b")]}
    [log] = inspect_eval(build_task(config, delivered=shipped()),
                         model=scripted_model(scripts, []), log_dir=str(tmp_path), display="none",
                         log_realtime=False, max_retries=0, message_limit=40)
    log = read_eval_log(log.location, resolve_attachments=True)
    assert log.samples[0].limit is not None and log.samples[0].limit.type == "message", log.samples[0].limit
    write_run(log.location, tmp_path / "run")
    records = {r["orbit_agent"]: r for r in map(json.loads, (tmp_path / "run" / "episodes.jsonl").open())}
    assert (records["a01"]["ended_by"], records["a01"]["error"], records["a01"]["failure"]) == ("delivered", None, None)
    assert records["a01"]["ok"] is True and records["a01"]["rules"]["work_steps"] == 1
    for agent in ("a02", "a03"):
        assert (records[agent]["ended_by"], records[agent]["failure"]) == ("limit", "max_turns"), records[agent]


def test_without_the_rule_nothing_is_written_and_no_sighting_ends_nobody(tmp_path):
    """After a run under the rule, a run without it in the same process takes every step and writes no Delivered
    record; under the rule, a predicate that never shows a deliverable ends nobody and is asked every round."""
    sample, _ = run(pull(n_agents=1, steps_per_agent=6, stop_after_delivery=0), LONE_SHIP, tmp_path,
                    delivered=shipped())
    assert store(sample, "ended") == {"a01": "delivered"}
    sample, _ = run(pull(n_agents=1, steps_per_agent=6), LONE_SHIP, tmp_path)
    assert store(sample, "work_steps") == {"a01": 6} and store(sample, "ended") == {}
    assert not [key for key in sample.store if key.startswith("Delivered:")]
    assert not any(isinstance(e.data, dict) and DELIVERED_TAG in e.data for e in sample.events
                   if e.event == "info")
    sample, _ = run(pull(n_agents=1, steps_per_agent=4, stop_after_delivery=0), {"a01": [do("work")]}, tmp_path,
                    delivered=shipped())
    assert store(sample, "work_steps") == {"a01": 4} and store(sample, "ended") == {}
    assert delivery(sample, "seen") == {} and delivery(sample, "checks") == 4


def test_an_episode_the_plan_ends_anyway_is_not_ended_by_the_rule(tmp_path):
    """The rule ends an episode only when it drops a step: a lone agent whose N steps after delivery are its last,
    and a team whose deliverable shows in the plan's last round, end with the plan."""
    sample, _ = run(pull(n_agents=1, steps_per_agent=3, stop_after_delivery=1), LONE_SHIP, tmp_path,
                    delivered=shipped())
    assert store(sample, "work_steps") == {"a01": 3} and store(sample, "ended") == {}
    assert delivery(sample, "seen") == {"a01": {"round": 2, "turn": 2}}
    config = pull(steps_per_agent=3, mode="shared_task", stop_after_delivery=0)
    scripts = {"a01": [do("a"), do("b"), do("ship")], "a02": [do("a")], "a03": [do("a")]}
    sample, _ = run(config, scripts, tmp_path, delivered=shipped(config.names))
    assert store(sample, "ended") == {} and step_ids(sample, "a02") == ["r1", "r2", "r3"]
    assert (delivery(sample, "halt_round"), delivery(sample, "cut")) == (3, [])


def test_the_rule_needs_a_predicate_and_reads_the_mode():
    with pytest.raises(ValueError, match="delivery predicate"):
        rules.rules_setup(pull(stop_after_delivery=1), ["a01", "a02", "a03"])
    names = ["a01", "a02", "a03"]
    assert team_delivery(pull(), names) is True
    assert team_delivery(pull(mode="solo_task"), names) is False
    assert team_delivery(pull(n_agents=1), ["a01"]) is False


def test_solo_task_agents_stop_on_their_own_deliverable_and_leave_their_steps_to_nobody(tmp_path):
    """Each solo_task agent stops one working step after its own deliverable shows. a02 ships in the follow-up of a
    board post, and the round's check comes after that follow-up. a03 never ships and keeps its own five rounds:
    the steps a01 and a02 leave are not pooled."""
    config = pull(steps_per_agent=5, mode="solo_task", stop_after_delivery=1)
    assert config.pooled_steps
    scripts = {"a01": [do("ship"), do("x"), do("y")],
               "a02": [do("a"), do("b"), board_post("shipping"), do("ship"), do("c"), do("d")],
               "a03": [do("c1")]}
    sample, _ = run(config, scripts, tmp_path, delivered=shipped())
    assert step_ids(sample, "a01") == ["r1", "r2"]
    assert step_ids(sample, "a02") == ["r1", "r2", "r3", "r3-free1", "r4"]
    assert step_ids(sample, "a03") == ["r1", "r2", "r3", "r4", "r5"]
    assert store(sample, "work_steps") == {"a01": 2, "a02": 4, "a03": 5} and store(sample, "rounds") == 5
    assert store(sample, "ended") == {"a01": "delivered", "a02": "delivered"}
    assert delivery(sample, "seen") == {"a01": {"round": 1, "turn": 1}, "a02": {"round": 3, "turn": 3}}
    assert delivery(sample, "checks") == 5 and delivery(sample, "team") is False
    # the agent ended by its own step carries the reason in that step's timeline entry
    assert [e["ended"] for e in store(sample, "timeline") if e["agent"] == "a01"] == [None, "delivered"]


@pytest.mark.parametrize("after", [0, 1])
def test_a_team_stops_n_rounds_after_the_round_that_shows_its_deliverable(tmp_path, after):
    """a01 merges in round 2; a02's board post that round earns a follow-up, which runs before the check. Every
    agent then takes N more rounds and is ended "delivered"; the rounds after are cut."""
    config = pull(steps_per_agent=6, mode="shared_task", stop_after_delivery=after)
    scripts = {"a01": [do("a"), do("ship"), do("b")],
               "a02": [do("a"), board_post("merged?"), do("b")],
               "a03": [do("a")]}
    sample, _ = run(config, scripts, tmp_path, delivered=shipped(config.names))
    rounds = [f"r{r}" for r in range(1, 3 + after)]
    assert step_ids(sample, "a01") == rounds and step_ids(sample, "a03") == rounds
    assert step_ids(sample, "a02") == ["r1", "r2", "r2-free1", *rounds[2:]]
    assert store(sample, "ended") == dict.fromkeys(config.names, "delivered")
    assert delivery(sample, "seen") == dict.fromkeys(config.names, {"round": 2, "turn": 2})
    assert (delivery(sample, "halt_round"), delivery(sample, "cut")) == (2 + after, config.names)
    assert (delivery(sample, "checks"), delivery(sample, "team")) == (2, True)
    assert delivery_infos(sample) == [("seen", 2, config.names), ("ended", 2 + after, config.names)]


def test_no_step_after_a_teams_last_round_runs(tmp_path):
    """The steps after a team's last round are cut as soon as the round is known. Here both workers ship and stop
    working in round 2, so the last round, 3, holds no step; the regulator, whose window opens at round 4, never acts
    and is ended "delivered" when round 2 ends."""
    config = pull(steps_per_agent=6, mode="shared_task", regulators=1, regulator_window=Window(start=4),
                  stop_after_delivery=1)
    roles = {"a01": "worker", "a02": "regulator", "a03": "worker"}
    scripts = {"a01": [do("a"), calls(("work", {"item": "ship"}), ("stop_working", {}))],
               "a02": [do("r")], "a03": [do("c"), stop()]}
    sample, _ = run(config, scripts, tmp_path, roles=roles, delivered=shipped(config.names))
    assert step_ids(sample, "a02") == [] and step_ids(sample, "a01") == ["r1", "r2"]
    assert store(sample, "ended") == {"a01": "stop_working", "a03": "stop_working", "a02": "delivered"}
    assert (delivery(sample, "halt_round"), delivery(sample, "cut")) == (3, ["a02"])


def test_in_sequence_the_team_is_checked_once_per_round(tmp_path):
    """With sequential rounds the check waits for the round's last step: a01 merges in its round-2 step, a02 and a03
    still take theirs, and nothing runs after round 2."""
    config = pull(steps_per_agent=4, mode="shared_task", stop_after_delivery=0, concurrent_rounds=False,
                  turn_order="round_robin")
    scripts = {"a01": [do("a"), do("ship")], "a02": [do("b")], "a03": [do("c")]}
    sample, _ = run(config, scripts, tmp_path, delivered=shipped(config.names))
    assert [e["step"] for e in store(sample, "timeline")] == ["r1-a01", "r1-a02", "r1-a03",
                                                               "r2-a01", "r2-a02", "r2-a03"]
    assert delivery(sample, "checks") == 2 and delivery(sample, "halt_round") == 2
    assert store(sample, "ended") == dict.fromkeys(config.names, "delivered")


def test_a_failing_predicate_shows_nothing_and_the_run_goes_on(tmp_path):
    async def broken() -> set[str]:
        raise RuntimeError("sandbox gone")

    sample, _ = run(pull(n_agents=1, steps_per_agent=3, stop_after_delivery=0), LONE_SHIP, tmp_path, delivered=broken)
    assert store(sample, "work_steps") == {"a01": 3} and store(sample, "ended") == {}
    assert delivery(sample, "errors") == [f"round {r}: RuntimeError: sandbox gone" for r in (1, 2, 3)]
    assert delivery(sample, "seen") == {} and delivery(sample, "checks") == 3


def test_a_submit_ends_the_run_and_the_rule_stays_out(tmp_path):
    config = pull(steps_per_agent=4, mode="shared_task", stop_after_delivery=0)
    scripts = {"a01": [submit("done"), do("a")], "a02": [do("ship")], "a03": [do("c")]}
    sample, _ = run(config, scripts, tmp_path, delivered=shipped(config.names))
    assert store(sample, "halted_after_round") == 3 and store(sample, "ended") == {}
    assert delivery(sample, "seen") == {} and delivery(sample, "checks") == 0
