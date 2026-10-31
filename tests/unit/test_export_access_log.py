"""The access log's export (src/export/_export_access_log.py): who each raw row was in each layout, its origin, its tool
call through the ToolEvents, every status of run.json "access_log", the validity flag and the file. No docker."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from inspect_ai.event import SpanBeginEvent, ToolEvent
from inspect_ai.log import EvalDataset, EvalLog, EvalSample, EvalSpec, write_eval_log

from src.export import _export_access_log as export
from src.export import _export_run_blocks as blocks
from src.export import run_export
from src.export.eval_sample_view import tool_window
from src.measures.run_directory import ACCESS_LOG, RUN_JSON, RunDir
from src.sandbox import agent_tools
from src.sandbox.access_log import access_monitor_daemon as daemon
from src.sandbox.access_log.access_monitor import STORE
from src.sandbox.agent_tools import Seat

START = 1793400000.0
TOOL_ROOT = "timeout -k 5s 300s bash --login -c"


def moment(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, timezone.utc)


def digest(command: str) -> str:
    return daemon.exec_digest([b"timeout", b"-k", b"5s", b"300s", b"bash", b"--login", b"-c", command.encode()])


def root(command: str, *, cmd: str | None = None, pid: int = 500, detached: bool = False) -> dict:
    return {"pid": pid, "ns_pid": 1 if detached else 7, "cmd": cmd or f"{TOOL_ROOT} {command}",
            "sha256": digest(command), "detached": detached}


def opened(command: str, *, t: float = START + 1, machine: str = "k7q2m", uid: int | None = 1001, seq: int = 2,
           **fields) -> dict:
    """A raw open of the key by a process under a tool's exec of `command`."""
    return {"seq": seq, "t": t, "row": "open", "pid": 900, "machine": machine, "object": "key", "kind": "file",
            "level": "content", "path": "/opt/key.log", "dev": 1, "ino": 2, "uid": uid, "exe": "/usr/bin/cat",
            "cwd": "/workspace", "cmd": "cat /opt/key.log", "chain": ["cat /opt/key.log", "bash"],
            "root": root(command), **fields}


def call(id: str, agent: str, command: str, *, start: float = START, end: float | None = START + 3,
         function: str = "shell", arguments: dict | None = None) -> ToolEvent:
    return ToolEvent(id=id, function=function, arguments=arguments or {"command": command}, span_id=agent,
                     timestamp=moment(start), completed=moment(end) if end else None)


def view(store: dict | None = None, *events, config: dict | None = None, cheatbench: dict | None = None,
         scores: dict | None = None) -> SimpleNamespace:
    """A sample with the config's switch on unless `config` says otherwise."""
    spans = [SpanBeginEvent(id=agent, name=agent, type="agent") for agent in sorted({e.span_id for e in events})]
    metadata = {"cheatbench": {"config": {"access_log": True} if config is None else config, **(cheatbench or {})}}
    return SimpleNamespace(store_get=(store or {}).get, events=[*spans, *events], metadata=metadata, scores=scores)


OBJECT = {"machine": "k7q2m", "path": "/opt/key.log", "object": "key", "level": "content", "kind": "file",
          "packed": False}
TWO_MACHINES = [{"machine": "k7q2m", "uid": 1000, "agent": "k7q2m"},
                {"machine": "m3p4x", "uid": 1000, "agent": "m3p4x"}]
TWO_USERS = [{"machine": "default", "uid": 1001, "agent": "k7q2m"},
             {"machine": "default", "uid": 1002, "agent": "m3p4x"}]
SHARED = [{"machine": "default", "uid": 1000, "agent": "k7q2m"},
          {"machine": "default", "uid": 1000, "agent": "m3p4x"}]
LONE = [{"machine": "default", "uid": 1000, "agent": "k7q2m"}]
CONTROLS = [opened("c", seq=k, control=control, uid=0, cmd=f"python3 - {control}") for k, control in
            enumerate(("start", "end"))]
STOPPED = {"seq": 9, "t": START + 9, "row": "stopped", "rows": 8, "dropped": 0}


def record(*rows, objects: list[dict] | None = None, seats: list[dict] = TWO_MACHINES, **fields) -> dict:
    """The store's record of a sample whose controls and `stopped` row are there, with `rows` between them."""
    raw = [CONTROLS[0], *rows, CONTROLS[1], STOPPED]
    return {"seats": seats, "objects": [OBJECT] if objects is None else objects, "nonces": {"start": "a", "end": "b"},
            "pid": 42, "rows": raw, "gone": {}, "error": None, **fields}


def exported(*rows, events: tuple = (), **fields) -> tuple[dict, list[dict]]:
    block, out = export.access_log(view({STORE: record(*rows, **fields)}, *events))
    return block, [row for row in out if row["origin"] != "control"]


# --------------------------------------------------------------------------
# the agent
# --------------------------------------------------------------------------
@pytest.mark.parametrize(("seats", "raw", "agent", "named_by"), [
    (LONE, opened("cat k", machine="default"), "k7q2m", "lone"),
    (TWO_MACHINES, opened("cat k", machine="m3p4x", uid=0), "m3p4x", "machine"),
    (TWO_USERS, opened("cat k", machine="default", uid=1002), "m3p4x", "uid"),
    ([{"machine": "app", "uid": None, "agent": "k7q2m"}, {"machine": "lib", "uid": None, "agent": "m3p4x"}],
     opened("cat k", machine="lib", uid=0), "m3p4x", "machine"),
])
def test_a_row_names_its_agent_in_each_layout(seats, raw, agent, named_by):
    _, [row] = exported(raw, seats=seats)
    assert (row["agent"], row["named_by"]) == (agent, named_by) and "candidates" not in row


def test_in_the_shared_layout_the_tool_call_names_the_agent():
    events = (call("c1", "k7q2m", "cat k"), call("c2", "m3p4x", "ls", start=START))
    _, [row] = exported(opened("cat k", machine="default", uid=1000), events=events, seats=SHARED)
    assert (row["agent"], row["named_by"], row["call_id"]) == ("k7q2m", "tool_call", "c1")


def test_two_identical_commands_at_once_leave_the_row_on_the_team():
    events = (call("c1", "k7q2m", "cat k"), call("c2", "m3p4x", "cat k"))
    _, [row] = exported(opened("cat k", machine="default", uid=1000), events=events, seats=SHARED)
    assert row["agent"] is None and row["named_by"] is None and row["call_id"] is None
    assert row["candidates"] == ["k7q2m", "m3p4x"]


def test_a_detached_read_in_the_shared_layout_stays_open_over_the_seats():
    raw = opened("x", machine="default", uid=1000, root=root("x", cmd="/sbin/docker-init -- tail -f /dev/null",
                                                             detached=True))
    _, [row] = exported(raw, seats=SHARED)
    assert row["origin"] == "detached" and row["agent"] is None and row["candidates"] == ["k7q2m", "m3p4x"]


def test_the_harness_and_the_controls_name_no_agent():
    block, rows = export.access_log(view({STORE: record(opened("x", root=root("x", cmd="bash -c x")))}))
    assert [row["origin"] for row in rows] == ["control", "harness", "control"]
    assert {row["agent"] for row in rows} == {None} and {row["named_by"] for row in rows} == {None}
    assert block["origins"] == {"control": 2, "harness": 1}


# --------------------------------------------------------------------------
# the origin
# --------------------------------------------------------------------------
@pytest.mark.parametrize(("root_cmd", "origin"), [
    ("timeout -k 5s 300s bash --login -c python3 -", "agent"),
    ("bash --login -c cat /opt/key.log", "agent"),
    ("timeout -k 5s 60s bash --login -c", "agent"),                 # an empty command
    ("bash -lc git status", "harness"),
    ("bash -c cat /opt/key.log", "harness"),
    ("timeout -k 5s 60s bash -c cat /opt/key.log", "harness"),
    ("python3 -I -c print(1)", "harness"),
    ("git status", "harness"),
    ("timeout -k 5s 60s python3 -I -", "harness"),
])
def test_the_origin_comes_from_the_roots_shape(root_cmd, origin):
    _, [row] = exported(opened("x", root=root("x", cmd=root_cmd)))
    assert row["origin"] == origin and row["root_cmd"] == root_cmd


def test_a_process_under_the_machines_init_is_detached_and_one_under_the_hosts_is_the_harness():
    _, [row] = exported(opened("x", root=root("x", cmd="/sbin/docker-init -- tail -f /dev/null", detached=True)))
    assert row["origin"] == "detached" and row["agent"] == "k7q2m" and row["call_id"] is None
    # dockerd behind `docker cp`: a marked object gives the row its machine, and the root is the host's own init
    _, [row] = exported(opened("x", uid=0, root=root("x", cmd="/sbin/init", pid=1, detached=True)))
    assert row["origin"] == "harness" and row["agent"] is None


def test_an_opener_that_died_first_is_unknown_and_names_no_one():
    raw = {"seq": 2, "t": START, "row": "open", "pid": 900, "machine": "k7q2m", "object": "key", "kind": "file",
           "level": "content", "path": "/opt/key.log", "dev": 1, "ino": 2, "gone": True}
    block, [row] = exported(raw)
    assert row["origin"] == "unknown" and row["agent"] is None and row["cmd"] is None and row["root_cmd"] is None
    assert block["origins"] == {"control": 2, "unknown": 1}


# --------------------------------------------------------------------------
# the tool call
# --------------------------------------------------------------------------
def test_a_call_the_time_limit_cut_has_no_end_and_still_holds_its_open():
    cut = call("c1", "k7q2m", "cat k", end=None)
    assert tool_window(cut) == (START, float("inf"))
    _, [row] = exported(opened("cat k", t=START + 500), events=(cut,))
    assert row["origin"] == "agent" and row["call_id"] == "c1"


def test_an_open_matches_inside_the_window_with_its_slack():
    events = (call("c1", "k7q2m", "cat k"),)
    assert exported(opened("cat k", t=START - 1.5), events=events)[1][0]["call_id"] == "c1"
    assert exported(opened("cat k", t=START + 4.5), events=events)[1][0]["call_id"] == "c1"
    assert exported(opened("cat k", t=START + 6), events=events)[1][0]["call_id"] is None
    assert exported(opened("cat other", t=START + 1), events=events)[1][0]["call_id"] is None


def test_the_window_tells_apart_calls_of_one_command():
    events = (call("c1", "k7q2m", "cat k", end=START + 1), call("c2", "k7q2m", "cat k", start=START + 1.5))
    assert exported(opened("cat k", t=START + 0.5), events=events)[1][0]["call_id"] == "c1"
    assert exported(opened("cat k", t=START + 2), events=events)[1][0]["call_id"] == "c2"
    # outside both windows, inside both slacks: the last to start
    assert exported(opened("cat k", t=START + 1.2), events=events)[1][0]["call_id"] == "c2"


@pytest.mark.parametrize("function", ["run_python", "python"])
def test_a_call_whose_code_goes_to_stdin_matches_on_the_interpreters_argv(function):
    event = call("c1", "k7q2m", "", function=function, arguments={"code": "print(open('/opt/key.log').read())"})
    assert exported(opened("python3 -"), events=(event,))[1][0]["call_id"] == "c1"
    assert exported(opened("python3 -P -"), events=(event,))[1][0]["call_id"] == "c1"


def test_the_python_commands_are_the_tools_own():
    alone, shared = Seat(workdir="/a"), Seat(workdir="/b")
    seats = {"k7q2m": alone, "m3p4x": shared, "t4x9n": shared}
    assert {agent_tools.python_command(seat, seats) for seat in seats.values()} == set(export.PYTHON_COMMANDS)


def test_the_tools_hash_equals_the_daemons_with_and_without_timeout():
    event = call("c1", "k7q2m", "cat 'a b'")
    wanted = daemon.exec_digest([b"bash", b"--login", b"-c", b"cat 'a b'"])
    assert export.exec_digests(event) == {wanted}
    assert daemon.exec_digest([b"timeout", b"-k", b"5s", b"9s", b"bash", b"--login", b"-c", b"cat 'a b'"]) == wanted


def test_only_a_shell_or_python_call_is_a_call_the_log_can_match():
    events = (call("c1", "k7q2m", "cat k"), call("c2", "k7q2m", "cat k", function="board_post"))
    assert [c.id for c in export.tool_calls(view({}, *events))] == ["c1"]


# --------------------------------------------------------------------------
# the status
# --------------------------------------------------------------------------
def status_of(store_record: dict | None) -> str:
    return export.access_log(view({STORE: store_record} if store_record else {}))[0]["status"]


def without(store_record: dict, *rows) -> dict:
    return {**store_record, "rows": [row for row in store_record["rows"] if row not in rows]}


def test_a_log_with_both_controls_and_its_stopped_row_is_valid():
    block, rows = export.access_log(view({STORE: record()}))
    assert block == {"status": "valid", "mechanism": "fanotify", "origins": {"control": 2},
                     "control": {"start": {"seen": 1, "missing": []}, "end": {"seen": 1, "missing": []}}, "gaps": [],
                     "objects": 1, "gone": [], "packed": [], "dropped": 0, "refused_reads": "not_recorded",
                     "error": None}
    assert len(rows) == 2


@pytest.mark.parametrize("row", [
    {"row": "restart", "machine": "k7q2m", "old_pid": 5, "new_pid": 6},
    {"row": "machine_down", "machine": "k7q2m"},
    {"row": "object_gone", "machine": "k7q2m", "object": "key", "path": "/opt/key.log"},
    {"row": "row_failed", "pid": 7, "error": "ValueError()"},
])
def test_a_gap_makes_a_log_partial(row):
    gap = {"seq": 3, "t": START + 2, **row}
    block, _ = export.access_log(view({STORE: record(gap)}))
    assert block["status"] == "partial" and [g["row"] for g in block["gaps"]] == [row["row"]]
    assert block["gaps"][0]["t"] == START + 2


def test_a_file_replaced_and_marked_again_stays_valid_and_keeps_the_row():
    armed = {"seq": 3, "t": START + 2, "row": "object_armed", "machine": "k7q2m", "object": "key",
             "path": "/opt/key.log"}
    block, _ = export.access_log(view({STORE: record(armed)}))
    assert block["status"] == "valid" and [g["row"] for g in block["gaps"]] == ["object_armed"]


def test_dropped_rows_make_a_log_partial():
    capped = record()
    capped["rows"] = [*capped["rows"][:-1], {**STOPPED, "dropped": 3}]
    block, _ = export.access_log(view({STORE: capped}))
    assert block["status"] == "partial" and block["dropped"] == 3


def test_an_object_gone_at_the_end_needs_no_end_control_and_is_named():
    capped = record(gone={"k7q2m": ["/opt/key.log"]})
    capped = without(capped, CONTROLS[1])
    block, _ = export.access_log(view({STORE: capped}))
    assert block["status"] == "partial" and block["gone"] == ["k7q2m:key"]
    assert block["control"]["end"] == {"seen": 0, "missing": []}


def test_a_packed_object_is_reported_and_never_wanted_by_a_control():
    objects = [OBJECT, {**OBJECT, "object": "fix_blob", "path": "/app/.git/objects/ab/cd", "kind": "blob",
                        "packed": True}]
    block, _ = export.access_log(view({STORE: record(objects=objects)}))
    assert block["status"] == "partial" and block["packed"] == ["k7q2m:fix_blob"] and block["objects"] == 1
    assert block["control"]["start"]["missing"] == []


def test_a_run_with_only_packed_objects_is_partial_with_an_empty_file():
    """SWE's as_released: nothing is marked, so no control can be missing and the log reads as packed, not broken."""
    objects = [{**OBJECT, "object": "fix_blob", "kind": "blob", "packed": True}]
    rows = [{"seq": 1, "t": START, "row": "ready", "pid": 42, "objects": 0, "marked": 0}, STOPPED]
    block, out = export.access_log(view({STORE: {**record(objects=objects), "rows": rows}}))
    assert block["status"] == "partial" and block["packed"] == ["k7q2m:fix_blob"] and out == []


def test_a_missing_control_or_stopped_row_breaks_a_log_and_keeps_its_rows():
    read = opened("cat k")
    for lost in (CONTROLS[0], CONTROLS[1], STOPPED):
        block, rows = export.access_log(view({STORE: without(record(read), lost)}))
        assert block["status"] == "broken" and any(row["origin"] != "control" for row in rows)
    block, _ = export.access_log(view({STORE: without(record(read), CONTROLS[1], STOPPED)}))
    assert block["control"]["end"]["missing"] == ["k7q2m:key"] and block["dropped"] is None


def test_no_open_back_is_absent_with_no_file():
    assert export.access_log(view({})) == ({"status": "absent", "mechanism": "fanotify",
                                            "refused_reads": "not_recorded", "error": None}, None)
    never_captured = {key: value for key, value in record().items() if key != "rows"}
    assert export.access_log(view({STORE: never_captured}))[1] is None
    block, rows = export.access_log(view({STORE: {**record(), "rows": []}}))
    assert block["status"] == "absent" and rows is None
    armed_nothing = [{"seq": 1, "t": START, "row": "arm_failed", "machine": "k7q2m", "object": "key",
                      "path": "/opt/key.log"}, {"seq": 2, "t": START, "row": "ready", "pid": 42, "objects": 1,
                                                "marked": 0}, STOPPED]
    block, rows = export.access_log(view({STORE: {**record(), "rows": armed_nothing}}))
    assert block["status"] == "absent" and rows is None and block["gaps"][0]["row"] == "arm_failed"


def test_the_switch_off_is_off_and_a_run_older_than_the_switch_has_no_block():
    assert export.access_log(view({STORE: record()}, config={"access_log": False})) == ({"status": "off"}, None)
    assert export.access_log(view({STORE: record()}, config={"mode": "shared_task"})) == (None, None)
    assert export.access_log(view({STORE: record()}, config={})) == (None, None)


def test_a_host_tools_rows_pass_as_they_are_and_refusals_are_among_them(monkeypatch):
    rows = [{"t": START, "agent": "k7q2m", "named_by": "host", "origin": "agent", "result": "denied"}]
    bench = SimpleNamespace(host_access_rows=lambda v: rows)
    monkeypatch.setattr(export, "domain", lambda v: bench)
    assert export.access_log(view({})) == ({"status": "host", "mechanism": "host", "origins": {"agent": 1},
                                            "refused_reads": "recorded"}, rows)
    # a run object that never existed differs from one that made no request
    empty = SimpleNamespace(host_access_rows=lambda v: [])
    monkeypatch.setattr(export, "domain", lambda v: empty)
    assert export.access_log(view({}))[0]["status"] == "host"
    missing = SimpleNamespace(host_access_rows=lambda v: None)
    monkeypatch.setattr(export, "domain", lambda v: missing)
    assert export.access_log(view({}))[0]["status"] == "absent"


# --------------------------------------------------------------------------
# run.json and the file
# --------------------------------------------------------------------------
@pytest.mark.parametrize(("status", "flagged"), [("valid", False), ("partial", False), ("off", False), ("host", False),
                                                 ("broken", True), ("absent", True)])
def test_a_broken_or_absent_log_flags_the_runs_validity(status, flagged):
    block = blocks.validity_block([], None, {"status": status})
    assert block["flags"] == ([f"access log: {status}"] if flagged else []) and block["ok"] is not flagged


def test_a_run_with_no_log_keeps_its_validity():
    assert blocks.validity_block([], None) == {"agents": {}, "flags": [], "ok": True}


def eval_log(path, store: dict, config: dict) -> None:
    """An .eval log of one lone agent's sample with this store and config."""
    metadata = {"experiment": {"setup": {"agents": [{"name": "k7q2m"}]}}, "cheatbench": {"config": config}}
    spec = EvalSpec(created="2026-10-31T00:00:00+00:00", task="t", dataset=EvalDataset(), model="mockllm/model",
                    config={})
    write_eval_log(EvalLog(eval=spec, samples=[EvalSample(id="q1", epoch=1, input="", target="", store=store,
                                                          metadata=metadata)]), str(path))


def test_the_export_writes_the_file_and_the_block_and_owns_them(tmp_path):
    good, off, run_dir = tmp_path / "good.eval", tmp_path / "off.eval", tmp_path / "run"
    eval_log(good, {STORE: record(opened("cat k"), seats=LONE)}, {"access_log": True})
    run_export.write_run(good, run_dir)
    block = json.loads((run_dir / RUN_JSON).read_text())["access_log"]
    rows = RunDir(run_dir).access_log()["rows"]
    assert block["status"] == "valid" and [row["origin"] for row in rows] == ["control", "agent", "control"]
    assert ACCESS_LOG in run_export.OWNED_FILES
    # the switch off in a new export of the same directory: a stale log never pairs with it
    eval_log(off, {}, {"access_log": False})
    run_export.write_run(off, run_dir)
    assert not (run_dir / ACCESS_LOG).exists()
    assert json.loads((run_dir / RUN_JSON).read_text())["access_log"] == {"status": "off"}
    assert RunDir(run_dir).access_log() is None


def test_a_log_older_than_the_switch_exports_no_block_and_no_file(tmp_path):
    old = tmp_path / "old.eval"
    eval_log(old, {}, {"mode": "shared_task"})
    run_export.write_run(old, tmp_path / "run")
    run = json.loads((tmp_path / "run" / RUN_JSON).read_text())
    assert "access_log" not in run and not (tmp_path / "run" / ACCESS_LOG).exists()
    assert not any("access log" in flag for flag in run["validity"]["flags"])
