"""The Board tab's Network panel data (src/ui/transcript_network.py): rows on the board's clock, the listeners and
their ends, the calls and the cross-agent mark. No docker."""
from __future__ import annotations

import json

from src.export._export_network_log import NETWORK_LOG
from src.ui import transcript_network as net

START = 1793400000.0
NS = 4026532448


def row(op: str, t: float, **fields) -> dict:
    return {"t": START + t, "op": op, "proto": "tcp", "machine": "k7q2m", "netns": NS, "pid": 700, "agent": "k7q2m",
            "peer_agent": None, "laddr": "127.0.0.1", "lport": None, "raddr": None, "rport": None, "result": "ok",
            "origin": "agent", "call_id": None, "at_start": False, **fields}


def written(tmp_path, rows: list[dict], block: dict | None = None):
    (tmp_path / NETWORK_LOG).write_text("".join(json.dumps(r) + "\n" for r in rows))
    (tmp_path / "run.json").write_text(json.dumps({"network": block or {"status": "valid", "method": "ebpf"}}))
    return tmp_path


ROWS = [row("listen", 0.0, lport=8123, origin="control", control="start"),
        row("bind", 1.0, lport=8123, call_id="w1"),
        row("listen", 1.0, lport=8123, call_id="w1"),
        row("connect", 2.0, pid=800, agent="r5x8w", peer_agent="k7q2m", lport=46152, raddr="127.0.0.1", rport=8123,
            call_id="r1"),
        row("accept", 2.0, lport=8123, raddr="127.0.0.1", rport=46152, peer_agent="r5x8w", call_id="w1"),
        row("close", 3.0, lport=8123),
        row("bind", 4.0, proto="udp", lport=9999, pid=701),
        row("connect", 5.0, pid=900, agent=None, origin="harness", proto="unix", laddr=None, raddr="/run/x.sock",
            result="ENOENT")]
CALLS = {"w1": ("k7q2m", 0), "r1": ("r5x8w", 0)}
AT = {("k7q2m", 0): (0.5, 2.0), ("r5x8w", 0): (1.5, 1.0)}


def test_no_file_gives_no_panel(tmp_path):
    assert net.network_view(tmp_path, CALLS, AT, START) is None


def test_rows_take_their_own_time_on_the_eval_log_clock_and_a_call_for_the_link(tmp_path):
    view = net.network_view(written(tmp_path, ROWS), CALLS, AT, START)
    assert view["block"]["status"] == "valid" and view["cut"] == 0
    assert [(c["op"], c["t"], c["call"], c["cross"]) for c in view["calls"]] == [
        ("connect", 2.0, {"agent": "r5x8w", "k": 0}, True), ("accept", 2.0, {"agent": "k7q2m", "k": 0}, False),
        ("connect", 5.0, None, False)]


def test_a_listener_ends_at_its_close_and_a_bind_that_listened_is_folded(tmp_path):
    view = net.network_view(written(tmp_path, ROWS), CALLS, AT, START)
    assert [(s["proto"], s["lport"], s["t"], s["until"], s["call"]) for s in view["listeners"]] == [
        ("tcp", 8123, 1.0, 3.0, {"agent": "k7q2m", "k": 0}), ("udp", 9999, 4.0, None, None)]


def test_the_controls_rows_stay_out(tmp_path):
    view = net.network_view(written(tmp_path, ROWS), CALLS, AT, START)
    assert all(s["origin"] != "control" for s in view["listeners"]) and len(view["listeners"]) == 2


def test_on_a_call_order_clock_only_rows_of_a_call_have_a_place(tmp_path):
    view = net.network_view(written(tmp_path, ROWS), CALLS, AT, None)
    assert [(c["op"], c["t"]) for c in view["calls"]] == [("accept", 0.5), ("connect", 1.5), ("connect", None)]


def test_the_calls_are_cut_at_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(net, "MAX_CALLS", 1)
    view = net.network_view(written(tmp_path, ROWS), CALLS, AT, START)
    assert len(view["calls"]) == 1 and view["cut"] == 2


def test_a_cross_agent_call_is_connected_only_when_established_and_a_failed_unix_connect_is_quiet(tmp_path):
    """The panel's split of the block's cross_agent and its hidden nscd connects; each connect carries its outcome."""
    rows = [row("connect", 2.0, pid=800, agent="r5x8w", peer_agent="k7q2m", lport=46152, rport=8123,
                outcome="established", outcome_by="syscall"),
            row("connect", 3.0, pid=801, agent="r5x8w", peer_agent="k7q2m", lport=0, rport=8123,
                result="EINPROGRESS", outcome="refused", outcome_error="ECONNREFUSED", outcome_by="state"),
            row("connect", 4.0, pid=802, proto="unix", laddr=None, raddr="/var/run/nscd/socket", result="ENOENT",
                outcome="failed", outcome_error="ENOENT", outcome_by="syscall", via=650),
            row("connect", 5.0, pid=803, proto="unix", laddr=None, raddr="/tmp/s.sock", outcome="established")]
    view = net.network_view(written(tmp_path, rows), CALLS, AT, START)
    assert [(c["cross"], c["connected"], c["quiet"], c["outcome"]) for c in view["calls"]] == [
        (True, True, False, "established"), (True, False, False, "refused"), (False, False, True, "failed"),
        (False, False, False, "established")]
    assert view["calls"][1]["outcome_error"] == "ECONNREFUSED" and view["calls"][2]["via"] == 650
