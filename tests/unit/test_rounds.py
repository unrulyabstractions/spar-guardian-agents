"""Rounds of up to N tool calls on the real ORBIT swarm executor and Inspect's mock provider: N calls per round, a reply
of several calls counted per call, end_round, a reply with no tool call, the board calls that use up no round but count
toward N, R rounds and the lines when they run out, the live board, the round lines and the first message's values,
and the record of every call's time."""
from __future__ import annotations

import json

from src.configuration import prompt_texts
from src.configuration.presets import PRESETS
from src.domains.openmath import OpenMath
from src.swarm import turn_rules as rules
from src.swarm.board import round_rules
from src.swarm.turn_rules import _turn_rounds as rounds_clock
from src.swarm.turn_rules import turn_activation
from src.tasks import instruction_suffix, system_prompts
from tests.support.mock_model import calls, tagged_infos, text
from tests.support.turn_rules_harness import (
    PROMPTS,
    board_post,
    by_agent,
    do,
    end_round,
    outcomes,
    pull,
    read,
    run,
    sequential,
    status,
    store,
    submit,
)

ROUND = PROMPTS["round"]


def entries(sample, agent: str) -> list[dict]:
    return [e for e in store(sample, "timeline") if e["agent"] == agent]


def executed(sample, agent: str) -> list[str]:
    return [e["item"] for e in sample.store.get("WorkLog:executed") or [] if e["agent"] == agent]


def test_a_round_runs_up_to_n_calls_one_model_call_after_another(tmp_path):
    config = pull(n_agents=1, rounds=2, calls_per_round=3)
    sample, rec = run(config, {"a01": [do("A")]}, tmp_path)
    # Each round runs three model calls, each seeing the result before it, then ends at N.
    assert executed(sample, "a01") == ["A"] * 6
    assert [e["end"] for e in entries(sample, "a01")] == ["calls", "calls"]
    assert [len(e["calls"]) for e in entries(sample, "a01")] == [3, 3]
    assert [r["roles"][-2:] for r in rec[1:3]] == [["assistant", "tool"], ["assistant", "tool"]]
    # Each round uses up one of the agent's R; the lone agent ends "rounds" at R.
    assert store(sample, "rounds_used") == {"a01": 2} and store(sample, "ended") == {"a01": "rounds"}


def test_a_reply_of_several_calls_counts_each_and_a_call_past_n_is_refused(tmp_path):
    config = pull(n_agents=1, rounds=1, calls_per_round=3)
    sample, _ = run(config, {"a01": [do("A", "B"), do("C", "D")]}, tmp_path)
    # Two calls, then two more: the fourth call of the round is refused and the round ends at N.
    assert executed(sample, "a01") == ["A", "B", "C"]
    [entry] = entries(sample, "a01")
    assert entry["end"] == "calls" and [c["refused"] for c in entry["calls"]] == [False, False, False, True]
    assert [c["k"] for c in entry["calls"]] == [1, 2, 3, 4]
    refusal = ROUND["spent"].format(calls=3)
    assert outcomes(sample, "work")[-1] == ("work", "", refusal)


def test_end_round_ends_the_round_and_never_counts(tmp_path):
    config = pull(n_agents=1, rounds=2, calls_per_round=2)
    sample, rec = run(config, {"a01": [do("A"), calls(("work", {"item": "B"}), ("end_round", {}),
                                                         ("work", {"item": "late"})), do("C"), do("D"), do("E")]},
                      tmp_path)
    first, second = entries(sample, "a01")
    # The second reply works on B, ends its round, and its later call is refused. end_round is not one of the N.
    assert first["end"] == "end_round" and [c["tool"] for c in first["calls"]] == ["work", "work", "end_round", "work"]
    assert [c["refused"] for c in first["calls"]] == [False, False, False, True]
    assert executed(sample, "a01") == ["A", "B", "C", "D"]
    assert second["end"] == "calls"
    assert ("end_round", ROUND["round_ended"], None) in outcomes(sample, "end_round")
    assert ("work", "", ROUND["closed"]) in outcomes(sample, "work")


def test_a_round_of_end_round_alone_uses_up_a_round(tmp_path):
    config = pull(n_agents=1, rounds=2, calls_per_round=5)
    sample, _ = run(config, {"a01": [end_round()]}, tmp_path)
    assert [(e["charge"], e["end"]) for e in entries(sample, "a01")] == [("idle", "end_round"), ("idle", "end_round")]
    assert store(sample, "rounds_used") == {"a01": 2}


def test_a_reply_with_no_tool_call_ends_the_round(tmp_path):
    config = pull(n_agents=1, rounds=3, calls_per_round=5)
    sample, rec = run(config, {"a01": [do("A"), text("thinking"), do("B"), do("C"), text("done")]}, tmp_path)
    assert [e["end"] for e in entries(sample, "a01")] == ["no_tool_call", "no_tool_call", "no_tool_call"]
    assert [e["charge"] for e in entries(sample, "a01")] == ["work", "work", "idle"]
    # The next round opens with its line, so no input ends with a model turn.
    assert all(r["tag"] is None for r in rec)


def test_board_calls_use_up_no_round_but_count_toward_n(tmp_path):
    config = sequential(n_agents=2, rounds=2, calls_per_round=2)
    sample, _ = run(config, {"a01": [status(), read(), do("A")], "a02": [do("B")]}, tmp_path)
    first, *rest = entries(sample, "a01")
    # Two board calls fill the round's N, and the round uses up none of a01's rounds.
    assert (first["charge"], first["charged"], first["end"]) == ("board", False, "calls")
    assert [c["free"] for c in first["calls"]] == [True, True]
    assert store(sample, "board_rounds") == {"a01": 1}
    # So a01 gets a third round of the plan (2R rounds on a board), and both agents use up their two.
    assert [e["round"] for e in entries(sample, "a01")] == [1, 2, 3]
    assert [e["n"] for e in entries(sample, "a01")] == [1, 1, 2]
    assert store(sample, "rounds_used") == {"a01": 2, "a02": 2}
    # A board call and a work call in one round: the round uses one up.
    mixed, _ = run(config, {"a01": [read(), do("A")], "a02": [do("B")]}, tmp_path / "mixed")
    assert [e["charge"] for e in entries(mixed, "a01")] == ["work", "work"]


def test_a_refused_call_never_ran_so_it_uses_up_no_round(tmp_path):
    """A call our rules refused did nothing: a round of board calls whose work call the gate refused past N, or after
    end_round, uses up none of the agent's rounds; so does a refused submit beside board calls. The calls keep their
    places and their refusals."""
    config = sequential(n_agents=2, rounds=2, calls_per_round=2)
    past_n = calls(("board_read", {}), ("board_status", {}), ("work", {"item": "late"}))
    sample, _ = run(config, {"a01": [past_n, do("A")], "a02": [do("B")]}, tmp_path)
    first = entries(sample, "a01")[0]
    assert [(c["tool"], c["refused"]) for c in first["calls"]] == [("board_read", False), ("board_status", False),
                                                                   ("work", True)]
    assert (first["charge"], first["charged"], first["end"]) == ("board", False, "calls")
    assert "late" not in executed(sample, "a01") and store(sample, "board_rounds")["a01"] == 1
    closed = calls(("board_read", {}), ("end_round", {}), ("work", {"item": "late"}))
    sample, _ = run(config, {"a01": [closed, do("A")], "a02": [do("B")]}, tmp_path / "closed")
    first = entries(sample, "a01")[0]
    assert [c["refused"] for c in first["calls"]] == [False, False, True]
    assert (first["charge"], first["end"]) == ("board", "end_round")

    async def missing(agent: str) -> str | None:
        return "nothing is shipped"

    early = calls(("board_read", {}), ("submit", {"answer": "early"}))
    sample, _ = run(config.replace(submit_delay_rounds=0), {"a01": [early, do("A")], "a02": [do("B")]},
                    tmp_path / "submit", missing=missing)
    first = entries(sample, "a01")[0]
    assert [(c["tool"], c["refused"]) for c in first["calls"]] == [("board_read", False), ("submit", True)]
    assert first["charge"] == "board" and sample.store.get("TeamSubmission:refused")[0]["agent"] == "a01"
    # A round whose only call was refused holds no call that ran: idle, which uses one up.
    assert turn_activation.round_charge([{"tool": "work", "refused": True}], None) == "idle"


def test_peer_shell_is_no_board_call():
    assert rules.PEER_SHELL_TOOL not in rules.FREE_TOOLS
    assert set(rules.FREE_TOOLS) == {*rules.BOARD_TOOLS, *rules.OVERSIGHT_TOOLS}


def test_an_agent_out_of_rounds_exits_and_its_peers_hear_it(tmp_path):
    config = sequential(n_agents=2, rounds=3, calls_per_round=1, regulators=0)
    sample, rec = run(config, {"a01": [do("A")], "a02": [read(), read(), read(), read(), do("B")]}, tmp_path)
    # a01 uses up its three rounds; a02's reads use up none, so it outlasts a01 and hears of its exit.
    assert store(sample, "ended")["a01"] == "rounds"
    lines = [m for r in by_agent(rec, "a02") for m in r["presence"]]
    assert PROMPTS["presence"]["rounds"].format(agent="a01") + "\n" + PROMPTS["presence"]["exited"].format(
        agent="a01") in lines
    assert store(sample, "exits")["a01"]["why"] == "rounds"


def test_the_board_is_live_within_a_round(tmp_path):
    config = pull(n_agents=2, rounds=1, calls_per_round=3, max_concurrency=2)
    sample, _ = run(config, {"a01": [board_post("found it"), do("A")], "a02": [do("B"), do("C"), read()]}, tmp_path)
    # a02 reads after a01 posted in the same concurrent round, and sees the post.
    (_, result, error), *_ = outcomes(sample, "board_read")
    assert error is None and "found it" in result
    assert [c["tool"] for c in entries(sample, "a02")[0]["calls"]] == ["work", "work", "board_read"]
    assert {step.visibility for step in rules.schedule(config, config.names).steps} == {"live"}


def test_round_lines_open_each_round_and_the_first_message_gives_the_values(tmp_path):
    config = pull(n_agents=1, rounds=2, calls_per_round=2)
    sample, rec = run(config, {"a01": [do("A")]}, tmp_path)
    lines = [i for i in tagged_infos(sample, rules.ROUND_TAG)]
    assert [(i["round"], i["n"], i["of"], i["content"]) for i in lines] == [(1, 1, 2, "Round 1 of 2."),
                                                                            (2, 2, 2, "Round 2 of 2.")]
    # The line is durable: the round's first model call ends with it, and every later call keeps it.
    assert rec[0]["last"] == "Round 1 of 2." and rec[2]["last"] == "Round 2 of 2."
    assert rec[3]["texts"].count("Round 1 of 2.") == 1
    # The first message ends with the values; the system prompt states the rules without numbers.
    lone = PRESETS["default"].replace(n_agents=1, rounds=7, calls_per_round=4)
    values = "You have 7 rounds, and up to 4 tool calls per round."
    assert instruction_suffix(OpenMath(), lone).rstrip().endswith(values)
    plan = OpenMath().plan(lone)
    [prompt] = system_prompts(OpenMath(), plan).values()
    assert ROUND["rules"] in prompt and "Calls to" not in prompt and not any(ch.isdigit() for ch in ROUND["rules"])


def test_the_rules_name_the_board_calls_an_agent_holds():
    team = PRESETS["default"].replace(n_agents=3, regulators=1)
    roles = {"a01": "worker", "a02": "regulator", "a03": "worker"}
    worker, regulator = round_rules(team, "a01", roles), round_rules(team, "a02", roles)
    assert worker == f"{ROUND['rules']} " + ROUND["free"].format(tools="board_status, board_read and board_post")
    assert regulator.endswith(ROUND["free"].format(
        tools="board_status, board_read, board_post, get_peer_actions and get_peer_observations"))
    assert prompt_texts.rounds_values(team) == "You have 30 rounds, and up to 10 tool calls per round."


def test_every_call_and_round_records_its_time_and_order(tmp_path):
    config = pull(n_agents=1, rounds=1, calls_per_round=3)
    sample, _ = run(config, {"a01": [do("A"), do("B", "C")]}, tmp_path)
    [entry] = entries(sample, "a01")
    times = [(c["t0"], c["t1"]) for c in entry["calls"]]
    assert all(t0 is not None and t1 is not None and t0 <= t1 for t0, t1 in times)
    assert [c["k"] for c in entry["calls"]] == [1, 2, 3]
    assert entry["t0"] <= times[0][0] and times[0][1] <= times[1][0] and times[-1][1] <= entry["t1"]
    # Each call's id is the tool call's, so the eval log's events join it.
    ids = [e.id for e in sample.events if e.event == "tool"]
    assert [c["id"] for c in entry["calls"]] == ids


def test_an_ending_tool_ends_the_round_and_refuses_later_calls(tmp_path):
    config = sequential(n_agents=2, rounds=2, calls_per_round=5)
    sample, _ = run(config, {"a01": [calls(("stop_working", {}), ("work", {"item": "late"}))], "a02": [do("B")]},
                    tmp_path)
    first = entries(sample, "a01")[0]
    assert (first["charge"], first["end"]) == ("stop", "ending") and len(entries(sample, "a01")) == 1
    assert executed(sample, "a01") == [] and ("work", "", ROUND["ended"]) in outcomes(sample, "work")
    # A lone agent's hand-in ends its round too: the run ends after it.
    lone, _ = run(pull(n_agents=1, rounds=3, calls_per_round=5), {"a01": [do("A"), submit(), do("late")]},
                  tmp_path / "lone")
    assert [(e["charge"], e["end"]) for e in entries(lone, "a01")] == [("submit", "ending")]
    assert executed(lone, "a01") == ["A"]


def test_a_round_ends_once_the_time_limit_has_passed(tmp_path, monkeypatch):
    clock = iter(range(1000))
    monkeypatch.setattr(rounds_clock, "now", lambda: float(next(clock)))    # one second per reading
    config = pull(n_agents=1, rounds=2, calls_per_round=50)
    sample, _ = run(config, {"a01": [do("A")]}, tmp_path, time_limit_s=10)
    # The first batch after the limit ends the round, long before its 50 calls; ORBIT halts between rounds itself.
    first = entries(sample, "a01")[0]
    assert first["end"] == "time" and len(first["calls"]) < 10


def test_peer_tools_see_a_teammates_calls_of_the_round_under_way(tmp_path):
    config = pull(n_agents=3, regulators=1, rounds=1, calls_per_round=3, max_concurrency=3)
    roles = {"a01": "worker", "a02": "regulator", "a03": "worker"}
    peek = calls(("get_peer_actions", {"peer": "a01"}))
    sample, _ = run(config, {"a01": [do("A1"), do("A2"), do("A3")], "a02": [do("R1"), do("R2"), peek],
                             "a03": [do("C1")]}, tmp_path, roles=roles)
    (_, answer, error), *_ = outcomes(sample, "get_peer_actions")
    # ORBIT's registry holds a01's state as of its last round; the boundary's conversation shows this round's calls.
    shown = json.loads(answer)["actions"]
    assert error is None and shown and {c["round"] for c in shown} == {1}
    # A peer tool is a board call: a02's round of work and get_peer_actions uses one up, and the call counts to N.
    first = entries(sample, "a02")[0]
    assert first["charge"] == "work" and [c["free"] for c in first["calls"]] == [False, False, True]
