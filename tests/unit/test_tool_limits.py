"""Limits on one tool's calls (SwarmConfig.tool_limits, src/swarm/tool_limits.py): the text form, the counts of the
run, of each round and of N rounds in a row on the turn rules' mock task, the description's sentence, a task's
default limits and bounds on a stub task, the refusal of a limit on a tool no agent holds, and bindings left as they
were with no limit. No Docker, no paid calls."""
from __future__ import annotations

import pytest
from inspect_ai.tool import ToolDef
from orbit.tools import ToolContext, ToolFactory
from pydantic import ValidationError

from src.configuration.presets import build_config, with_overrides
from src.configuration.settings import load_prompts
from src.configuration.swarm_config import SwarmConfig, ToolLimit
from src.domains.protein import ProteinDesign
from src.sandbox.machines import agent_machines
from src.swarm import tool_limits
from src.swarm.tool_limits import limited, limited_bindings, most_calls, run_limits
from src.tasks import swarm_task
from tests.support.turn_rules_harness import do, outcomes, pull, run, store, work

TEXTS = load_prompts()["tool_limits"]


def executed(sample, agent: str) -> list[str]:
    return [e["item"] for e in sample.store.get("WorkLog:executed") or [] if e["agent"] == agent]


def refused(sample) -> list[tuple[str, int]]:
    """(agent, round) of each call a limit refused, as the store keeps them."""
    return [(r["agent"], r["round"]) for r in sample.store.get("ToolCalls:refused") or []]


def refusal(key: str, calls: int, left: int = 1) -> str:
    count = TEXTS["count"]["one" if calls == 1 else "many"].format(calls=calls)
    again = TEXTS["again"]["one" if left == 1 else "many"].format(left=left)
    return TEXTS["refused"][key].format(count=count, again=again)


def refused_rounds(sample, agent: str) -> list[int]:
    return [r for a, r in refused(sample) if a == agent]


# ------------------------------------------------------------------------------------------------ the text form

def test_the_text_form_reads_one_limit_or_several():
    one = SwarmConfig(tool_limits="check_fix:2:agent:run").tool_limits
    assert one == (ToolLimit(tool="check_fix", calls=2, per="agent", rounds=None),)
    assert one[0].text == "check_fix:2:agent:run"
    two = with_overrides(pull(n_agents=2), ["tool_limits=check_fix:2:agent:run, alert_humans:1:team:round"])
    assert [limit.text for limit in two.tool_limits] == ["check_fix:2:agent:run", "alert_humans:1:team:round"]
    # a config built again from its dump, as replace and run.json do, keeps them
    assert two.replace(seed=3).tool_limits == two.tool_limits
    assert SwarmConfig.model_validate(two.model_dump(mode="json")).tool_limits == two.tool_limits
    for empty in ("none", "", "null"):
        assert with_overrides(two, [f"tool_limits={empty}"]).tool_limits == ()


def test_the_text_form_reads_a_number_of_rounds():
    [five] = SwarmConfig(tool_limits="check_fix:1:agent:5rounds").tool_limits
    assert (five.rounds, five.text) == (5, "check_fix:1:agent:5rounds")
    [one] = SwarmConfig(tool_limits="check_fix:1:agent:1rounds").tool_limits
    assert (one.rounds, one.text) == (1, "check_fix:1:agent:round")     # round is 1rounds
    assert SwarmConfig(tool_limits="check_fix:1:agent:round").tool_limits == (one,)
    [team] = pull(n_agents=2, tool_limits="check_fix:2:team:3rounds").tool_limits
    assert (team.per, team.calls, team.rounds) == ("team", 2, 3)


def test_no_limit_by_default():
    assert SwarmConfig().tool_limits == () and build_config("default", "small").tool_limits == ()


@pytest.mark.parametrize("text", ["work", "work:2", "work:2:agent", "work:2:agent:run:x", ":2:agent:run",
                                  "work:two:agent:run", "work:0:agent:run", "work:-1:agent:run",
                                  "work:2:everyone:run", "work:2:agent:week", "work:2:agent:run,",
                                  "work:2:agent:run,work:3:agent:run", "work:1:agent:0rounds",
                                  "work:1:agent:rounds", "work:1:agent:5", "work:1:agent:5round",
                                  "work:1:agent:-2rounds", "work:1:agent:round,work:2:agent:1rounds"])
def test_nonsense_is_refused(text):
    with pytest.raises(ValidationError):
        pull(n_agents=2, tool_limits=text)


def test_a_team_limit_needs_agents_told_of_their_team():
    with pytest.raises(ValidationError, match="team limit"):
        pull(n_agents=1, tool_limits="work:2:team:run")
    with pytest.raises(ValidationError, match="team limit"):
        pull(n_agents=2, mode="solo_task", tool_limits="work:2:team:round")
    assert pull(n_agents=1, tool_limits="work:2:agent:round").tool_limits[0].per == "agent"


# ------------------------------------------------------------------------------------------------ run and round

def test_an_agents_own_count_for_the_run(tmp_path):
    """work:2:agent:run: each agent's third call is refused, and so is every later one."""
    config = pull(n_agents=2, rounds=4, concurrent_rounds=False, turn_order="round_robin",
                  tool_limits="work:2:agent:run")
    sample, _ = run(config, {"a01": [do("A")], "a02": [do("B")]}, tmp_path)
    assert executed(sample, "a01") == ["A", "A"] and executed(sample, "a02") == ["B", "B"]
    assert refused(sample) == [("a01", 3), ("a02", 3), ("a01", 4), ("a02", 4)]
    errors = [error for _, _, error in outcomes(sample, "work")]
    assert errors == [None] * 4 + [refusal("agent_run", 2)] * 4
    # a refused call still uses one of the agent's calls, and its timeline entry keeps it refused
    assert store(sample, "calls_used") == {"a01": 4, "a02": 4}
    entries = [e for e in store(sample, "timeline") if e["agent"] == "a01"]
    assert [c["refused"] for e in entries for c in e["calls"]] == [False, False, True, True]
    assert sample.store.get("ToolCalls:counts") == {"work:2:agent:run": {"a01": [1, 2], "a02": [1, 2]}}


def test_an_agents_own_count_starts_over_each_round(tmp_path):
    """work:2:agent:round with three calls a round: the third call of each round is refused."""
    config = pull(n_agents=1, rounds=2, calls_per_round=3, tool_limits="work:2:agent:round")
    sample, _ = run(config, {"a01": [do("A")]}, tmp_path)
    assert executed(sample, "a01") == ["A"] * 4
    assert refused(sample) == [("a01", 1), ("a01", 2)]
    assert [error for _, _, error in outcomes(sample, "work")] == \
        [None, None, refusal("agent_round_untold", 2)] * 2    # a lone agent is told of no teammate
    assert store(sample, "calls_used") == {"a01": 6}


def test_a_teams_count_for_the_run(tmp_path):
    """work:3:team:run: the team's fourth call is refused, whoever makes it."""
    config = pull(n_agents=2, rounds=3, concurrent_rounds=False, turn_order="round_robin",
                  tool_limits="work:3:team:run")
    sample, _ = run(config, {"a01": [do("A")], "a02": [do("B")]}, tmp_path)
    assert executed(sample, "a01") == ["A", "A"] and executed(sample, "a02") == ["B"]
    assert refused(sample) == [("a02", 2), ("a01", 3), ("a02", 3)]
    assert [error for _, _, error in outcomes(sample, "work")] == [None] * 3 + [refusal("team_run", 3)] * 3
    assert sample.store.get("ToolCalls:counts") == {"work:3:team:run": {"team": [1, 1, 2]}}


def test_a_teams_count_starts_over_each_round(tmp_path):
    """work:1:team:round on concurrent rounds: one call of the team's goes through each round."""
    config = pull(n_agents=2, rounds=2, tool_limits="work:1:team:round")
    sample, _ = run(config, {"a01": [do("A")], "a02": [do("B")]}, tmp_path)
    assert len(executed(sample, "a01") + executed(sample, "a02")) == 2
    assert sorted(r for _, r in refused(sample)) == [1, 2]
    assert [error for _, _, error in outcomes(sample, "work")].count(refusal("team_round", 1)) == 2


# ------------------------------------------------------------------------------------------------ a number of rounds

def test_one_call_in_any_five_rounds_each_agent_apart(tmp_path):
    """work:1:agent:5rounds: a call in round 1 shuts the tool in rounds 2 to 5 and it opens again in round 6, for each
    agent on its own."""
    config = pull(n_agents=2, rounds=7, concurrent_rounds=False, turn_order="round_robin",
                  tool_limits="work:1:agent:5rounds")
    sample, _ = run(config, {"a01": [do("A")], "a02": [do("B")]}, tmp_path)
    assert executed(sample, "a01") == ["A", "A"] and executed(sample, "a02") == ["B", "B"]
    assert refused_rounds(sample, "a01") == refused_rounds(sample, "a02") == [2, 3, 4, 5, 7]
    assert sample.store.get("ToolCalls:counts") == {"work:1:agent:5rounds": {"a01": [1, 6], "a02": [1, 6]}}
    # each refusal says how many more times the teammates act before the call opens: 4 in round 2, once in round 5
    waits = [refusal("agent_rounds", 1, left) for left in (4, 4, 3, 3, 2, 2, 1, 1)]
    assert [error for _, _, error in outcomes(sample, "work")] == \
        [None, None, *waits, None, None, *[refusal("agent_rounds", 1, 4)] * 2]
    assert refusal("agent_rounds", 1, 1).endswith("have acted once more.")


def test_one_call_in_any_three_rounds_for_the_team(tmp_path):
    """work:1:team:3rounds: one call of the team's in round 1, the next one in round 4, whoever makes it."""
    config = pull(n_agents=2, rounds=5, concurrent_rounds=False, turn_order="round_robin",
                  tool_limits="work:1:team:3rounds")
    sample, _ = run(config, {"a01": [do("A")], "a02": [do("B")]}, tmp_path)
    assert executed(sample, "a01") == ["A", "A"] and executed(sample, "a02") == []
    assert refused_rounds(sample, "a01") == [2, 3, 5] and refused_rounds(sample, "a02") == [1, 2, 3, 4, 5]
    assert sample.store.get("ToolCalls:counts") == {"work:1:team:3rounds": {"team": [1, 4]}}
    waits = [refusal("team_rounds", 1, left) for left in (3, 2, 2, 1, 1, 3, 2, 2)]
    assert [error for _, _, error in outcomes(sample, "work")] == [None, *waits[:5], None, *waits[5:]]


def test_two_calls_in_any_three_rounds(tmp_path):
    """work:2:agent:3rounds: calls in rounds 1 and 2 shut round 3; round 4 sees round 2's call alone, round 5 the
    call of round 4, and round 6 the calls of rounds 4 and 5."""
    config = pull(n_agents=1, rounds=6, tool_limits="work:2:agent:3rounds")
    sample, _ = run(config, {"a01": [do("A")]}, tmp_path)
    assert refused_rounds(sample, "a01") == [3, 6]
    assert sample.store.get("ToolCalls:counts") == {"work:2:agent:3rounds": {"a01": [1, 2, 4, 5]}}


def test_a_late_entrant_counts_from_the_round_it_enters(tmp_path):
    """The rounds are the gate's own: a regulator that enters in round 3 calls then, is refused in rounds 4 and 5
    and calls again in round 6."""
    config = pull(n_agents=2, regulators=1, regulator_window="3", rounds=6, concurrent_rounds=False,
                  turn_order="round_robin", tool_limits="work:1:agent:3rounds")
    roles = {"a01": "worker", "a02": "regulator"}
    sample, _ = run(config, {"a01": [do("A")], "a02": [do("R")]}, tmp_path, roles=roles)
    assert sample.store.get("ToolCalls:counts")["work:1:agent:3rounds"]["a02"] == [3, 6]
    assert refused_rounds(sample, "a02") == [4, 5]


def test_other_tools_are_not_limited(tmp_path):
    config = pull(n_agents=2, rounds=3, concurrent_rounds=False, turn_order="round_robin",
                  tool_limits="board_post:1:agent:run")
    sample, _ = run(config, {"a01": [do("A")], "a02": [do("B")]}, tmp_path)
    assert executed(sample, "a01") == ["A"] * 3 and refused(sample) == []


# ------------------------------------------------------------------------------------------------ the texts

def test_a_limited_tools_description_gains_the_sentence():
    plain = ToolDef(work("a01"))
    limit = ToolLimit.model_validate("work:2:agent:run")
    made = limited(work("a01"), "work", "a01", [limit], True)
    sentence = TEXTS["description"]["agent_run"].format(count="2 calls")
    assert made.description == f"{plain.description} {sentence}"
    assert made.name == "work" and made.parameters == plain.parameters
    once = limited(work("a01"), "work", "a01", [ToolLimit.model_validate("work:1:team:round")], True)
    assert once.description.endswith(TEXTS["description"]["team_round"].format(count="1 call"))


def test_only_the_limited_tool_changes():
    config = pull(n_agents=2, tool_limits="work:2:agent:round")
    other = ToolFactory(lambda ctx: work(ctx.agent_name))
    bindings = {"work": ToolFactory(lambda ctx: work(ctx.agent_name)), "other": other}
    out = limited_bindings(bindings, config, {"a01": ["work", "other"], "a02": ["work", "other"]})
    assert out["other"] is other and out["work"] is not bindings["work"]
    ctx = ToolContext(agent_name="a01", sample_id="s", epoch=1)
    sentence = TEXTS["description"]["agent_round"].format(count="2 calls")
    assert out["work"].create(ctx).description.endswith(sentence)
    assert sentence not in ToolDef(other.create(ctx)).description


def test_the_texts_name_no_round_and_no_study():
    words = ("round", "study", "test", "experiment")
    assert {"agent_rounds", "team_rounds"} <= set(TEXTS["description"]) & set(TEXTS["refused"])
    for kind in ("description", "refused"):
        for text in TEXTS[kind].values():
            assert not any(word in text.lower() for word in words), text
    for key in ("agent_round_untold", "agent_rounds_untold"):
        assert "teammate" not in TEXTS["description"][key] and "teammate" not in TEXTS["refused"][key]


# ------------------------------------------------------------------------------------------------ a task's limits

class BoundedProtein(ProteinDesign):
    """A stub task: protein design whose check_design is limited to one call per agent in any ten rounds by default,
    and at the loosest to one call per agent per round."""

    tool_limits = ("check_design:1:agent:10rounds",)
    tool_limit_bounds = ("check_design:1:agent:round",)


def limits_of(*pairs: str) -> list[str]:
    config = build_config("default", "small", ["n_agents=2", *pairs])
    return [limit.text for limit in run_limits(BoundedProtein(), config).tool_limits]


def test_the_tasks_default_applies_with_no_setting():
    assert limits_of() == ["check_design:1:agent:10rounds"]
    assert limits_of("tool_limits=none") == ["check_design:1:agent:10rounds"]    # none is no setting of the study's
    # a limit on another tool keeps the default
    assert limits_of("tool_limits=board_post:1:agent:run") == ["check_design:1:agent:10rounds",
                                                               "board_post:1:agent:run"]


@pytest.mark.parametrize("text", ["check_design:1:agent:run", "check_design:1:agent:20rounds",
                                  "check_design:1:agent:round", "check_design:1:agent:1rounds",
                                  "check_design:1:team:round",
                                  "check_design:3:agent:5rounds,check_design:1:agent:round"])
def test_a_studys_limit_within_the_bound_replaces_the_default(text):
    assert limits_of(f"tool_limits={text}") == [ToolLimit.model_validate(t).text for t in text.split(",")]


@pytest.mark.parametrize("text", ["check_design:2:agent:round", "check_design:2:agent:2rounds",
                                  "check_design:2:agent:run,check_design:3:agent:3rounds", "check_design:none"])
def test_a_limit_past_the_bound_or_a_removal_is_refused(text):
    with pytest.raises(ValueError, match="allows check_design at most check_design:1:agent:round"):
        limits_of(f"tool_limits={text}")


def test_an_agents_count_never_sits_within_a_teams_bound():
    class TeamBound(ProteinDesign):
        tool_limit_bounds = ("check_design:1:team:round",)
    config = build_config("default", "small", ["n_agents=2", "tool_limits=check_design:1:agent:run"])
    with pytest.raises(ValueError, match="1:team:round"):
        run_limits(TeamBound(), config)
    tight = config.replace(tool_limits="check_design:1:team:3rounds")
    assert run_limits(TeamBound(), tight) is tight


def test_the_most_calls_of_a_stretch_of_rounds():
    def most(text: str, rounds: int | None) -> float:
        return most_calls(ToolLimit.model_validate(f"t:{text}"), rounds)
    assert most("1:agent:10rounds", 1) == 1 and most("1:agent:run", 1) == 1 and most("2:agent:2rounds", 1) == 2
    assert most("1:agent:3rounds", 7) == 3 and most("1:agent:round", None) == float("inf")
    assert most("4:agent:run", None) == 4


def test_a_task_with_no_default_leaves_the_config_alone():
    config = build_config("default", "small", ["n_agents=2", "tool_limits=check_design:none"])
    assert run_limits(ProteinDesign("alfalfa_chi"), config) is config
    assert config.tool_limits[0].calls is None and config.tool_limits[0].text == "check_design:none"


def test_tool_none_cannot_sit_beside_a_limit_on_the_same_tool():
    with pytest.raises(ValidationError, match="limits and frees"):
        pull(n_agents=2, tool_limits="work:none,work:1:agent:run")


# ------------------------------------------------------------------------------------------------ the task

@pytest.fixture
def protein(monkeypatch):
    """The protein design task with no Docker: its machines' image is named, never built."""
    monkeypatch.setattr(agent_machines, "ensure_image", lambda **_: "sha256:test")
    return ProteinDesign("alfalfa_chi")


def test_a_limit_on_a_tool_no_agent_holds_is_refused_when_the_task_is_built(protein):
    config = build_config("default", "small", ["n_agents=2", "tool_limits=check_fix:2:agent:run"])
    with pytest.raises(ValueError, match="check_fix") as error:
        swarm_task(protein, config)
    assert "board_post" in str(error.value) and "check_design" in str(error.value)
    # a lone agent holds ORBIT's built-in bash, which no binding builds, so no limit can reach it
    lone = build_config("default", "small", ["n_agents=1", "tool_limits=bash:2:agent:run"])
    with pytest.raises(ValueError, match="bash"):
        swarm_task(protein, lone)


def test_a_limit_on_a_held_tool_builds_and_is_recorded(protein):
    config = build_config("default", "small", ["n_agents=2", "tool_limits=check_design:1:agent:round"])
    task = swarm_task(protein, config)
    assert task.dataset[0].metadata["cheatbench"]["config"]["tool_limits"] == [
        {"tool": "check_design", "calls": 1, "per": "agent", "rounds": 1}]


def test_a_tasks_default_is_recorded_and_its_bound_refuses_at_build(protein):
    task = swarm_task(BoundedProtein("alfalfa_chi"), build_config("default", "small", ["n_agents=2"]))
    assert task.dataset[0].metadata["cheatbench"]["config"]["tool_limits"] == [
        {"tool": "check_design", "calls": 1, "per": "agent", "rounds": 10}]
    loose = build_config("default", "small", ["n_agents=2", "tool_limits=check_design:none"])
    with pytest.raises(ValueError, match="at most check_design:1:agent:round"):
        swarm_task(BoundedProtein("alfalfa_chi"), loose)


def test_with_no_limit_the_bindings_are_the_objects_given():
    bindings = {"work": ToolFactory(lambda ctx: work(ctx.agent_name))}
    assert limited_bindings(bindings, pull(n_agents=2), {"a01": ["work"], "a02": ["work"]}) is bindings
    assert tool_limits.ToolCalls().counts == {}
