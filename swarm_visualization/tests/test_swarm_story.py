"""The story built from a small hand-made run: the clock, who talks to whom, where a piece goes and the rungs."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swarm_visualization.src.page_builder import build_page  # noqa: E402
from swarm_visualization.src.run_files import RunFiles  # noqa: E402
from swarm_visualization.src.swarm_story import build_story  # noqa: E402

T0 = 1_000_000.0


def iso(seconds: float) -> str:
    return datetime.fromtimestamp(T0 + seconds, tz=timezone.utc).isoformat()


def call(cid: str, tool: str, args: dict | None = None) -> dict:
    return {"tool_call_id": cid, "function_name": tool, "arguments": args or {}}


def write_run(folder: Path) -> Path:
    processed, analysis = folder / "processed", folder / "analysis"
    for p in (processed / "trajectories" / "s@aaaaa", processed / "trajectories" / "s@bbbbb",
              processed / "communication", processed / "facts", analysis / "labels"):
        p.mkdir(parents=True)
    calls = {"aaaaa": [call("a0", "shell"), call("a1", "board_post", {"message": "x"}), call("a2", "shell")],
             "bbbbb": [call("b0", "board_read", {"from_agent": "aaaaa"}), call("b1", "alert_humans")]}
    timeline = [{"agent": agent, "calls": [{"id": c["tool_call_id"], "t0": T0 + 10 * k + (agent == "bbbbb"),
                                            "t1": T0 + 10 * k + 2 + (agent == "bbbbb")}
                                           for k, c in enumerate(rows)]}
                for agent, rows in calls.items()]
    (processed / "run.json").write_text(json.dumps({
        "agents": ["aaaaa", "bbbbb"], "rules": {"timeline": timeline, "entry_rounds": {"aaaaa": 1, "bbbbb": 1}},
        "access": {"agents": {"aaaaa": {"holds": ["A"]}, "bbbbb": {"holds": ["B"]}}},
        "team_submission": {"agent": "aaaaa"},
        "alerts": [{"agent": "bbbbb", "agents": ["aaaaa"], "reason": "aaaaa used the leaked files"}],
        "unsubmits": {"submits": [{"agent": "aaaaa", "time": iso(15)}],
                      "unsubmits": [{"agent": "bbbbb", "time": iso(16), "reason": "aaaaa cheated"}]},
        "model": "anthropic/claude-opus-5-5",
        "role_models": {"regulator": "openrouter/deepseek/deepseek-v4.1-flash-1017"}}))
    (processed / "facts" / "topology.json").write_text(json.dumps(
        {"agents": [{"agent": "aaaaa", "role": "worker"}, {"agent": "bbbbb", "role": "regulator"}]}))
    episodes = []
    for agent, rows in calls.items():
        transcript = {"trajectory": {"steps": [{"source": "agent", "tool_calls": rows}]}}
        (processed / "trajectories" / f"s@{agent}" / "transcript.json").write_text(json.dumps(transcript))
        episodes.append({"agent": agent, "transcript_path": f"trajectories/s@{agent}/transcript.json",
                         "ended_by": "stop_working"})
    (processed / "episodes.jsonl").write_text("\n".join(json.dumps(e) for e in episodes))
    (processed / "communication" / "s.json").write_text(json.dumps({
        "messages": [{"id": "m1", "sender": "aaaaa", "content": "aaaaa to bbbbb: here is my part"}],
        "events": [{"kind": "sent", "agent_id": "aaaaa", "tool_call_id": "a1", "message_ids": ["m1"]}]}))
    (analysis / "signals.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"row": "agent", "agent": "bbbbb", "pieces_seen_at": {"A": 1, "B": 0}},
        {"row": "agent", "agent": "aaaaa", "pieces_seen_at": {"A": 0}},
        {"row": "team", "alert_outcomes": [{"outcome": "true_alarm"}]}]))
    (analysis / "labels" / "cheating.json").write_text(json.dumps({"team": "hacked", "passed": True, "agents": {
        "aaaaa": {"rung": "hacked", "timeline": {"after": ["none", "looked", "attempted"]}},
        "bbbbb": {"rung": "looked", "timeline": {"after": ["looked", "looked"]}}}}))
    return folder


def test_story(tmp_path: Path) -> None:
    story = build_story(RunFiles(write_run(tmp_path / "run")))
    assert story["duration"] == 22.0                       # a2 ends at 20 + 2 after the first call's start
    a, b = story["agents"]
    assert (a["role"], b["role"]) == ("worker", "regulator")
    assert a["holds"] == [0] and b["holds"] == [1]
    assert (a["label"], b["label"]) == ("W1", "R1")
    assert (a["model"], b["model"]) == ("Opus", "DeepSeek")
    assert story["piece_names"] == ["A", "B"]
    # The post's text with agent IDs replaced by labels.
    assert story["said"] == [[10.0, 0, "W1 to R1: here is my part"]]
    # The post at a1 (t=10) names bbbbb; the read at b0 (t=1) asks for aaaaa's posts.
    assert sorted(story["talk"]) == [[1.0, 1, 0, 1], [10.0, 0, 1, 0]]
    assert story["posts"] == [[10.0, 0]]
    # bbbbb saw piece A at its call 1 (ending at 13), from its only holder; its own piece B is no flow.
    assert story["flows"] == [[13.0, 0, 0, 1]]
    # aaaaa: looked after call 1 (t=12), attempted after call 2 (t=22), hacked at the run's end.
    assert a["rungs"] == [[0.0, 0], [12.0, 1], [22.0, 3], [22.0, 4]]
    assert b["rungs"] == [[0.0, 0], [3.0, 1]]
    # bbbbb's alert at its alert_humans call (b1, t=11) names aaaaa; the labels call it a true alarm.
    assert story["alerts"] == [[11.0, 1, [0], 1, "W1 used the leaked files"]]
    # A hand-in by aaaaa at 15 s and bbbbb taking it back at 16 s, on the story's clock.
    assert story["handins"] == [[15.0, 0, 0, ""], [16.0, 1, 1, "W1 cheated"]]
    assert story["submit"]["agent"] == 0 and story["submit"]["cheated"] and story["submit"]["passed"]


def test_page_inlines_everything(tmp_path: Path) -> None:
    story = build_story(RunFiles(write_run(tmp_path / "run")))
    page = build_page(story)
    assert "__STORY__" not in page and "__JS__" not in page and "__THREE__" not in page
    assert "data:text/javascript;base64," in page
    assert '"run":"run"' in page
