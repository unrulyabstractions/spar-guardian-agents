"""The results browser offline: the run rows and their aggregate block, the team records beside the episodes' agent
rows, the episodes' counted verdicts, the timeline's code-ladder moments and its submit and stop kinds on a synthetic
run, the server's routes and path safety, and the access log on the run page, rendered by the page's own code."""
from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.ui._eval_log_views import LogViews
from src.ui._run_store import Store
from src.ui._server import make_server
from src.utils.json_files import read_jsonl, write_jsonl

T0 = datetime(2026, 10, 30, tzinfo=timezone.utc)
CALL_IDS = {"a01": ["c1", "c2", "c3"], "a02": ["d1", "d2"]}
# The code ladder's team record of the board run's one sample.
TEAM = {"row": "team", "sample": "s#1", "task": "openmath_agent", "agents": ["a01", "a02"], "rung": "opened",
        "rung_by_code": "opened", "collaboration": "discussed", "passed": None, "ended_by": "submit"}


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
        {"row": "agent", "sample": "s#1", "episode": "s#1@a01", "agent": "a01", "rung": "opened", "exposed_call": 0,
         "opened_call": 2},
        {"row": "agent", "sample": "s#1", "episode": "s#1@a02", "agent": "a02", "rung": "exposed", "exposed_call": 1,
         "opened_call": None},
        TEAM,
    ])
    write_jsonl(run / "ladder.jsonl", [
        {"row": "agent", "sample": "s#1", "episode": "s#1@a01", "rung": "used", "raw": "bookkeeping"},
        {"row": "team", "sample": "s#1", "view": "full", "presentation": None, "pushback": False},
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
    assert row["team"] == {"rung": "opened", "collaboration": "discussed"}
    assert store.run_row("failed_board_run")["team"] is None        # the code ladder never ran there


def test_the_run_detail_carries_every_team_record_and_no_episode_takes_a_team_row(runs):
    store = Store(runs)
    assert store.run_detail("board_run")["team"] == [TEAM]
    eps = store.episodes("board_run")
    assert [e["signals"]["rung"] for e in eps] == ["opened", "exposed"]
    assert eps[0]["ladder"] == {"rung": "used"} and eps[1]["ladder"] is None


def test_episodes_count_a_verdict_only_with_a_transcript_and_drop_the_judges_bookkeeping(runs):
    store = Store(runs)
    eps = store.episodes("board_run")
    assert [e["counted"] for e in eps] == [True, True]
    assert "judge_prompt" not in eps[0]["verdict"] and eps[0]["verdict"]["summary"] is None
    assert eps[1]["signals"] == {"rung": "exposed", "exposed_call": 1, "opened_call": None}
    failed = store.run_row("failed_board_run")
    assert failed["failed"] and failed["episodes"][0]["counted"] is False
    assert failed["endings"] == {"unrecorded": 1}       # an episode that records no ending is not counted as "end"
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


# The access log on the run page. tests/support/render_run_page.mjs runs the page's own code on what the API answers.
RENDER = Path(__file__).parents[1] / "support" / "render_run_page.mjs"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node renders the page")
LOG = {"status": "partial", "mechanism": "fanotify", "origins": {"agent": 3, "control": 4, "harness": 2},
       "control": {"start": {"seen": 2, "missing": []}, "end": {"seen": 1, "missing": []}},
       "gaps": [{"t": 5.0, "row": "object_gone", "machine": "a02", "object": "key", "path": "/opt/key"}],
       "objects": 2, "gone": ["a02:key"], "packed": ["a02:fix"], "dropped": 7, "refused_reads": "not_recorded",
       "harness_reads": {"count": 2, "own": 1,
                         "commands": [{"cmd": "git cat-file -p x:f", "root_cmd": "git", "rows": 2}]},
       "error": None}
AGENT_KEYS = {"status": "partial", "objects": ["key"], "first_call": 0, "refused_seen": 0}
AGENT_LOGS = {"a01": {**AGENT_KEYS, "records": 3, "raised": ["tried"], "log_only": True, "transcript_only": False,
                      "incidental": 1},
              "a02": {**AGENT_KEYS, "records": 0, "raised": [], "log_only": False, "transcript_only": False,
                      "incidental": 0}}
TEAM_LOG = {"status": "partial", "log_only": ["a01: [0] access log: key by python3 -"], "transcript_only": [],
            "team_only": [], "rung_without_log": "exposed", "rests_on_log": True}


def keep_log(folder: Path, block: dict, *, team: dict | None = TEAM_LOG) -> None:
    """The run as a run with this log keeps it: run.json's block, each agent's key and the team's."""
    run = json.loads((folder / "run.json").read_text())
    (folder / "run.json").write_text(json.dumps({**run, "access_log": block}))
    rows = [{**row, "access_log": AGENT_LOGS[row["agent"]]} if row["row"] == "agent" else {**row, "access_log": team}
            for row in read_jsonl(folder / "signals.jsonl")]
    write_jsonl(folder / "signals.jsonl", rows)


def page_of(runs: Path) -> list[str]:
    """The lines of the board run's page, from the API's answer."""
    detail = json.dumps(Store(runs).run_detail("board_run"), default=str)
    done = subprocess.run(["node", str(RENDER)], input=detail, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return done.stdout.splitlines()


def after(lines: list[str], label: str) -> str:
    """What follows a label on the page."""
    return lines[lines.index(label) + 1]


@needs_node
def test_a_run_with_no_log_has_no_access_log_on_its_page(runs):
    assert "Access log" not in page_of(runs)


@needs_node
def test_the_run_page_shows_the_logs_status_and_counts_and_who_only_it_saw(runs):
    keep_log(runs / "board_run", LOG)
    lines = page_of(runs)
    assert "Access log" in lines and after(lines, "status") == "partial"
    assert after(lines, "rows by origin") == "3 agent, 4 control, 2 harness"
    assert (after(lines, "objects marked"), after(lines, "controls seen")) == ("2", "start 2, end 1")
    assert (after(lines, "gaps"), after(lines, "objects gone"), after(lines, "rows dropped")) == ("1", "1", "7")
    assert after(lines, "agents only the log saw") == "a01"
    assert after(lines, "only the log saw") == "a01: [0] access log: key by python3 -"


@needs_node
def test_the_run_page_says_when_the_rung_rests_on_the_log(runs):
    keep_log(runs / "board_run", LOG)
    lines = page_of(runs)
    assert after(lines, "rung and the log") == "rests on the log alone" and " exposed without it" in lines


@needs_node
def test_a_rung_that_stands_without_the_log_shows_no_such_line(runs):
    keep_log(runs / "board_run", LOG, team={**TEAM_LOG, "rests_on_log": False, "rung_without_log": "opened"})
    assert "rung and the log" not in page_of(runs)


@needs_node
@pytest.mark.parametrize(("reads", "marked"), [({"count": 2, "own": 1}, True), ({"count": 2, "own": 2}, False)])
def test_harness_reads_beyond_its_own_mark_the_run_to_read(runs, reads, marked):
    keep_log(runs / "board_run", {**LOG, "harness_reads": {**LOG["harness_reads"], **reads}})
    lines = page_of(runs)
    assert ("read this run" in lines) is marked and "git cat-file -p x:f" in lines
    assert after(lines, "harness reads") == f"2 of content after the first turn, {reads['own']} the harness's own"


@needs_node
def test_a_run_with_no_harness_reads_says_zero(runs):
    keep_log(runs / "board_run", {**LOG, "harness_reads": {"count": 0, "own": 0, "commands": []}})
    assert after(page_of(runs), "harness reads") == "0"


@needs_node
@pytest.mark.parametrize("block", [
    {"status": "off"},
    {"status": "host", "mechanism": "host", "origins": {"agent": 2}, "refused_reads": "recorded"},
    {"status": "absent", "mechanism": "fanotify", "refused_reads": "not_recorded", "error": "no daemon"},
    {"status": "valid", "mechanism": "fanotify", "origins": {}, "objects": 0, "gaps": [], "gone": [], "packed": [],
     "control": {"start": {"seen": 0, "missing": []}, "end": {"seen": 0, "missing": []}}, "dropped": 0,
     "refused_reads": "not_recorded", "harness_reads": {"count": 0, "own": 0, "commands": []}, "error": None},
])
def test_every_status_renders_with_the_keys_it_has(runs, block):
    keep_log(runs / "board_run", block)
    assert after(page_of(runs), "status") == block["status"]


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
    "/static/../_server.py",
    "/static/%2e%2e/_server.py",
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
