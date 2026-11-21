"""The cap on one tool result, by role, and the peer tools' JSON under it: no Docker, no paid calls.

An influencer's tools carry SwarmConfig.influencer_tool_output_bytes (64 KiB) as ToolDef.max_output; a worker's take
the run's cap (16 KiB unless set). The peer tools pack whole rows under the caller's cap and name what they left out, so
Inspect never cuts their JSON.
"""
from __future__ import annotations

import json

from src.export.eval_sample_view import SampleView, span_owners
from src.swarm import _regulator_oversight as oversight
from src.swarm.tool_output_caps import DEFAULT_CAP, cap_of
from tests.support.mock_model import calls
from tests.support.turn_rules_harness import do, pull, run

ROLES = {"a01": "worker", "a02": "regulator", "a03": "disregulator"}
BIG = "x" * 30_000          # over the workers' 16 KiB, under the influencers' 64 KiB


def tool_events(sample, log, function: str) -> dict[str, list]:
    """Each agent's events of one tool, by the span that holds it."""
    owner = span_owners(SampleView.from_eval_sample(sample, log))
    out: dict[str, list] = {}
    for event in sample.events:
        if event.event == "tool" and event.function == function:
            out.setdefault(owner(event.span_id)[0], []).append(event)
    return out


def config(**fields):
    return pull(steps_per_agent=1, regulators=1, disregulators=1, concurrent_rounds=False, turn_order="round_robin",
                **fields)


def test_cap_of_by_role():
    c = config()
    assert c.influencer_tool_output_bytes == 64 * 1024
    assert cap_of(c, "regulator") == cap_of(c, "disregulator") == 64 * 1024
    assert cap_of(c, "worker") == DEFAULT_CAP == 16 * 1024
    off = config(influencer_tool_output_bytes=None)
    assert cap_of(off, "regulator") == DEFAULT_CAP


def test_an_influencer_sees_64_kib_of_one_result_and_a_worker_16(tmp_path):
    """The same tool, the same 30,000-character result: Inspect cuts the worker's at 16 KiB and says so; the regulator
    and the disregulator get theirs whole."""
    sample, _, log = run(config(), {a: [do(BIG)] for a in ROLES}, tmp_path, roles=ROLES, with_log=True)
    events = tool_events(sample, log, "work")
    worker, = events["a01"]
    assert worker.truncated == (len(f"a01 worked on {BIG}"), 16 * 1024)
    for influencer in ("a02", "a03"):
        event, = events[influencer]
        assert event.truncated is None and str(event.result) == f"{influencer} worked on {BIG}"


def test_without_the_field_the_influencers_take_the_runs_cap(tmp_path):
    sample, _, log = run(config(influencer_tool_output_bytes=None), {a: [do(BIG)] for a in ROLES}, tmp_path,
                         roles=ROLES, with_log=True)
    for agent, (event,) in tool_events(sample, log, "work").items():
        assert event.truncated is not None, agent


def test_get_peer_observations_over_the_cap_packs_whole_rows_and_names_the_rest(tmp_path):
    """a01 makes five calls whose results each pass max_chars (16000), about 80 KB together, over the regulator's
    64 KiB. The answer keeps the newest whole rows, names the older one in not_shown_ids, parses as JSON and stays
    under the cap, so Inspect cuts nothing; a second call with ids returns those it names."""
    scripts = {"a01": [do(*[BIG] * 5)],
               "a02": [calls((oversight.OBSERVATIONS_TOOL, {"peer": "a01"})),
                       calls((oversight.OBSERVATIONS_TOOL, {"peer": "a01", "ids": [0, 1]}))],
               "a03": [do("C")]}
    sample, _, log = run(pull(steps_per_agent=2, regulators=1, disregulators=1, concurrent_rounds=False,
                              turn_order="round_robin", free_board_actions_influencer=1), scripts, tmp_path,
                         roles=ROLES, with_log=True)
    first, second = tool_events(sample, log, oversight.OBSERVATIONS_TOOL)["a02"][:2]
    for event in (first, second):
        assert event.truncated is None and len(str(event.result).encode()) <= 64 * 1024
    answer = json.loads(str(first.result))
    # four rows of about 16 KB and the wrapper fit under 64 KiB; a fifth would not
    assert [r["id"] for r in answer["observations"]] == [1, 2, 3, 4] and answer["not_shown_ids"] == [0]
    # a01's own results reached it cut at the workers' 16 KiB, and those cut results are what it holds
    assert all(r["shown_chars"] == 16000 and r["truncated"] and r["chars"] > 16000 for r in answer["observations"])
    again = json.loads(str(second.result))
    assert [r["id"] for r in again["observations"]] == [0, 1] and again["not_shown_ids"] == []


def called(n: int, text: str) -> list[oversight.Called]:
    return [oversight.Called(i, 1, "shell", {"command": text}, "ok", text) for i in range(n)]


def test_a_first_row_alone_over_the_budget_is_cut_with_the_cut_stated():
    """At the workers' 16 KiB one 16000-character result and its wrapper do not fit: the newest row is cut further,
    shown_chars says how far, and the rest are named."""
    answer = oversight.observations_payload("a01", called(3, "é" * 16000), None, 20, 16000, budget=16 * 1024)
    assert len(oversight._dump(answer).encode()) <= 16 * 1024
    row, = answer["observations"]
    assert row["id"] == 2 and row["truncated"] and row["chars"] == 16000 and row["shown_chars"] == len(row["result"])
    assert 0 < row["shown_chars"] < 16000 and answer["not_shown_ids"] == [0, 1]


def test_get_peer_actions_packs_under_the_budget_and_takes_ids():
    calls_ = called(6, "y" * 5000)
    answer = oversight.actions_payload("a01", calls_, 20, None, budget=16 * 1024)
    assert len(oversight._dump(answer).encode()) <= 16 * 1024
    shown = [a["id"] for a in answer["actions"]]
    assert shown == [3, 4, 5] and answer["shown"] == 3 and answer["not_shown_ids"] == [0, 1, 2]
    again = oversight.actions_payload("a01", calls_, 20, None, ids=[1, 0, 9], budget=16 * 1024)
    assert [a["id"] for a in again["actions"]] == [1, 0, 9] and again["actions"][2]["status"] == "no such call"
    assert again["not_shown_ids"] == []
    huge = oversight.actions_payload("a01", called(1, "z" * 40_000), 20, None, budget=16 * 1024)
    row, = huge["actions"]
    assert "arguments" not in row and row["truncated"]
    assert row["arguments_chars"] == len(json.dumps({"command": "z" * 40_000}))
    assert len(oversight._dump(huge).encode()) <= 16 * 1024
