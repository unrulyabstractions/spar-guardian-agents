"""SwarmConfig (src/configuration/swarm_config.py): validation, the modes, role shares, windows, names and the
instruction levels it accepts."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.configuration import settings
from src.configuration.presets import PRESETS, build_config, with_overrides
from src.configuration.swarm_config import SwarmConfig, Window, agent_names, role_count
from src.domains import DOMAINS as REGISTRY

DOMAINS = sorted(REGISTRY)      # instruction_levels.yaml's benchmark keys are the registry's


def test_defaults_and_properties():
    c = SwarmConfig()
    assert (c.n_agents, c.shutdown, c.turn_order) == (1, True, "random")
    assert (c.mention_board, c.mention_team, c.notice, c.concurrent_rounds) == (False, None, "counts", True)
    assert (c.informed_roles, c.submit, c.batch_judge, c.mode, c.scale) == (True, None, True, "shared_task", None)
    assert c.submit_delay_rounds == 2
    assert (c.rule_level, c.regulator_level, c.disregulator_level) == ("none", "ambiguous", "persuade")
    assert (c.regulator_discloses, c.access, c.free_board_actions_regulator) == (False, "split", 10)
    assert (c.reads_per_turn, c.posts_per_turn, c.turn_budget) == (None, None, None)
    assert (c.max_message_chars, c.message_limit, c.working_steps) == (4000, None, 60)
    assert not c.board and c.names == ["a01"] and c.max_turns == 60 * 4
    c = SwarmConfig(n_agents=10, regulators=3, disregulators=2, steps_per_agent=5, free_board_actions=0)
    # Seven agents take one step a round; the three regulators also take up to ten follow-ups (their own K).
    assert c.board and c.names[-1] == "a10" and c.max_turns == 5 * (7 * 1 + 3 * 11)
    assert with_overrides(c, ["free_board_actions_regulator=0"]).max_turns == 50   # K 0 for every agent
    assert c.judge == settings.JUDGE_MODEL


@pytest.mark.parametrize("fields", [
    {"n_agents": 0},
    {"n_agents": 2, "regulators": 2, "disregulators": 1},
    {"regulators": -1},
    {"steps_per_agent": 0},
    {"rule_level": "loud"},
    {"regulator_level": "precise"},
    {"disregulator_level": "role"},
    {"n_agents": 4, "regulators": 1.5},
    {"n_agents": 4, "regulators": 0.0},
    {"n_agents": 4, "disregulators": 1.0},
    {"n_agents": 2, "regulators": 0.5, "disregulators": 0.9},
    {"regulator_window": {"start": 0}},
    {"steps_per_agent": 5, "regulator_window": {"start": 4, "end": 3}},
    {"steps_per_agent": 5, "disregulator_window": {"start": 1, "end": 6}},
    {"turn_order": "alphabetical"},
    {"unknown_field": 1},
    {"mode": "pairs"},
    {"mode": {"openmath": "solo_task"}},
    {"access": "swe:split,openmath:all"},
    {"scale": "huge"},
    {"notice": "loud"},
    {"read_page": 0},
    {"max_concurrency": 0},
    {"free_board_actions": -1},
    {"free_board_actions_regulator": -1},
    {"reads_per_turn": 0},
    {"posts_per_turn": 0},
    {"turn_budget": 0},
    {"turn_budget": {"openmath": 70}},
    {"turn_budget": "knowledge_work:70"},
    {"message_limit": 0},
    {"regulator_level": "loud"},
    {"submit_delay_rounds": -1},
    {"stop_after_delivery": -1},
    {"stop_after_delivery": "soon"},
])
def test_invalid(fields):
    with pytest.raises(ValidationError):
        SwarmConfig(**fields)


def test_frozen():
    with pytest.raises(ValidationError):
        SwarmConfig().n_agents = 3


def test_every_field_is_documented():
    for name, field in SwarmConfig.model_fields.items():
        assert field.description, name


def test_role_shares_round_half_up():
    assert [role_count(share, 10) for share in (0.04, 0.15, 0.25, 0.35, 0.45)] == [1, 2, 3, 4, 5]
    assert role_count(3, 10) == 3 and role_count(0, 10) == 0
    c = SwarmConfig(n_agents=10, regulators=0.3, disregulators=0.2)
    assert (c.regulator_count, c.disregulator_count) == (3, 2)


def test_windows():
    assert Window.model_validate("3:5") == Window(start=3, end=5)
    assert Window.model_validate("3:") == Window(start=3) == Window.model_validate("3")
    w = Window(start=2, end=3)
    assert [r for r in range(1, 6) if w.contains(r)] == [2, 3]
    c = SwarmConfig(n_agents=3, regulators=1, steps_per_agent=5, regulator_window=Window(start=2, end=5))
    assert c.window("regulator") == Window(start=2, end=5)
    assert c.window("worker") == c.window("disregulator") == Window()


def test_names_width():
    assert agent_names(1) == ["a01"] and agent_names(3) == ["a01", "a02", "a03"] and agent_names(99)[-1] == "a99"
    assert agent_names(100)[0] == "a001" and agent_names(100)[-1] == "a100" and agent_names(999)[-1] == "a999"
    assert agent_names(1000)[0] == "a0001" and agent_names(1000)[-1] == "a1000"
    for n in (3, 10, 100, 500, 1000):
        names = agent_names(n)
        assert len(set(names)) == n and len({len(name) for name in names}) == 1


def test_windows_are_checked_against_the_rounds_the_budget_gives():
    # Ninety rounds fit the budget's 100.
    late = build_config("default", "small", ["regulators=1", "regulator_window=1:90"])
    assert late.regulator_window.end == 90
    with pytest.raises(ValidationError, match="the rounds"):
        build_config("default", "small", ["regulators=1", "regulator_window=160:"])
    with pytest.raises(ValidationError, match="the rounds"):
        build_config("default", "small", ["regulators=1", "turn_budget=20", "regulator_window=25:"])
    # Without a budget the rounds are steps_per_agent.
    with pytest.raises(ValidationError, match="the rounds"):
        SwarmConfig(n_agents=3, regulators=1, steps_per_agent=5, regulator_window="1:6")


def test_the_mode_sets_what_the_team_is_told_and_who_ends_how():
    c = PRESETS["default"]
    # shared_task is the default mode: neutral agents hand in the team's work, and every agent may stop.
    assert c.mode == "shared_task" and c.knows_team
    assert (c.gives_submit, c.gives_stop_working, c.submit_kind) == (True, True, "team")
    assert c.holds_submit("worker") and not c.holds_submit("regulator") and not c.holds_submit("disregulator")
    solo = with_overrides(c, ["mode=solo_task"])
    assert not solo.knows_team and (solo.gives_submit, solo.gives_stop_working, solo.submit_kind) == (True, False,
                                                                                                    "solo")
    assert not with_overrides(c, ["mention_team=false"]).knows_team
    assert with_overrides(solo, ["mention_team=true"]).knows_team
    off = with_overrides(c, ["submit=false", "shutdown=false"])
    assert not off.gives_submit and not off.holds_submit("worker") and not off.gives_stop_working
    assert with_overrides(c, ["submit=true"]).gives_submit
    lone = with_overrides(c, ["n_agents=1"])
    assert not lone.knows_team and (lone.gives_submit, lone.gives_stop_working, lone.submit_kind) == (True, False,
                                                                                                    "lone")


def test_instruction_levels_match_the_ladders():
    levels = settings.load_instruction_levels()
    for ladder in ("rule", "regulator", "disregulator"):
        for level in levels[ladder]["order"]:
            SwarmConfig(**{f"{ladder}_level": level})
    assert levels["regulator"]["order"] == ["none", "monitor", "role", "enforce", "ambiguous", "explicit", "pressure"]
    assert SwarmConfig().regulator_level == "ambiguous"
    assert SwarmConfig().disregulator_level == "persuade"
    assert sorted(levels["vars"]) == DOMAINS
    assert all(set(levels["vars"][d]) == set(levels["vars"]["openmath"]) for d in DOMAINS)
