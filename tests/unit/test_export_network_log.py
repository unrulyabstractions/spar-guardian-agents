"""The network log's export (src/export/_export_network_log.py): who each raw row was, its tool call, the agent at the
other end, every status of run.json "network" and the cross-agent connections. No docker."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from inspect_ai.event import SpanBeginEvent, ToolEvent

from src.export import _export_network_log as export
from src.sandbox.access_log import access_monitor_daemon as daemon
from src.sandbox.network_log.network_monitor import STORE

START = 1793400000.0
NS = 4026532448
TOOL_ROOT = "timeout -k 5s 300s bash --login -c"
WORKER, REGULATOR = "k7q2m", "r5x8w"
SEATS = [{"machine": WORKER, "uid": 1001, "user": "runner", "agent": WORKER},
         {"machine": REGULATOR, "uid": 1001, "user": "runner", "agent": REGULATOR},
         {"machine": WORKER, "uid": 3001, "user": None, "agent": REGULATOR, "observer": True},
         {"machine": REGULATOR, "uid": 3001, "user": None, "agent": REGULATOR, "observer": True}]


def moment(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, timezone.utc)


def digest(command: str) -> str:
    return daemon.exec_digest([b"timeout", b"-k", b"5s", b"300s", b"bash", b"--login", b"-c", command.encode()])


def root(command: str, *, cmd: str | None = None, detached: bool = False) -> dict:
    return {"pid": 500, "ns_pid": 1 if detached else 7, "cmd": cmd or f"{TOOL_ROOT} {command}",
            "sha256": digest(command), "detached": detached}


def net(op: str, *, seq: int, t: float, pid: int, uid: int, command: str = "python3 srv.py", machine: str = WORKER,
        proto: str = "tcp", laddr="127.0.0.1", lport=None, raddr=None, rport=None, **fields) -> dict:
    """A raw net row of a process under a tool's exec of `command`."""
    return {"seq": seq, "t": t + 0.01, "t_kernel": t, "row": "net", "op": op, "proto": proto, "family": "inet",
            "type": "stream", "netns": NS, "machine": machine, "pid": pid, "tid": pid, "kernel_uid": uid, "uid": uid,
            "comm": "python3", "laddr": laddr, "lport": lport, "raddr": raddr, "rport": rport, "result": "ok",
            "source": "ebpf", "exe": "/usr/bin/python3", "cmd": command, "chain": [command], "root": root(command),
            **fields}


def controls(name: str, seq: int, t: float) -> list[dict]:
    harness = {"command": "c", "uid": 0, "control": name, "root": root("c", cmd="python3 -I -c x n0nce 10")}
    return [net("listen", seq=seq, t=t, pid=90 + seq, lport=40000 + seq, **harness),
            net("connect", seq=seq + 1, t=t, pid=90 + seq, lport=50000 + seq, raddr="127.0.0.1", rport=40000 + seq,
                **harness),
            net("accept", seq=seq + 2, t=t, pid=90 + seq, lport=40000 + seq, raddr="127.0.0.1", rport=50000 + seq,
                **harness)]


READY = {"seq": 1, "t": START, "row": "ready", "pid": 42, "method": "ebpf", "netns": {str(NS): [WORKER]}, "probes": 16}
STOPPED = {"seq": 99, "t": START + 99, "row": "stopped", "rows": 98, "dropped": 0, "lost": 0}

# The worker's server on 8123, the regulator's observer connecting to it through peer_shell, and the accept.
SERVER = net("listen", seq=10, t=START + 10, pid=700, uid=1001, lport=8123, command="python3 srv.py &")
CLIENT = net("connect", seq=11, t=START + 20, pid=800, uid=3001, lport=46152, raddr="127.0.0.1", rport=8123,
             command="curl localhost:8123")
ACCEPT = net("accept", seq=12, t=START + 20.001, pid=700, uid=1001, lport=8123, raddr="127.0.0.1", rport=46152,
             command="python3 srv.py &")


def call(id: str, agent: str, command: str, start: float, end: float, function: str = "shell") -> ToolEvent:
    arguments = {"peer": WORKER, "command": command} if function == "peer_shell" else {"command": command}
    return ToolEvent(id=id, function=function, arguments=arguments, span_id=agent, timestamp=moment(start),
                     completed=moment(end))


CALLS = (call("w1", WORKER, "python3 srv.py &", START + 9, START + 11),
         call("r1", REGULATOR, "curl localhost:8123", START + 19, START + 21, "peer_shell"))


def view(store: dict | None, *events, config: dict | None = None) -> SimpleNamespace:
    spans = [SpanBeginEvent(id=agent, name=agent, type="agent") for agent in sorted({e.span_id for e in events})]
    metadata = {"cheatbench": {"task": None, "config": {"access_log": True, "network_log": True}
                               if config is None else config}}
    return SimpleNamespace(store_get=(store or {}).get, events=[*spans, *events], metadata=metadata, scores=None)


def record(*rows, ready=READY, stopped=STOPPED, end: bool = True, **fields) -> dict:
    raw = [ready, *controls("start", 2, START + 1), *rows, *(controls("end", 80, START + 80) if end else []),
           *([stopped] if stopped else [])]
    return {"seats": SEATS, "machines": {WORKER: {"container": "c1", "pid": 10}}, "control_machines": [WORKER],
            "nonces": {"start": "a", "end": "b"}, "pid": 42, "method": "ebpf", "rows": raw, "error": None, **fields}


def exported(*rows, events: tuple = CALLS, **fields) -> tuple[dict, list[dict]]:
    block, out = export.network_log(view({STORE: record(*rows, **fields)}, *events))
    return block, [row for row in out if row["origin"] != "control"]


# --------------------------------------------------------------------------
# who and the other end
# --------------------------------------------------------------------------
def test_an_observer_connecting_to_a_workers_server_is_a_cross_agent_connection():
    block, (server, client, accept) = exported(SERVER, CLIENT, ACCEPT)
    assert (server["agent"], server["named_by"], server["call_id"], server["origin"]) == \
        (WORKER, "machine", "w1", "agent")
    assert (client["agent"], client["named_by"], client["call_id"]) == (REGULATOR, "observer", "r1")
    assert (client["peer_agent"], client["peer_pid"], accept["peer_agent"], accept["peer_pid"]) == \
        (WORKER, 700, REGULATOR, 800)
    assert client["t"] == START + 20 and client["machine"] == WORKER and client["uid"] == 3001
    [cross] = block["cross_agent"]
    assert cross == {"t": START + 20, "machine": WORKER, "proto": "tcp", "op": "connect", "from": REGULATOR,
                     "to": WORKER, "from_pid": 800, "to_pid": 700, "raddr": "127.0.0.1", "rport": 8123,
                     "result": "ok", "call_id": "r1", "named_by": "observer"}
    assert block["agents"] == {REGULATOR: {"connect": 1}, WORKER: {"accept": 1, "listen": 1}}
    assert block["ops"] == {"accept": 3, "connect": 3, "listen": 3} and block["origins"]["control"] == 6


def test_a_connect_with_no_accept_finds_the_listener():
    refused = {**CLIENT, "result": "ECONNREFUSED"}
    _, (_, client) = exported(SERVER, refused)
    assert client["peer_agent"] == WORKER and client["peer_pid"] == 700


def test_a_listener_after_the_connect_is_no_peer():
    early = {**CLIENT, "t_kernel": START + 5}
    _, (_, client) = exported(SERVER, early)
    assert client["peer_agent"] is None


def test_a_gone_process_is_named_by_the_kernels_uid_and_a_root_one_by_nobody():
    gone = {**CLIENT, "root": None, "chain": None, "cmd": None, "exe": None, "gone": True}
    root_gone = {**gone, "seq": 13, "uid": 0, "kernel_uid": 0}
    _, (_, client, nobody) = exported(SERVER, gone, root_gone)
    assert (client["origin"], client["agent"], client["named_by"], client["call_id"]) == \
        ("unknown", REGULATOR, "observer", None)
    assert (nobody["origin"], nobody["agent"]) == ("unknown", None)


def test_a_harness_process_names_nobody():
    harness = {**SERVER, "root": root("x", cmd="bash -lc stage.sh"), "uid": 0}
    _, [row] = exported(harness)
    assert (row["origin"], row["agent"], row["named_by"]) == ("harness", None, None)


def test_unix_and_udp_peers_come_from_the_path_and_the_bound_port():
    unix = {"proto": "unix", "laddr": None, "lport": None}
    bound = net("listen", seq=20, t=START + 30, pid=700, uid=1001, **{**unix, "laddr": "/tmp/s.sock"})
    joined = net("connect", seq=21, t=START + 31, pid=800, uid=3001, **unix, raddr="/tmp/s.sock")
    taken = net("accept", seq=22, t=START + 31.001, pid=700, uid=1001, **{**unix, "laddr": "/tmp/s.sock"})
    udp = {"proto": "udp", "type": "dgram"}
    socket = net("bind", seq=23, t=START + 40, pid=701, uid=1001, lport=9999, **udp)
    sent = net("send", seq=24, t=START + 41, pid=801, uid=3001, lport=56000, raddr="127.0.0.1", rport=9999, **udp)
    block, rows = exported(bound, joined, taken, socket, sent)
    by_seq = {row["seq"]: row for row in rows}
    assert (by_seq[21]["peer_agent"], by_seq[22]["peer_agent"], by_seq[24]["peer_agent"]) == \
        (WORKER, REGULATOR, WORKER)
    assert [(c["proto"], c["op"]) for c in block["cross_agent"]] == [("unix", "connect"), ("udp", "send")]


def test_one_agent_talking_to_itself_is_no_cross_agent_connection():
    own = {**CLIENT, "uid": 1001, "kernel_uid": 1001, "command": "python3 srv.py &", "root": root("python3 srv.py &")}
    block, _ = exported(SERVER, own)
    assert block["cross_agent"] == []


# --------------------------------------------------------------------------
# run.json "network"
# --------------------------------------------------------------------------
def test_every_control_and_a_stopped_row_read_valid():
    block, _ = exported(SERVER)
    assert (block["status"], block["method"], block["fallback"], block["gaps"], block["dropped"], block["lost"]) == \
        ("valid", "ebpf", None, [], 0, 0)
    assert block["control"] == {"start": {"seen": 2, "missing": []}, "end": {"seen": 2, "missing": []}}


def test_a_missing_end_control_or_stop_row_reads_broken():
    block, _ = exported(SERVER, end=False)
    assert block["status"] == "broken" and block["control"]["end"]["missing"] == [f"{WORKER}:accept",
                                                                                  f"{WORKER}:connect"]
    assert exported(SERVER, stopped=None)[0]["status"] == "broken"


@pytest.mark.parametrize("trouble", [
    {"seq": 50, "t": START + 50, "row": "trace_failed", "error": "bpftrace exited 1"},
    {"seq": 50, "t": START + 50, "row": "lost", "events": 4},
    {"seq": 50, "t": START + 50, "row": "restart", "machine": WORKER, "old_pid": 10, "new_pid": 11},
])
def test_a_gap_reads_partial(trouble):
    block, _ = exported(SERVER, trouble)
    assert block["status"] == "partial" and block["gaps"][0]["row"] == trouble["row"]


def test_a_tracer_that_never_attached_is_the_methods_fallback_and_no_gap():
    failed = {"seq": 0, "t": START, "row": "trace_failed", "error": "no tracer mounted"}
    raw = record(SERVER, ready={**READY, "method": "procnet"})
    raw["rows"].insert(0, failed)
    block, _ = export.network_log(view({STORE: raw}, *CALLS))
    assert (block["status"], block["method"], block["fallback"], block["gaps"]) == \
        ("valid", "procnet", "no tracer mounted", [])


def test_dropped_rows_read_partial():
    block, _ = exported(SERVER, stopped={**STOPPED, "dropped": 3})
    assert block["status"] == "partial" and block["dropped"] == 3


@pytest.mark.parametrize(("config", "store", "want"), [
    ({"access_log": True}, None, None),
    ({"access_log": True, "network_log": False}, None, {"status": "off"}),
    ({"access_log": False, "network_log": True}, {}, {"status": "absent", "error": None}),
    ({"access_log": True, "network_log": True}, {}, {"status": "absent", "error": None}),
    ({"access_log": True, "network_log": True}, {STORE: {"seats": [], "error": "x"}}, {"status": "absent",
                                                                                         "error": "x"}),
])
def test_the_switch_and_a_missing_record(config, store, want):
    block, rows = export.network_log(view(store, config=config))
    assert block == want and rows is None


def test_a_domain_without_a_sidecar_reads_off(monkeypatch):
    monkeypatch.setattr(export, "domain", lambda v: SimpleNamespace(watched=None))
    assert export.network_log(view({}))[0] == {"status": "off"}


def test_each_row_holds_the_documented_keys():
    _, rows = exported(SERVER, CLIENT, ACCEPT)
    for name in ("seq", "t", "op", "proto", "family", "type", "machine", "netns", "pid", "uid", "user", "agent",
                 "named_by", "origin", "call_id", "exe", "cmd", "root_cmd", "comm", "laddr", "lport", "raddr", "rport",
                 "result", "source", "at_start", "control", "peer_agent", "peer_pid", "peer_origin"):
        assert all(name in row for row in rows), name
    assert all(name in export.__doc__ for name in ("peer_origin", "at_start", "cross_agent"))
