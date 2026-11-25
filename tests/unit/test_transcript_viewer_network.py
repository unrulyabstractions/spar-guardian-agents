"""The transcript viewer's network log: /api/network serves the run's log on the board's clock
(transcript_board.network_payload), read once per run, and 404s without network.jsonl or without the run; the board
payload says whether the run has the file; network_payload reads no run without the file and puts the rows on the
board's clock with their process and their call. No Docker, no model call."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.measures.run_calls import Call
from src.ui import transcript_board, transcript_viewer
from src.ui.transcript_viewer import Handler, Server, Viewer, board_detail, network_file
from tests.support.run_files import write_text

STATIC = Path(transcript_viewer.__file__).parent / "transcript_static"
START = 1795245076.0
NS = 4026532448


def row(op: str, t: float, **fields) -> dict:
    return {"t": START + t, "op": op, "proto": "tcp", "machine": "w2", "netns": NS, "pid": 700, "uid": 0,
            "agent": "w2", "peer_agent": None, "laddr": "127.0.0.1", "lport": None, "raddr": None, "rport": None,
            "result": "ok", "origin": "agent", "call_id": None, "at_start": False, "comm": "python3",
            "exe": "/usr/bin/python3.11", "cmd": "python3 -m http.server 8180", **fields}


# a worker's server on 8180 and a regulator's curl to it from its observer uid, as the SWE smoke's swe_http records it
ROWS = [row("listen", 0.2, lport=8180, call_id="serve"),
        row("connect", 4.1, pid=800, uid=3001, agent="r1", peer_agent="w2", lport=39166, raddr="127.0.0.1",
            rport=8180, result="EINPROGRESS", call_id="reach", comm=None, exe="/usr/bin/curl",
            cmd="curl http://127.0.0.1:8180/"),
        row("accept", 4.1, lport=8180, raddr="127.0.0.1", rport=39166, peer_agent="r1", origin="detached")]


def call(agent: str, k: int, start: float, end: float, cid: str) -> Call:
    return Call(agent, k, 0, START + start, START + end, "shell", cid, "", {}, "")


RUN = SimpleNamespace(calls={"w2": [call("w2", 0, 0.1, 3.0, "serve")], "r1": [call("r1", 0, 0.0, 0.05, "r0"),
                                                                         call("r1", 1, 3.9, 4.3, "reach")]})


def written(run_dir: Path) -> Path:
    write_text(run_dir / "processed/network.jsonl", "".join(json.dumps(r) + "\n" for r in ROWS))
    write_text(run_dir / "processed/run.json", json.dumps({"network": {"status": "valid", "method": "ebpf"}}))
    return run_dir


# ------------------------------------------------------------------------------------------------------------ route
@pytest.fixture
def served(tmp_path, monkeypatch):
    """A viewer on a free port over two runs: one with network.jsonl, one without; network_payload counted."""
    with_file, without = tmp_path / "with", tmp_path / "without"
    with_file.mkdir()
    without.mkdir()
    written(with_file)
    asked = []

    def payload(run_dir):
        asked.append(Path(run_dir))
        return {"block": {"status": "valid"}, "listeners": [], "calls": [], "cut": 0}

    monkeypatch.setattr(transcript_board, "network_payload", payload)
    viewer = object.__new__(Viewer)
    viewer.dirs = {"c/with": with_file, "c/without": without}
    viewer.rows, viewer.measured = {}, {}
    viewer._network, viewer._network_lock = {}, threading.Lock()
    monkeypatch.setattr(Handler, "viewer", viewer, raising=False)
    srv = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", with_file, asked
    srv.shutdown()
    srv.server_close()


def get(url: str) -> tuple[int, dict, str]:
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, json.loads(r.read()), r.headers["Content-Type"]
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()), e.headers["Content-Type"]


def test_api_network_serves_the_payload_and_reads_the_run_once(served):
    base, run_dir, asked = served
    status, body, ctype = get(f"{base}/api/network?id=c/with")
    assert status == 200 and ctype == "application/json"
    assert body == {"block": {"status": "valid"}, "listeners": [], "calls": [], "cut": 0}
    assert get(f"{base}/api/network?id=c/with")[1] == body
    assert asked == [run_dir]                       # cached per run


def test_api_network_is_404_without_a_file_or_a_run(served):
    base, _, asked = served
    status, body, _ = get(f"{base}/api/network?id=c/without")
    assert status == 404 and body == {"error": "no network log in c/without"}
    status, body, _ = get(f"{base}/api/network?id=c/nothing")
    assert status == 404 and "no run" in body["error"]
    assert get(f"{base}/api/network")[0] == 404
    assert asked == []


def test_the_board_payload_says_whether_the_run_has_a_network_log(tmp_path, monkeypatch):
    monkeypatch.setattr(transcript_board, "board_payload", lambda run_dir, levels: {"posts": []})
    monkeypatch.setattr(transcript_viewer.RunFiles, "read",
                        staticmethod(lambda run_dir: SimpleNamespace(levels=lambda measured: {})))
    assert board_detail({}, tmp_path, {}) == {"posts": [], "tmp": False, "network": False}
    assert network_file(tmp_path) is None
    write_text(tmp_path / "processed/network.jsonl", "")
    assert board_detail({}, tmp_path, {}) == {"posts": [], "tmp": False, "network": True}
    assert network_file(tmp_path) == tmp_path / "processed/network.jsonl"


# ------------------------------------------------------------------------------------------------- network_payload
def test_network_payload_reads_no_run_without_the_file(tmp_path, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("read the run")

    monkeypatch.setattr(transcript_board, "load_run", refuse)
    assert transcript_board.network_payload(tmp_path) is None


def test_network_payload_puts_the_rows_on_the_boards_clock(tmp_path, monkeypatch):
    monkeypatch.setattr(transcript_board, "load_run", lambda run_dir, times: RUN)
    view = transcript_board.network_payload(written(tmp_path))
    # the clock's zero is the first call's start (r1's call 0 at 0.0)
    [listener] = view["listeners"]
    assert (listener["machine"], listener["lport"], listener["t"], listener["until"]) == ("w2", 8180, 0.2, None)
    assert listener["call"] == {"agent": "w2", "k": 0}
    assert (listener["prog"], listener["pid"], listener["uid"]) == ("python3", 700, 0)
    connect, accept = view["calls"]
    assert (connect["op"], connect["t"], connect["agent"], connect["peer_agent"], connect["rport"], connect["cross"]) \
        == ("connect", 4.1, "r1", "w2", 8180, True)
    assert connect["call"] == {"agent": "r1", "k": 1}
    assert (connect["prog"], connect["uid"], connect["cmd"]) == ("curl", 3001, "curl http://127.0.0.1:8180/")
    assert (accept["op"], accept["agent"], accept["peer_agent"], accept["lport"], accept["cross"], accept["call"]) \
        == ("accept", "w2", "r1", 8180, False, None)


def test_the_board_shows_the_panel_only_with_a_network_log():
    board = (STATIC / "tv_view_board.js").read_text()
    run = (STATIC / "tv_view_run.js").read_text()
    panel = (STATIC / "tv_net_panel.js").read_text()
    assert "const net = d.network && opts.netUrl ? netPanel(opts.netUrl" in board
    # the message board stands first, a slider above and below its posts; then the timeline with its own slider;
    # the /tmp panel and this panel stand under the timeline
    order = ("h('section', { class: 'card bd-board' }, boardHead, scrubs.boardTop.el, boardBox, "
             "scrubs.boardBottom.el),\n"
             "    h('section', { class: 'card bd-timeline' }, controls, lensKeyRow, svgHost, scrubs.timeline.el, "
             "cardRow, legend),\n"
             "    tmp ? tmp.el : null,\n    net ? net.el : null));")
    assert order in board
    assert "netUrl: netUrl(id)" in run
    assert "export const netUrl = id => `/api/network?id=${enc(id)}`;" in panel


def test_the_panel_hides_failed_unix_connects_until_asked_and_splits_cross_agent_calls():
    """The nscd connects (two per shell) stay out of the Calls list until the chip is on; a cross-agent call that
    connected and one only attempted take their own colours, in the list and on the lanes; a partial log shows its
    reasons."""
    panel = (STATIC / "tv_net_panel.js").read_text()
    board = (STATIC / "tv_view_board.js").read_text()
    assert "let showQuiet = false;" in panel and "&& (showQuiet || !row.quiet);" in panel
    assert "quietToggle.textContent = `failed unix ${quiet}`;" in panel
    assert "c.connected ? 'net-cross' : c.cross ? 'net-cross-try' : null" in panel
    assert "block.partial_reasons || []" in panel
    assert "c.connected ? 'bd-net-cross' : c.cross ? 'bd-net-try' : null" in board
