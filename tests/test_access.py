"""The access policy (src/configuration/access.py): the text form, the deal, the config field, each domain's items
and plan under split, split_copy, all and an explicit map, and the record every task carries."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.benchmark.domains.knowledge import KnowledgeWork
from src.benchmark.domains.knowledge.sandbox import is_split, split_items
from src.benchmark.domains.openmath import OpenMath
from src.benchmark.domains.openmath.sandbox import archive_parts, solo_setup_script, split_layout, split_setup_script
from src.benchmark.domains.openmath.task import SPLIT_DEVIATIONS, layout_of, load_rows
from src.benchmark.domains.swe import SWE
from src.benchmark.domains.swe.task import build_swe, part_mates, parts_by_agent, pieces_by_agent
from src.configuration import access
from src.configuration.swarm_config import PRESETS, SwarmConfig, agent_names, build_config
from src.run import access_line
from src.swarm.roles import assign_roles
from src.tasks import hidden_roles, swarm_task

THREE = agent_names(3)
FOUR = agent_names(4)


def test_text_form_round_trips():
    assert access.parse("split") == "split" and access.parse(" all ") == "all"
    assert access.parse("split_copy") == "split_copy" and access.policy_name("split_copy") == "split_copy"
    explicit = {"a01": ["B", "C"], "a02": ["A"], "a03": []}
    assert access.parse("a01:B+C,a02:A,a03:") == explicit and access.parse(explicit) is explicit
    assert access.text(explicit) == "a01:B+C,a02:A,a03:" and access.text("split") == "split"
    assert access.policy_name(explicit) == "explicit" and access.policy_name("all") == "all"
    with pytest.raises(ValueError, match="agent:item"):
        access.parse("a01")
    with pytest.raises(ValueError, match="agent:item"):
        access.parse("none")
    with pytest.raises(ValueError, match="twice"):
        access.parse("a01:x,a01:y")


def test_the_deal_is_round_robin_over_the_neutral_agents():
    assert access.deal(["x", "y", "z"], ["a01", "a02"]) == {"a01": ["x", "z"], "a02": ["y"]}
    assert access.deal(["x"], THREE) == {"a01": ["x"], "a02": [], "a03": []}
    assert access.deal(["x"], []) == {}
    roles = {"a01": "worker", "a02": "regulator", "a03": "worker"}
    assert access.neutral_agents(THREE, roles) == ["a01", "a03"] and access.neutral_agents(THREE, None) == THREE


def test_plan_under_each_policy():
    items = ["x", "y"]
    whole = ["w"]
    roles = {"a01": "worker", "a02": "regulator", "a03": "worker"}
    # split: the items over the neutral agents, nothing for the regulator
    assert access.plan("split", THREE, roles, lambda n: items[:n], whole) == \
        {"a01": ["x"], "a02": [], "a03": ["y"]}
    # split_copy: the same deal, and the regulator a copy of the first dealt item
    assert access.plan("split_copy", THREE, roles, lambda n: items[:n], whole) == \
        {"a01": ["x"], "a02": ["x"], "a03": ["y"]}
    two_roles = {"a01": "regulator", "a02": "worker", "a03": "worker", "a04": "disregulator"}
    assert access.plan("split_copy", FOUR, two_roles, lambda n: items[:n], whole) == \
        {"a01": ["x"], "a02": ["x"], "a03": ["y"], "a04": ["y"]}
    assert access.plan("split", FOUR, two_roles, lambda n: items[:n], whole) == \
        {"a01": [], "a02": ["x"], "a03": ["y"], "a04": []}
    assert access.roles_hold("split_copy", [], ["a01"]) == {"a01": []}
    assert access.plan("all", THREE, roles, lambda n: items, whole) == {a: ["w"] for a in THREE}
    # fewer than two neutral agents: everyone holds everything
    alone = {"a01": "worker", "a02": "regulator", "a03": "disregulator"}
    assert access.plan("split", THREE, alone, lambda n: items, whole) == {a: ["w"] for a in THREE}
    assert access.plan("split_copy", THREE, alone, lambda n: items, whole) == {a: ["w"] for a in THREE}
    explicit = {"a01": ["x", "y"], "a02": [], "a03": ["w"]}
    assert access.plan(explicit, THREE, roles, lambda n: items, whole) == explicit
    with pytest.raises(ValueError, match="every agent"):
        access.check_map({"a01": ["x"]}, THREE, lambda i: True)
    with pytest.raises(ValueError, match="unknown item"):
        access.check_map({"a01": ["q"], "a02": [], "a03": []}, THREE, lambda i: i == "x")
    with pytest.raises(ValueError, match="twice"):
        access.check_map({"a01": ["x", "x"], "a02": [], "a03": []}, THREE, lambda i: True)
    with pytest.raises(ValueError, match="whole and more"):
        access.check_map({"a01": ["whole", "x"], "a02": [], "a03": []}, THREE, lambda i: True)
    access.check_map("split", THREE, lambda i: False)


def test_the_config_field_parses_the_command_line_and_names_the_roster():
    assert SwarmConfig().access == "split" and PRESETS["default"].access == "split"
    by_domain = SwarmConfig(access={"openmath": "all", "swe": "split", "knowledge_work": "all"})
    assert by_domain.resolve_access("openmath_agent") == "all" and by_domain.resolve_access("swe") == "split"
    config = build_config("default", "small", ["access=a01:slice1+slice2,a02:slice3,a03:"])
    assert config.access == {"a01": ["slice1", "slice2"], "a02": ["slice3"], "a03": []}
    assert config.resolve_access("openmath") == config.access
    assert build_config("default", "small", ["access=all"]).access == "all"
    assert build_config("default", "small", ["access=split_copy"]).access == "split_copy"
    assert build_config("default", "small", ["access=swe:split_copy"]).resolve_access("swe") == "split_copy"
    by_domain = build_config("default", "small", ["access=swe:all,openmath:split"])
    assert by_domain.access == {"swe": "all", "openmath": "split"}
    assert [by_domain.resolve_access(d) for d in ("swe", "openmath", "knowledge_work")] == ["all", "split", "split"]
    assert build_config("default", "small", ["access=openmath_agent:all"]).access == {"openmath": "all"}
    with pytest.raises(ValidationError, match="keys are domains"):
        build_config("default", "small", ["access=chess:all"])
    with pytest.raises(ValidationError, match="empty"):
        SwarmConfig(access={})
    with pytest.raises(ValidationError, match="every agent of the roster"):
        build_config("default", "small", ["access=a01:slice1,a02:slice2"])
    with pytest.raises(ValidationError, match="every agent of the roster"):
        build_config("default", "small", ["access=a01:,a02:,a03:,a04:"])
    with pytest.raises(ValidationError):
        build_config("default", "small", ["access=swe:sometimes"])
    with pytest.raises(ValidationError, match="agent:item"):
        build_config("default", "small", ["access=none"])


def test_openmath_slices_follow_the_plan():
    bench = OpenMath()
    config = build_config("default", "small", ["regulators=1", "seed=2"])
    roles = hidden_roles(bench, config)
    neutral = [a for a in THREE if roles[a] == "worker"]
    regulator = next(a for a in THREE if roles[a] == "regulator")
    plan = bench.access_plan(THREE, config, roles)
    assert plan == {neutral[0]: ["slice1"], neutral[1]: ["slice2"], regulator: []}
    assert archive_parts(plan) == {neutral[0]: [1], neutral[1]: [2], regulator: []}
    row = next(r for r in load_rows() if r["id"] == "riemann")
    layout = split_layout(THREE, row, plan)
    assert layout["layout"] == "split" and layout["archive"]["parts"] == 2
    assert layout["archive_parts"] == archive_parts(plan)
    assert layout["agents"][regulator]["slices"] == [] and layout["agents"][neutral[0]]["slices"] == [1]
    script = split_setup_script(layout)
    assert script.count("update-index --force-remove Riemann.zip") == 3    # the regulator's copy loses it too
    assert script.count("Riemann.zip.part1") == 1 and script.count("Riemann.zip.part2") == 1
    # both per-agent layouts end by closing the temporary directories and proving it between the first two agents
    for text in (script, solo_setup_script(THREE)):
        assert "chmod 1733 /tmp /var/tmp /dev/shm" in text and "umask 077" in text, text
        assert 'runuser -u a01 -- bash -lc "umask > $probe.umask' in text, text
        assert 'if runuser -u a02 -- bash -lc "cat $probe 2>/dev/null || ls /tmp 2>/dev/null"; then' in text, text
        assert text.index("chmod 1733") > text.index("chmod 700 /workspace/a03"), text
    # split_copy: the regulator holds a copy of slice1, so two copies carry part1
    copy = build_config("default", "small", ["regulators=1", "seed=2", "access=split_copy"])
    plan = bench.access_plan(THREE, copy, roles)
    assert plan == {neutral[0]: ["slice1"], neutral[1]: ["slice2"], regulator: ["slice1"]}
    layout = split_layout(THREE, row, plan)
    assert layout["archive"]["parts"] == 2 and layout["agents"][regulator]["slices"] == [1]
    script = split_setup_script(layout)
    assert script.count("Riemann.zip.part1") == 2 and script.count("Riemann.zip.part2") == 1
    # An explicit map: one agent holds two slices of three, one holds the third, one holds none.
    explicit = build_config("default", "small", ["access=a01:slice1+slice3,a02:slice2,a03:"])
    plan = bench.access_plan(THREE, explicit, None)
    assert plan == explicit.access and archive_parts(plan) == {"a01": [1, 3], "a02": [2], "a03": []}
    layout = split_layout(THREE, row, plan)
    assert layout["archive"]["parts"] == 3
    script = split_setup_script(layout)
    assert script.count("update-index --force-remove Riemann.zip") == 3    # every copy loses the archive
    assert script.count("Riemann.zip.part1") == 1 and script.count("Riemann.zip.part3") == 1
    assert layout_of(THREE, explicit) == "split" and layout_of(THREE, build_config("default", "small",
                                                                                  ["access=all"])) is None
    with pytest.raises(ValueError, match="unknown item"):
        bench.check(build_config("default", "small", ["access=a01:B,a02:,a03:"]))
    with pytest.raises(ValueError, match="slices 1..N"):
        bench.check(build_config("default", "small", ["access=a01:slice1,a02:slice3,a03:"]))
    with pytest.raises(ValueError, match="slices 1..N"):
        bench.check(build_config("default", "small", ["access=a01:slice1,a02:,a03:"]))
    bench.check(build_config("default", "small", ["access=a01:,a02:,a03:whole"]))
    assert bench.access_plan(["a01"], build_config("default", "small", ["n_agents=1"]), None) == {"a01": ["whole"]}
    assert bench.access_plan(THREE, build_config("default", "small", ["mode=solo_task"]), None) == \
        {a: ["whole"] for a in THREE}


def test_the_applied_policy_is_the_one_recorded():
    bench = OpenMath()
    solo = swarm_task(bench, build_config("default", "small", ["mode=solo_task"]))
    assert solo.dataset[0].metadata["cheatbench"]["access"] == {"policy": "all",
                                                                "agents": {a: ["whole"] for a in THREE}}
    lone = swarm_task(bench, build_config("default", "small", ["n_agents=1"]))
    assert lone.dataset[0].metadata["cheatbench"]["access"] == {"policy": "all", "agents": {"a01": ["whole"]}}
    one_neutral = build_config("default", "small", ["regulators=1", "disregulators=1"])
    assert bench.applied_access(THREE, one_neutral, hidden_roles(bench, one_neutral)) == "all"
    one_neutral = build_config("default", "small", ["regulators=1", "disregulators=1", "access=split_copy"])
    assert bench.applied_access(THREE, one_neutral, hidden_roles(bench, one_neutral)) == "all"
    for pairs in (["mode=solo_task", "access=a01:slice1,a02:slice2,a03:"], ["n_agents=1", "access=a01:whole"]):
        with pytest.raises(ValueError, match="cannot apply"):
            bench.check(build_config("default", "small", pairs))
    own = SWE("flipt-02e2", "own_piece")
    collab = build_config("default", "small", ["mode=collaboration_needed"])
    assert own.applied_access(THREE, collab, None) == "split"
    assert own.access_plan(THREE, collab, None) == {"a01": ["A"], "a02": ["B"], "a03": ["C"]}
    with pytest.raises(ValueError, match="by part"):
        own.check(build_config("default", "small", ["mode=collaboration_needed", "access=all"]))
    own.check(build_config("default", "small", ["mode=collaboration_needed", "access=split_copy"]))
    # own_piece with a regulator: the parts go to the two neutral agents (A; B and C), the regulator owns the first
    # neutral agent's part and holds nothing, or that part's piece under split_copy
    with_role = build_config("default", "small", ["mode=collaboration_needed", "regulators=1", "seed=2"])
    roles = hidden_roles(own, with_role)
    neutral = [a for a in THREE if roles[a] == "worker"]
    regulator = next(a for a in THREE if roles[a] == "regulator")
    assert parts_by_agent(THREE, "collaboration_needed", roles) == \
        {neutral[0]: ["A"], neutral[1]: ["B", "C"], regulator: ["A"]}
    assert part_mates(parts_by_agent(THREE, "collaboration_needed", roles)) == \
        {neutral[0]: [regulator], regulator: [neutral[0]]}
    assert own.applied_access(THREE, with_role, roles) == "split"
    assert own.access_plan(THREE, with_role, roles) == {neutral[0]: ["A"], neutral[1]: ["B", "C"], regulator: []}
    copy = build_config("default", "small", ["mode=collaboration_needed", "regulators=1", "seed=2",
                                             "access=split_copy"])
    assert own.applied_access(THREE, copy, roles) == "split_copy"
    assert own.access_plan(THREE, copy, roles) == {neutral[0]: ["A"], neutral[1]: ["B", "C"], regulator: ["A"]}
    swe = build_swe("flipt-02e2", "own_piece", THREE, with_role, roles=roles)
    assert swe["pieces"] == own.access_plan(THREE, with_role, roles) and swe["shares"] == part_mates(
        parts_by_agent(THREE, "collaboration_needed", roles))
    # one neutral agent among three: the whole issue and the whole fix for everyone
    two_roles = build_config("default", "small", ["mode=collaboration_needed", "regulators=1", "disregulators=1"])
    roles = hidden_roles(own, two_roles)
    assert parts_by_agent(THREE, "collaboration_needed", roles) == {a: [] for a in THREE}
    assert own.access_plan(THREE, two_roles, roles) == {a: ["whole"] for a in THREE}
    # without roles the parts are as before: four agents take A, B, C, A
    assert parts_by_agent(FOUR, "collaboration_needed") == {"a01": ["A"], "a02": ["B"], "a03": ["C"], "a04": ["A"]}
    assert SWE("flipt-02e2", "jigsaw").applied_access(THREE, build_config("default", "small", ["access=all"]),
                                                     None) == "all"


def test_swe_pieces_follow_the_plan():
    assert pieces_by_agent(THREE, "jigsaw", "shared_task", None, "all") == {a: ["whole"] for a in THREE}
    roles = {"a01": "worker", "a02": "regulator", "a03": "worker"}
    assert pieces_by_agent(THREE, "jigsaw", "shared_task", roles, "split") == {"a01": ["B", "A"], "a02": [],
                                                                               "a03": ["C"]}
    assert pieces_by_agent(THREE, "jigsaw", "shared_task", roles, "split_copy") == {"a01": ["B", "A"], "a02": ["B"],
                                                                                    "a03": ["C"]}
    explicit = {"a01": ["A", "B"], "a02": ["C"], "a03": []}
    assert pieces_by_agent(THREE, "jigsaw", "shared_task", None, explicit) == explicit
    assert pieces_by_agent(THREE, "as_released", "shared_task", None, explicit) == {a: [] for a in THREE}
    bench = SWE("flipt-02e2", "jigsaw")
    config = build_config("default", "small", ["access=a01:A+B,a02:C,a03:"])
    assert bench.access_plan(THREE, config, None) == explicit
    assert build_swe("flipt-02e2", "jigsaw", THREE, config)["pieces"] == explicit
    assert bench.whole_access() == ["whole"] and SWE("flipt-02e2", "as_released").whole_access() == []
    assert bench.split_items(3) == ["B", "C", "A"] and SWE("flipt-02e2", "own_piece").split_items(2) == ["A", "B", "C"]
    with pytest.raises(ValueError, match="unknown item"):
        bench.check(build_config("default", "small", ["access=a01:slice1,a02:,a03:"]))
    with pytest.raises(ValueError, match="plants no pieces"):
        SWE("flipt-02e2", "as_released").check(config)
    SWE("flipt-02e2", "as_released").check(build_config("default", "small", ["access=a01:,a02:,a03:"]))
    assert bench.access_plan(THREE, None, None) == {a: ["whole"] for a in THREE}


def test_knowledge_work_tools_and_permissions_follow_the_plan():
    assert split_items(3) == ["review", "review", "read"] and split_items(4) == ["review", "review", "read", "read"]
    bench = KnowledgeWork()
    seats = bench.plan(THREE, build_config("default", "small"), None)
    assert [(s["review"], s["read"]) for s in seats.values()] == [(True, False), (True, False), (False, True)]
    assert bench.split and is_split(seats)
    roles = assign_roles(THREE, 1, 0, seed=1)
    regulator = next(a for a in THREE if roles[a] == "regulator")
    seats = bench.plan(THREE, build_config("default", "small", ["regulators=1", "seed=1"]), roles)
    assert (seats[regulator]["review"], seats[regulator]["read"]) == (False, False)
    neutral = [a for a in THREE if roles[a] == "worker"]
    assert [(seats[a]["review"], seats[a]["read"]) for a in neutral] == [(True, False), (False, True)]
    assert "review_deliverable" not in bench.tools(THREE, None).per_agent[regulator]
    seats = bench.plan(THREE, build_config("default", "small", ["regulators=1", "seed=1", "access=split_copy"]), roles)
    assert (seats[regulator]["review"], seats[regulator]["read"]) == (True, False)      # a copy of `review`
    assert [(seats[a]["review"], seats[a]["read"]) for a in neutral] == [(True, False), (False, True)]
    seats = bench.plan(THREE, build_config("default", "small", ["access=all"]), None)
    assert all(s["review"] and s["read"] for s in seats.values()) and not bench.split
    seats = bench.plan(THREE, build_config("default", "small", ["access=a01:review+read,a02:read,a03:"]), None)
    assert [(s["review"], s["read"]) for s in seats.values()] == [(True, True), (False, True), (False, False)]
    assert bench.split
    tools = bench.tools(THREE, None)
    assert tools.per_agent["a01"][-1] == "review_deliverable" and "review_deliverable" not in tools.per_agent["a02"]
    seats = bench.plan(["a01"], build_config("default", "small", ["n_agents=1"]), None)
    assert seats["a01"]["review"] and seats["a01"]["read"] and not bench.split
    with pytest.raises(ValueError, match="unknown item"):
        bench.check(build_config("default", "small", ["access=a01:whole,a02:,a03:"]))


def test_every_task_records_the_plan():
    config = build_config("default", "small", ["regulators=1", "seed=2"])
    task = swarm_task(OpenMath(), config)
    cb = task.dataset[0].metadata["cheatbench"]
    roles = cb["roles"]
    assert cb["access"]["policy"] == "split"
    held = cb["access"]["agents"]
    assert [held[a] for a in THREE if roles[a] == "worker"] == [["slice1"], ["slice2"]]
    assert all(held[a] == [] for a in THREE if roles[a] != "worker")
    assert cb["openmath"]["archive_parts"] == archive_parts(held)
    assert all(label in cb["deviations"] for label in SPLIT_DEVIATIONS)
    line = access_line(task)
    assert line.startswith("== access split: ") and "nothing" in line and "slice1" in line and "whole" not in line
    copy = swarm_task(OpenMath(), build_config("default", "small", ["regulators=1", "seed=2", "access=split_copy"]))
    cb = copy.dataset[0].metadata["cheatbench"]
    assert cb["access"]["policy"] == "split_copy"
    assert [cb["access"]["agents"][a] for a in THREE if roles[a] != "worker"] == [["slice1"]]
    assert access_line(copy).startswith("== access split_copy: ")
    shared = swarm_task(OpenMath(), build_config("default", "small", ["access=all"]))
    assert shared.dataset[0].metadata["cheatbench"]["access"] == {"policy": "all",
                                                                   "agents": {a: ["whole"] for a in THREE}}
    collab = build_config("default", "small", ["mode=collaboration_needed"])
    assert build_swe("flipt-02e2", "jigsaw", THREE, collab)["pieces"] == {"a01": ["B"], "a02": ["C"], "a03": ["A"]}
    explicit = swarm_task(OpenMath(), build_config("default", "small", ["access=a01:slice1,a02:slice2,a03:"]))
    assert access_line(explicit) == "== access explicit: a01 slice1, a02 slice2, a03 nothing"
