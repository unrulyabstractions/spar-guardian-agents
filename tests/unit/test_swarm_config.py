"""SwarmConfig (src/configuration/swarm_config.py): validation, the modes, role shares, windows, names and the
instruction levels it accepts."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.configuration import settings
from src.configuration.presets import PRESETS, build_config, with_overrides
from src.configuration.swarm_config import SwarmConfig, Window, role_count
from src.domains import DOMAINS as REGISTRY

DOMAINS = sorted(REGISTRY)      # instruction_levels.yaml's benchmark keys are the registry's


def test_defaults_and_properties():
    c = SwarmConfig()
    assert (c.n_agents, c.shutdown, c.turn_order) == (1, True, "random")
    assert (c.mention_board, c.mention_team, c.notice, c.concurrent_rounds) == (True, None, "counts", True)
    assert (c.informed_roles, c.submit, c.batch_judge, c.mode, c.scale) == (True, None, True, "shared_task", None)
    assert c.submit_delay_rounds == 3 and c.unsubmit is True
    assert (c.rule_level, c.regulator_level, c.disregulator_level) == ("none", "generic", "instructed")
    assert (c.regulator_discloses, c.access) == (False, "split")
    assert (c.reads_per_turn, c.posts_per_turn, c.rounds, c.calls_per_round) == (None, None, 30, 10)
    # The plan holds C = R x N rounds, a lone agent's one step each.
    assert (c.max_message_chars, c.message_limit, c.rounds, c.call_budget) == (4000, None, 30, 300)
    assert not c.board and c.names == ["a01"] and c.max_turns == 300
    assert c.sample_message_limit == 3 * 300 + 300 + 2 + 20
    c = SwarmConfig(n_agents=10, regulators=3, disregulators=2, rounds=5, calls_per_round=4)
    # On a board too the plan holds C rounds, influencers' as workers', one step per agent each.
    assert c.board and c.names[-1] == "a10" and (c.rounds, c.call_budget, c.max_turns) == (5, 20, 200)
    # three messages a call of C, one round's message a round of the plan, each agent's system and first message
    assert c.sample_message_limit == 10 * (3 * 20 + 20 + 2) + 20
    assert c.judge == settings.JUDGE_MODEL


@pytest.mark.parametrize("fields", [
    {"n_agents": 0},
    {"n_agents": 2, "regulators": 2, "disregulators": 1},
    {"regulators": -1},
    {"rounds": 0},
    {"calls_per_round": 0},
    {"steps_per_agent": 8},
    {"free_board_actions": 3},
    {"pooled_steps": True},
    {"rule_level": "loud"},
    {"regulator_level": "precise"},
    {"disregulator_level": "role"},
    {"n_agents": 4, "regulators": 1.5},
    {"n_agents": 4, "regulators": 0.0},
    {"n_agents": 4, "disregulators": 1.0},
    {"n_agents": 2, "regulators": 0.5, "disregulators": 0.9},
    {"regulator_window": {"start": 0}},
    {"rounds": 5, "regulator_window": {"start": 4, "end": 3}},
    {"rounds": 5, "calls_per_round": 1, "disregulator_window": {"start": 1, "end": 6}},
    {"turn_order": "alphabetical"},
    {"unknown_field": 1},
    {"mode": "pairs"},
    {"mode": {"protein_design": "solo_task"}},
    {"access": "swe:split,protein_design:all"},
    {"scale": "huge"},
    {"notice": "loud"},
    {"read_page": 0},
    {"max_concurrency": 0},
    {"reads_per_turn": 0},
    {"posts_per_turn": 0},
    {"turn_budget": 100},
    {"rounds": {"protein_design": 70}},
    {"rounds": "creative_writing:70"},
    {"message_limit": 0},
    {"regulator_level": "loud"},
    {"submit_delay_rounds": -1},
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


def test_the_access_log_is_on_unless_a_run_turns_it_off():
    assert SwarmConfig().access_log is True
    assert with_overrides(PRESETS["default"], ["access_log=false"]).access_log is False


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
    c = SwarmConfig(n_agents=3, regulators=1, rounds=5, regulator_window=Window(start=2, end=5))
    assert c.window("regulator") == Window(start=2, end=5)
    assert c.window("worker") == c.window("disregulator") == Window()


def test_windows_are_checked_against_the_plans_rounds():
    # The plan holds C = R x N rounds: three hundred fit the default's, three hundred and one do not.
    late = build_config("default", "small", ["regulators=1", "regulator_window=1:300"])
    assert late.regulator_window.end == 300
    with pytest.raises(ValidationError, match="the plan's rounds"):
        build_config("default", "small", ["regulators=1", "regulator_window=301:"])
    with pytest.raises(ValidationError, match="the plan's rounds"):
        build_config("default", "small", ["regulators=1", "rounds=10", "regulator_window=101:"])
    with pytest.raises(ValidationError, match="the plan's rounds"):
        SwarmConfig(n_agents=3, regulators=1, rounds=5, calls_per_round=2, regulator_window="1:11")


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
    assert levels["regulator"]["order"] == levels["disregulator"]["order"] == ["none", "generic", "instructed"]
    assert SwarmConfig().regulator_level == "generic"
    assert SwarmConfig().disregulator_level == "instructed"
    for ladder, old in (("regulator", "ambiguous"), ("disregulator", "persuade")):
        with pytest.raises(ValueError, match="is not one of"):
            SwarmConfig(**{f"{ladder}_level": old})
    assert sorted(levels["vars"]) == DOMAINS
    assert all(set(levels["vars"][d]) == set(levels["vars"]["swe"]) for d in DOMAINS)
