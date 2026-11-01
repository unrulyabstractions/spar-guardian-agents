"""deal_replay: today's code deals a recorded run again and says whether the roster and every agent's holdings match.
Each case records a run.json from a plan, the way the export records the roster and the access block, then changes
it."""
from __future__ import annotations

import pytest

from src import tasks
from src.configuration.presets import build_config
from src.domains import BY_TASK
from src.measures.deal_replay import Replay, config_of, replay
from tests.support.ordered_ids import drawn_ids

ROWS = {"protein_design": ("protein", {"row_id": "alfalfa_chi"}),
        "creative_writings": ("creative", {"row_id": "museum_poem"}),
        "software_engineering": ("swe", {"issue": "flipt-02e2", "honeypot": "jigsaw"})}


def recorded(task: str, *pairs: str) -> dict:
    """run.json as a run of today's code records it: the config, the roster and each agent's holdings."""
    block, row = ROWS[task]
    config = build_config("default", "small", list(pairs))
    plan = BY_TASK[task](row.get("row_id") or row["issue"]).plan(config, tasks.hidden_roles(config))
    return {"task": task, block: row, "config": config.model_dump(mode="json"), "agents": plan.agents,
            "access": {"policy": plan.access, "agents": {a: {"holds": items} for a, items in plan.holds.items()}}}


@pytest.mark.parametrize("task", sorted(ROWS))
def test_a_run_of_todays_code_replays_the_same_deal(task):
    with drawn_ids():
        run = recorded(task, "seed=2", "id_salt=binder_minimal_luna2")
        assert run["agents"] != ["a01", "a02", "a03"]
        assert replay(run) == Replay("same")


@pytest.mark.parametrize("task", sorted(ROWS))
def test_a_regulator_holds_nothing_in_the_replay_as_in_the_run(task):
    with drawn_ids():
        run = recorded(task, "n_agents=4", "regulators=1", "seed=3", "id_salt=flipt_minimal_regulated_luna_luna1")
        assert sorted(len(entry["holds"]) == 0 for entry in run["access"]["agents"].values()) == [False] * 3 + [True]
        assert replay(run) == Replay("same")


def test_swapped_holdings_differ_and_name_both_agents():
    run = recorded("protein_design", "seed=1")
    first, second = run["agents"][:2]
    holds = run["access"]["agents"]
    holds[first]["holds"], holds[second]["holds"] = holds[second]["holds"], holds[first]["holds"]
    result = replay(run)
    assert result.status == "differs" and result.detail.startswith("holdings: ")
    assert f"{first} recorded" in result.detail and f"{second} recorded" in result.detail


def test_a_roster_todays_draw_does_not_give_differs():
    """A run of the first screen kept the names a01 to a03, which today's draw never gives."""
    run = recorded("creative_writings", "seed=1")
    with drawn_ids():
        result = replay(run)
    assert result.status == "differs" and result.detail.startswith("roster: recorded a01, a02, a03; today ")


def test_a_config_todays_plan_refuses_is_refused():
    run = recorded("software_engineering", "seed=1")
    run["config"]["mode"] = "solo_task"
    result = replay(run)
    assert result.status == "refused" and "jigsaw honeypot needs a team in shared_task" in result.detail


def test_the_config_takes_the_preset_and_scale_then_the_fields_that_fix_the_deal():
    run = recorded("protein_design", "n_agents=4", "regulators=1", "seed=5", "id_salt=x5")
    run["config"]["rule_level"] = "identified"          # no field of the deal: the preset's stands
    config = config_of(run)
    assert (config.n_agents, config.regulators, config.seed, config.id_salt, config.access, config.mode) == (
        4, 1, 5, "x5", "split", "shared_task")
    assert config.rule_level == build_config("default", "small").rule_level


def test_a_task_the_replay_does_not_cover_gives_none():
    assert replay({"task": "chess", "config": {}}) is None
