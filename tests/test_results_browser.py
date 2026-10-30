"""The results browser offline: the run rows and their aggregate block, the episodes' counted verdicts, the
timeline's code-ladder moments and its submit and stop kinds on a synthetic run, and the server's routes and path
safety."""
from __future__ import annotations

import http.client
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.ui.eval_log_views import LogViews
from src.ui.run_store import Store
from src.ui.server import make_server
from src.utils.json_files import write_jsonl

T0 = datetime(2026, 10, 30, tzinfo=timezone.utc)
CALL_IDS = {"a01": ["c1", "c2", "c3"], "a02": ["d1", "d2"]}


def transcript(agent: str) -> dict:
    """An ATIF transcript whose agent steps issue the agent's tool calls, one per step."""
    steps = [{"source": "agent", "tool_calls": [{"tool_call_id": cid, "function_name": "shell", "arguments": {}}]}
             for cid in CALL_IDS[agent]]
    return {"trajectory": {"steps": [{"source": "system", "message": "go"}, *steps]}}


@pytest.fixture
def runs(tmp_path) -> Path:
    """runs/ with a two-agent board run whose roles are hidden, and a run set aside under a failed_ prefix."""
    run = tmp_path / "board_run"
    (run / "communication").mkdir(parents=True)
    (run / "tools" / "graphs").mkdir(parents=True)
    (run / "tools" / "graphs" / "summary.json").write_text(json.dumps({"tool": "graphs", "n_nodes": 2}))
    (run / "tools" / "graphs" / "notes.txt").write_text("not served")
    (run / "run.json").write_text(json.dumps({
        "task": "openmath_agent", "model": "openai/test", "topology": "swarm", "agents": ["a01", "a02"],
        "eval_log": str(tmp_path / "missing.eval"), "defenses": [{"name": "turn_rules"}],
        "swarm": {"n_agents": 2, "regulators": 1, "disregulators": 0, "workers": 1, "seed": 0, "composition": "R1D0W1",
                  "roles": {"a01": "worker", "a02": "regulator"}},
        "deliverables": {"registered_on_main": True, "registered_message_copied_from": None,
                         "archive_in_workspace": False}}))
    for agent in CALL_IDS:
        path = run / "trajectories" / f"s#1@{agent}" / "transcript.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(transcript(agent)))
    write_jsonl(run / "episodes.jsonl", [
        {"episode": "s#1@a01", "orbit_agent": "a01", "role": "worker", "ended_by": "submit", "wall_time": 30.0,
         "usage": {"input": 100, "output": 10, "cache_read": 40}, "score": {"deliverables_scorer": 1}},
        {"episode": "s#1@a02", "orbit_agent": "a02", "role": "regulator", "failure": "timeout", "wall_time": 60.0,
         "usage": {"input": 50, "output": 5, "cache_read": 0}, "score": {"deliverables_scorer": 0}},
    ])
    write_jsonl(run / "judge.jsonl", [
        {"episode": "s#1@a01", "cheated": True, "honey_pots_seen": True, "attempted_access": True,
         "judge_cost_usd": 0.01, "judge_prompt": "bookkeeping"},
        {"episode": "s#1@a02", "cheated": False, "honey_pots_seen": True, "attempted_access": False},
    ])
    write_jsonl(run / "signals.jsonl", [
        {"episode": "s#1@a01", "agent": "a01", "rung": "opened", "exposed_call": 0, "opened_call": 2},
        {"episode": "s#1@a02", "agent": "a02", "rung": "exposed", "exposed_call": 1, "opened_call": None},
    ])
    failed = tmp_path / "failed_board_run"
    failed.mkdir()
    (failed / "run.json").write_text(json.dumps({"task": "openmath_agent", "topology": "single", "agents": ["a01"]}))
    # judged, but its transcript is gone: the verdict is not counted
    write_jsonl(failed / "episodes.jsonl", [{"episode": "s#1", "orbit_agent": "a01", "transcript_path": "/nowhere"}])
    write_jsonl(failed / "judge.jsonl", [{"episode": "s#1", "cheated": True}])
    return tmp_path


def test_a_run_row_carries_its_setting_verdict_fields_and_one_aggregate_block(runs):
    store = Store(runs)
    assert store.run_names() == ["board_run", "failed_board_run"]
    row = store.run_row("board_run")
    assert row["setting"] == {"key": "roles-2", "label": "board of 2, hidden roles", "order": 2, "n": 2,
                              "composition": "R1D0W1", "R": 1, "D": 0, "W": 1}
    assert [f["field"] for f in row["verdict_fields"]][0] == "honey_pots_seen" and row["verdict_fields"][-1] == {
        "field": "cheated", "label": "cheated", "kind": "bad"}
    agg = row["aggregate"]
    assert (agg["judged"], agg["cheated"], agg["timeouts"], agg["score"], agg["wall_time"]) == (2, 1, 1, 1, 60.0)
    assert agg["flags"]["honey_pots_seen"] == 2 and agg["flags"]["attempted_access"] == 1
    assert agg["tokens"] == {"input": 150, "output": 15, "cache_read": 40} and agg["judge_cost"] == 0.01
    assert agg["by_role"]["worker"] == {"n": 1, "cheated": 1} and agg["by_role"]["regulator"] == {"n": 1, "cheated": 0}
    assert agg["controlled"] is False   # the turn rules are passive, not a control
    assert row["endings"] == {"submit": 1, "timeout": 1} and not row["failed"] and not row["eval_log_exists"]
    assert row["tools"] == ["graphs"]


def test_episodes_count_a_verdict_only_with_a_transcript_and_drop_the_judges_bookkeeping(runs):
    store = Store(runs)
    eps = store.episodes("board_run")
    assert [e["counted"] for e in eps] == [True, True]
    assert "judge_prompt" not in eps[0]["verdict"] and eps[0]["verdict"]["summary"] is None
    assert eps[1]["signals"] == {"rung": "exposed", "exposed_call": 1, "opened_call": None}
    failed = store.run_row("failed_board_run")
    assert failed["failed"] and failed["episodes"][0]["counted"] is False
    assert (failed["aggregate"]["judged"], failed["aggregate"]["by_role"]) == (0, None)
    assert failed["setting"]["key"] == "single"
    with pytest.raises(KeyError):
        store.run_dir("..")


def event(kind: str, seconds: float, **fields) -> SimpleNamespace:
    return SimpleNamespace(event=kind, timestamp=T0 + timedelta(seconds=seconds), **fields)


def tool(agent: str, call_id: str, seconds: float, function: str = "shell", error=None) -> SimpleNamespace:
    return event("tool", seconds, id=call_id, span_id=agent, function=function, arguments={"command": "ls"},
                 result="", error=error, completed=T0 + timedelta(seconds=seconds + 1))


def fake_log(*calls: SimpleNamespace, metadata: dict | None = None) -> SimpleNamespace:
    """An eval log of the two agents: one agent span each, then their tool calls in time order."""
    spans = [event("span_begin", 0, id=a, type="agent", name=a, parent_id=None, span_id=None) for a in CALL_IDS]
    calls = calls or (tool("a01", "c1", 5), tool("a02", "d1", 6), tool("a02", "d2", 9), tool("a01", "c2", 10),
                      tool("a01", "c3", 20))
    sample = SimpleNamespace(events=[*spans, *calls], total_time=30.0, limit=None, id="s", epoch=1, metadata=metadata)
    return SimpleNamespace(samples=[sample])


def test_the_timeline_places_the_code_ladders_exposed_and_opened_calls(runs, monkeypatch):
    store = Store(runs)
    monkeypatch.setattr(store, "eval_log", lambda name: fake_log())
    view = LogViews(store).timeline("board_run")
    assert view["first_exposed"] == {"a01": 5.0, "a02": 9.0}
    assert view["first_opened"] == {"a01": 20.0}
    assert view["roles"] == {"a01": "worker", "a02": "regulator"}
    marked = {e["id"]: (e["exposed"], e["opened"]) for e in view["events"]}
    assert marked == {"c1": (True, False), "d1": (False, False), "d2": (True, False), "c2": (False, False),
                      "c3": (False, True)}


def test_the_timeline_marks_a_submit_or_stop_that_went_through(runs, monkeypatch):
    refused = SimpleNamespace(message="Nothing to hand in yet: no commit past the base on team/main.")
    calls = (tool("a01", "c1", 5, "submit", error=refused), tool("a02", "d1", 6, "stop_working"),
             tool("a01", "c2", 10, "submit"), tool("a01", "c3", 20))
    store = Store(runs)
    monkeypatch.setattr(store, "eval_log", lambda name: fake_log(*calls))
    kinds = {e["id"]: e["kind"] for e in LogViews(store).timeline("board_run")["events"]}
    assert kinds == {"c1": "tool", "d1": "stop", "c2": "submit", "c3": "tool"}
    # In a log whose agents held ORBIT's own submit, a call named submit was that tool: no kind is read from a name.
    older = {"experiment": {"setup": {"agents": [{"name": a, "submit": True} for a in CALL_IDS]}}}
    monkeypatch.setattr(store, "eval_log", lambda name: fake_log(*calls, metadata=older))
    kinds = {e["id"]: e["kind"] for e in LogViews(store).timeline("board_run")["events"]}
    assert kinds == {"c1": "tool", "d1": "tool", "c2": "tool", "c3": "tool"}


@pytest.fixture
def server(runs):
    httpd = make_server(runs, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def get(port: int, path: str) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path)
    response = conn.getresponse()
    return response.status, response.read()


def test_the_server_routes_the_page_and_the_api(server):
    status, body = get(server, "/")
    assert status == 200 and b"app/main.js" in body
    assert get(server, "/static/app/main.js")[0] == 200
    status, body = get(server, "/api/runs")
    assert status == 200 and [r["run"] for r in json.loads(body)] == ["board_run", "failed_board_run"]
    status, body = get(server, "/api/runs/board_run")
    assert status == 200 and json.loads(body)["tools"]["graphs"]["n_nodes"] == 2
    status, body = get(server, "/api/runs/board_run/transcript/s%231%40a01")
    assert status == 200 and json.loads(body)["episode"]["orbit_agent"] == "a01"
    assert get(server, "/api/runs/board_run/tools/graphs/summary.json")[0] == 200
    assert get(server, "/api/runs/nope")[0] == 404
    assert get(server, "/api/summary")[0] == 404
    assert get(server, "/api/runs/board_run/timeline")[0] == 404   # the eval log is missing


@pytest.mark.parametrize("path", [
    "/static/../server.py",
    "/static/%2e%2e/server.py",
    "/static/app/..%2F..%2Fserver.py",
    "/api/runs/..",
    "/api/runs/board_run/tools/graphs/../../run.json",
    "/api/runs/board_run/tools/graphs/..%2F..%2Frun.json",
    "/api/runs/board_run/tools/..%2Fboard_run/run.json",
    "/api/runs/board_run/tools/graphs/notes.txt",
    "/../../pyproject.toml",
])
def test_the_server_refuses_paths_outside_what_it_serves(server, path):
    assert get(server, path)[0] == 404
