"""The replay's recorded calls: attachments resolved, the transcripts' agent and k joined to the eval log's events, and
one global order that never reorders an agent's calls. No Docker."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from inspect_ai.event import ToolEvent
from inspect_ai.log import EvalDataset, EvalLog, EvalSample, EvalSpec, write_eval_log
from inspect_ai.tool import ToolCallError

from src.measures.run_directory import RunDir
from src.replay.recorded_calls import ReplayRefused, merge_by_start, recorded_calls, resolve, result_text
from tests.support.atif_builder import transcript
from tests.support.run_builder import write_run_dir


def moment(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, timezone.utc)


def write_log(path, calls: list[tuple[str, str, dict, str, float, float, str | None]]):
    """An .eval log of one sample with a tool event per (id, tool, arguments, result, start, end, error type)."""
    events = [ToolEvent(id=cid, function=tool, arguments=args, result=result, timestamp=moment(t0),
                        completed=moment(t1), error=ToolCallError(error, "x") if error else None)
              for cid, tool, args, result, t0, t1, error in calls]
    spec = EvalSpec(created="2026-11-01T19:00:00+00:00", task="swe", dataset=EvalDataset(), model="mockllm/model",
                    config={})
    write_eval_log(EvalLog(eval=spec, samples=[EvalSample(id="flipt-02e2", epoch=1, input="", target="",
                                                          events=events)]), str(path))
    return path


def test_resolve_replaces_attachments_at_any_depth():
    atts = {"h1": "git status", "h2": "out"}
    assert resolve({"command": "attachment://h1", "x": ["attachment://h2", 3]}, atts) == \
        {"command": "git status", "x": ["out", 3]}
    assert resolve("attachment://missing", atts) == "attachment://missing"
    assert result_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "ab"
    assert result_text(None) == ""


def test_merge_keeps_each_agents_order_and_sorts_by_start():
    per_agent = {"b": [(1.0, 0, "b0"), (5.0, 1, "b1")],
                 "a": [(2.0, 0, "a0"), (0.5, 1, "a1"), (6.0, 2, "a2")]}     # a1 started before a0 on the clock
    order = merge_by_start(per_agent)
    assert order == ["b0", "a0", "a1", "b1", "a2"]
    assert [x for x in order if x.startswith("a")] == ["a0", "a1", "a2"]


def test_merge_breaks_ties_by_agent_then_k():
    assert merge_by_start({"z": [(1.0, 0, "z0")], "a": [(1.0, 0, "a0"), (1.0, 1, "a1")]}) == ["a0", "a1", "z0"]


def test_recorded_calls_join_transcripts_and_events(tmp_path):
    log = write_log(tmp_path / "x.eval", [
        ("c0", "shell", {"command": "git status"}, "On branch a01", 100.0, 101.0, None),
        ("c1", "board_read", {}, "no posts", 103.0, 103.5, None),
        ("c0b", "shell", {"command": "ls"}, "go.mod", 100.5, 102.0, None),
        ("c1b", "run_python", {"description": "x"}, "", 104.0, 104.1, "parsing")])
    a = transcript([("shell", "git status", "On branch a01"), ("board_read", {}, "no posts")])
    b = transcript([("shell", "ls", "go.mod"), ("run_python", {"description": "x"}, "refused")])
    for k, step in enumerate(s for s in b["trajectory"]["steps"] if s["source"] == "agent"):
        step["tool_calls"][0]["tool_call_id"] = f"c{k}b"
        step["observation"]["results"][0]["source_call_id"] = f"c{k}b"
    folder = write_run_dir(tmp_path / "run", {"task": "software_engineering", "eval_log": str(log)},
                           {"a01": a, "b02": b}, row="flipt-02e2")
    calls = recorded_calls(RunDir(folder))
    assert [(c.n, c.agent, c.k, c.id) for c in calls] == [(1, "a01", 0, "c0"), (2, "b02", 0, "c0b"),
                                                          (3, "a01", 1, "c1"), (4, "b02", 1, "c1b")]
    assert calls[0].arguments == {"command": "git status"} and calls[0].result == "On branch a01"
    assert (calls[0].t0, calls[0].t1) == (100.0, 101.0)
    assert calls[3].error == "parsing" and calls[3].result == "refused"   # the transcript's text where the log has none


def test_a_call_without_an_event_or_a_run_without_a_log_is_refused(tmp_path):
    log = write_log(tmp_path / "x.eval", [("c0", "shell", {"command": "ls"}, "", 1.0, 2.0, None)])
    two = transcript([("shell", "ls", ""), ("shell", "pwd", "/app")])
    folder = write_run_dir(tmp_path / "run", {"eval_log": str(log)}, {"a01": two})
    with pytest.raises(ReplayRefused, match="c1"):
        recorded_calls(RunDir(folder))
    (folder / "run.json").write_text(json.dumps({}))
    with pytest.raises(ReplayRefused, match="no eval log"):
        recorded_calls(RunDir(folder))


def test_a_call_the_turn_rules_refused_never_ran(tmp_path):
    from src.replay.recorded_calls import NEVER_RAN, REFUSED
    from src.replay.swe_replayer import SweReplayer
    log = write_log(tmp_path / "x.eval", [
        ("c0", "shell", {"command": "ls"}, "go.mod", 1.0, 2.0, None),
        ("c1", "shell", {"command": "rm -rf /tmp/x"}, "No tool calls are left in this round: a round allows 1.", 2.0,
         2.0, "unknown")])
    calls = transcript([("shell", "ls", "go.mod"), ("shell", "rm -rf /tmp/x", "No tool calls are left")])
    for k, step in enumerate(s for s in calls["trajectory"]["steps"] if s["source"] == "agent"):
        step["tool_calls"][0]["tool_call_id"] = f"c{k}"
        step["observation"]["results"][0]["source_call_id"] = f"c{k}"
    rules = {"timeline": [{"agent": "a01", "round": 1, "calls": [{"id": "c0", "refused": None},
                                                                  {"id": "c1", "refused": "spent"}]}]}
    folder = write_run_dir(tmp_path / "run", {"task": "software_engineering", "eval_log": str(log), "rules": rules},
                           {"a01": calls}, row="flipt-02e2")
    got = recorded_calls(RunDir(folder))
    assert [c.error for c in got] == [None, REFUSED] and REFUSED in NEVER_RAN
    replayer = SweReplayer.__new__(SweReplayer)
    replayer.jobs = None
    assert [replayer.in_sandbox(c) for c in got] == [True, False]
