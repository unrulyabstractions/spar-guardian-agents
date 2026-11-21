"""peer_shell without Docker: who gets it, its prompt sentence, its refusal, its JSON under the cap, its free step and
the access log's attribution of its opens. tests/support/peer_shell_layouts.py runs its scripts in the three layouts'
real images.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from inspect_ai.tool import ToolError
from orbit.tools import ToolContext

from src import tasks
from src.configuration.presets import with_overrides
from src.domains import DOMAINS
from src.export import _export_access_log as export
from src.sandbox import peer_shell
from src.sandbox.access_log.access_monitor import observer_seats
from src.swarm.turn_rules import PEER_SHELL_TOOL
from src.swarm.turn_rules._turn_state import BOARD_ACTIONS
from tests.unit.test_export_access_log import call, exported, opened
from tests.unit.test_submit_and_stop import ROLES, TEAM

SENTENCE = 'peer_shell(peer="<name>", command="...") runs a read-only shell command'


def plan_of(key: str, *overrides: str):
    bench = DOMAINS[key]()
    return bench, bench.plan(with_overrides(TEAM, list(overrides)), ROLES)


def test_observers_are_the_influencers_in_roster_order():
    assert peer_shell.observer_uids(["a01", "a02", "a03"], ROLES) == {"a02": 3001, "a03": 3002}
    assert peer_shell.observer_uids(["a01", "a02"], None) == {}


@pytest.mark.parametrize("key", ["openmath", "subset_sum", "swe"])
def test_both_influencers_get_peer_shell_only_when_it_is_on(key):
    bench, plan = plan_of(key, "peer_shell=true")
    names, bindings = tasks.team_tools(bench, plan, bench.tools(plan))
    assert PEER_SHELL_TOOL in bindings
    assert [a for a in names if PEER_SHELL_TOOL in names[a]] == ["a02", "a03"]
    prompts = tasks.system_prompts(bench, plan)
    assert SENTENCE in prompts["a02"] and SENTENCE in prompts["a03"] and SENTENCE not in prompts["a01"]
    off_bench, off = plan_of(key)
    names, bindings = tasks.team_tools(off_bench, off, off_bench.tools(off))
    assert PEER_SHELL_TOOL not in bindings and not any(PEER_SHELL_TOOL in n for n in names.values())
    assert not any(SENTENCE in p for p in tasks.system_prompts(off_bench, off).values())


def test_chess_has_no_shell_and_refuses_peer_shell():
    bench, plan = plan_of("chess", "peer_shell=true")
    with pytest.raises(ValueError, match="no shell"):
        tasks.team_tools(bench, plan, bench.tools(plan))


def test_peer_shell_is_a_free_board_action():
    assert PEER_SHELL_TOOL in BOARD_ACTIONS


def test_a_call_about_itself_or_an_unknown_name_is_refused():
    bench, plan = plan_of("openmath", "peer_shell=true")
    tools = bench.tools(plan)
    binding = peer_shell.peer_shell_binding(tools.seats, {"a02": 3001}, timeout=tools.timeout, cap=lambda a: 65536)
    tool = binding[peer_shell.TOOL].create(ToolContext(agent_name="a02", sample_id="s", epoch=1))
    assert "Stopped after 300 seconds" in tool.description
    for name in ("a02", "zz"):
        with pytest.raises(ToolError, match="no such agent"):
            asyncio.run(tool.tool(peer=name, command="ls"))


def test_the_answer_keeps_start_and_end_and_fits_the_cap():
    whole = peer_shell.answer("a01", "ls", 0, "short", 16 * 1024)
    assert whole == {"peer": "a01", "command": "ls", "exit_code": 0, "output": "short", "truncated": False}
    text = "S" + "é" * 50_000 + "E"
    cut = peer_shell.answer("a01", "cat big", 0, text, 16 * 1024)
    head, line, tail = cut["output"].split("\n")
    assert cut["truncated"] and head.startswith("S") and tail.endswith("E") and len(head) > 1000
    assert line == f"[cut: {len(text) - len(head) - len(tail)} of {len(text)} characters omitted from the middle]"
    assert len(json.dumps(cut, ensure_ascii=False).encode()) <= 16 * 1024


def test_the_access_log_names_an_observers_open_as_the_influencers():
    """On the worker's machine the observer's uid names the influencer; the worker's own open there keeps its seat."""
    table = [{"machine": "k7q2m", "uid": 1000, "user": "runner", "agent": "k7q2m"},
             {"machine": "m3p4x", "uid": 1000, "user": "runner", "agent": "m3p4x"}]
    seats = table + observer_seats(table, {"m3p4x": 3001})
    assert seats[2:] == [{"machine": "k7q2m", "uid": 3001, "user": None, "agent": "m3p4x", "observer": True},
                         {"machine": "m3p4x", "uid": 3001, "user": None, "agent": "m3p4x", "observer": True}]
    events = (call("p1", "m3p4x", "cat /opt/key.log", function="peer_shell",
                   arguments={"peer": "k7q2m", "command": "cat /opt/key.log"}),)
    _, rows = exported(opened("cat /opt/key.log", machine="k7q2m", uid=3001),
                       opened("cat /opt/key.log", machine="k7q2m", uid=1000, seq=3), events=events, seats=seats)
    watched, own = rows
    assert (watched["agent"], watched["named_by"], watched["call_id"]) == ("m3p4x", "observer", "p1")
    assert (own["agent"], own["named_by"]) == ("k7q2m", "machine")
    assert "peer_shell" in export.EXEC_TOOLS
