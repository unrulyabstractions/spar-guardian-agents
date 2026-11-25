"""One cap on a tool result for every agent, cut in the middle, and the peer tools' JSON under it: no Docker, no paid
calls.

SwarmConfig.tool_output_bytes (64 KiB) is every agent's cap, workers and influencers alike. A longer text result keeps
its start and its end with one line between them, through Inspect's one truncation point, so ORBIT's built-in bash and
python get the same rule. The peer tools pack whole rows under the cap and name what they left out. The replay cuts as
each run did.
"""
from __future__ import annotations

import json

import inspect_ai.model._call_tools as call_tools

from src.configuration.swarm_config import TOOL_OUTPUT_BYTES
from src.export.eval_sample_view import SampleView, span_owners
from src.replay.replay_run import OutputCut, tool_output
from src.swarm import _regulator_oversight as oversight
from src.swarm.tool_output_caps import CUT_LINE, INSPECT_CUT, cap_of, middle_cut
from tests.support.mock_model import calls
from tests.support.turn_rules_harness import do, pull, run

ROLES = {"a01": "worker", "a02": "regulator", "a03": "disregulator"}
BIG = "S" + "x" * 112_000 + "E"         # a 112 KB output, past the 64 KiB cap


def tool_events(sample, log, function: str) -> dict[str, list]:
    """Each agent's events of one tool, by the span that holds it."""
    owner = span_owners(SampleView.from_eval_sample(sample, log))
    out: dict[str, list] = {}
    for event in sample.events:
        if event.event == "tool" and event.function == function:
            out.setdefault(owner(event.span_id)[0], []).append(event)
    return out


def config(**fields):
    return pull(rounds=1, regulators=1, disregulators=1, concurrent_rounds=False, turn_order="round_robin",
                **fields)


def assert_middle(text: str, whole: str, cap: int) -> None:
    head, line, tail = text.split("\n")
    assert whole.startswith(head) and whole.endswith(tail) and abs(len(head) - len(tail)) <= 4, (len(head), len(tail))
    assert line == CUT_LINE.format(omitted=len(whole) - len(head) - len(tail), total=len(whole)), line
    assert len(text.encode()) <= cap


def test_one_cap_for_every_role():
    c = config()
    assert c.tool_output_bytes == TOOL_OUTPUT_BYTES == 65536 and c.tool_output_cut == "middle"
    assert cap_of(c) == 65536


def test_middle_cut_keeps_start_and_end_and_leaves_short_text_alone():
    cut = middle_cut(BIG, 65536)
    assert_middle(cut, BIG, 65536)
    assert middle_cut("short", 65536) is None
    wide = "é" * 70_000                 # two bytes a character: the cut counts bytes and names characters
    assert_middle(middle_cut(wide, 65536), wide, 65536)


def test_inspects_one_truncation_point_cuts_in_the_middle_for_every_tool():
    """resolve_tool_content is where Inspect records every tool's result, ORBIT's built-in bash and python included."""
    resolved = call_tools.resolve_tool_content(BIG, "bash", 65536)
    assert resolved.truncated == (len(BIG), 65536)
    assert_middle(resolved.content, BIG, 65536)
    assert call_tools.resolve_tool_content("short", "bash", 65536).truncated is None


def test_every_agent_sees_the_same_cut_of_one_112_kb_result(tmp_path):
    """The same tool, the same 112 KB result: worker, regulator and disregulator each get its start, its end and the
    cut line, within 64 KiB; the ToolEvent records (raw bytes, cap)."""
    sample, _, log = run(config(), {a: [do(BIG)] for a in ROLES}, tmp_path, roles=ROLES, with_log=True)
    for agent, (event,) in tool_events(sample, log, "work").items():
        whole = f"{agent} worked on {BIG}"
        assert event.truncated == (len(whole), 65536), agent
        assert_middle(str(event.result), whole, 65536)


def test_under_the_cap_a_result_comes_back_whole(tmp_path):
    sample, _, log = run(config(), {a: [do("x" * 30_000)] for a in ROLES}, tmp_path, roles=ROLES, with_log=True)
    for agent, (event,) in tool_events(sample, log, "work").items():
        assert event.truncated is None and str(event.result) == f"{agent} worked on {'x' * 30_000}"


def test_get_peer_observations_over_the_cap_packs_whole_rows_and_names_the_rest(tmp_path):
    """a01 makes five calls whose results each pass max_chars (16000), about 80 KB together, over the 64 KiB cap. The
    answer keeps the newest whole rows, names the older one in not_shown_ids, parses as JSON and stays under the cap,
    so nothing cuts it; a second call with ids returns those it names."""
    scripts = {"a01": [do(*["x" * 30_000] * 5)],
               "a02": [calls((oversight.OBSERVATIONS_TOOL, {"peer": "a01"})),
                       calls((oversight.OBSERVATIONS_TOOL, {"peer": "a01", "ids": [0, 1]}))],
               "a03": [do("C")]}
    sample, _, log = run(pull(rounds=2, regulators=1, disregulators=1, concurrent_rounds=False,
                              turn_order="round_robin", calls_per_round=5), scripts, tmp_path,
                         roles=ROLES, with_log=True)
    first, second = tool_events(sample, log, oversight.OBSERVATIONS_TOOL)["a02"][:2]
    for event in (first, second):
        assert event.truncated is None and len(str(event.result).encode()) <= 65536
    answer = json.loads(str(first.result))
    # four rows of about 16 KB and the wrapper fit under 64 KiB; a fifth would not
    assert [r["id"] for r in answer["observations"]] == [1, 2, 3, 4] and answer["not_shown_ids"] == [0]
    assert all(r["shown_chars"] == 16000 and r["truncated"] and r["chars"] == 30_014 for r in answer["observations"])
    again = json.loads(str(second.result))
    assert [r["id"] for r in again["observations"]] == [0, 1] and again["not_shown_ids"] == []


def called(n: int, text: str) -> list[oversight.Called]:
    return [oversight.Called(i, 1, "shell", {"command": text}, "ok", text) for i in range(n)]


def test_a_first_row_alone_over_the_budget_is_cut_with_the_cut_stated():
    """At a 16 KiB cap one 16000-character result and its wrapper do not fit: the newest row is cut further,
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


def test_the_replay_cuts_as_each_run_did():
    """A run whose config records tool_output_cut replays the middle cut at its cap; an older run, Inspect's own cut at
    the start, at its recorded cap or Inspect's 16 KiB."""
    new = OutputCut.of({"tool_output_bytes": 65536, "tool_output_cut": "middle"})
    assert_middle(tool_output("shell", BIG, new), BIG, 65536)
    old = OutputCut.of({"tool_output_bytes": None})
    assert old == OutputCut(16 * 1024, False)
    seen = tool_output("shell", BIG, old)
    assert seen == INSPECT_CUT("shell", BIG, 16 * 1024).output and "too long to be displayed" in seen
    assert "[cut:" not in seen and "Sxxx" in seen and not seen.rstrip().endswith("E")


def test_a_cap_of_zero_or_less_never_cuts_as_in_inspect():
    """Inspect's react() gives its submit tool max_output 0, which Inspect reads as no cap; so does a run cap of 0."""
    from inspect_ai.model import GenerateConfig
    from inspect_ai.model._generate_config import active_generate_config_context_var

    from src.swarm import tool_output_caps as caps
    assert caps.truncate_tool_output("submit", "The final answer is 42", 0) is None
    assert caps.truncate_tool_output("submit", BIG, -1) is None and middle_cut(BIG, 0) is None
    token = active_generate_config_context_var.set(GenerateConfig(max_tool_output=0))
    try:
        assert caps.run_cap() == 0 and caps.truncate_tool_output("bash", BIG, None) is None
        assert caps.cap_of(config(tool_output_bytes=None)) == caps.NO_CAP
    finally:
        active_generate_config_context_var.reset(token)
    assert caps.run_cap() == 16 * 1024          # nothing set: Inspect's default


def test_the_sandbox_bridge_takes_the_same_cut():
    """The bridge's host tools import the truncation by name; install() replaces that name too."""
    import inspect_ai.agent._bridge.sandbox.host_tool as host_tool

    from src.swarm import tool_output_caps as caps
    assert host_tool.truncate_tool_output is caps.truncate_tool_output is call_tools.truncate_tool_output


def test_the_task_carries_the_cap_for_every_entry_point():
    """swarm_task sets max_tool_output in the task's own GenerateConfig, so `inspect eval src/tasks.py@...` gets it."""
    from src.configuration.presets import build_config
    from src.domains.swe import SWE
    from src.tasks import swarm_task
    from tests.unit.test_swe_task import ISSUE
    task = swarm_task(SWE(ISSUE), build_config("default", "small", ["n_agents=1"]))
    assert task.config.max_tool_output == TOOL_OUTPUT_BYTES


def test_a_long_ids_list_stays_under_the_cap_and_bad_ids_are_rows():
    """20,000 asked ids, most past the teammate's calls: the answer stays under the cap, counts what it left out in
    not_shown and names as many as fit; a bad first id is a row "no such call", never a Python error."""
    calls_ = called(3, "y" * 100)
    many = list(range(20_000))
    for payload in (oversight.observations_payload("a01", calls_, many, 20, 16000, budget=65536),
                    oversight.actions_payload("a01", calls_, 20, None, ids=many, budget=65536)):
        rows = payload.get("observations", payload.get("actions"))
        assert len(oversight._dump(payload).encode()) <= 65536, len(oversight._dump(payload).encode())
        assert [r["id"] for r in rows[:4]] == [0, 1, 2, 3]
        assert rows[3] == {"agent": "a01", "id": 3, "status": "no such call"}
        assert payload["not_shown"] == 20_000 - len(rows) and payload["not_shown_ids"] == many[len(rows):][
            :len(payload["not_shown_ids"])] and payload["not_shown_ids"]
    for budget in (65536, 64):
        bad = oversight.actions_payload("a01", calls_, 20, None, ids=[99, 0], budget=budget)
        assert bad["actions"][0] == {"agent": "a01", "id": 99, "status": "no such call"}, bad
        assert oversight.observations_payload("a01", calls_, [99], 20, 16000, budget=budget)["observations"][0] == \
            {"agent": "a01", "id": 99, "status": "no such call"}
