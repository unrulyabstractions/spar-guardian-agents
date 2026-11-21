"""The transcript viewer's /tmp snapshots: /api/tmp serves a run's tmp_snapshots.json as stored and 404s without one,
the board payload says whether the run has the file, and the Board tab's state module (tv_tmp_state.js, run in node)
rebuilds each machine's /tmp at a time on the board's clock and what changed in a window. No Docker, no model call."""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.ui import transcript_board, transcript_viewer
from src.ui.transcript_viewer import TMP_FILE, Handler, Server, board_detail, tmp_snapshots

STATIC = Path(transcript_viewer.__file__).parent / "transcript_static"
DOC = {"schema": 1, "run": "r", "task": "creative_writings", "dirs": ["/tmp", "/var/tmp"],
       "machines": {"default": ["a1", "b2"]},
       "steps": [
           {"n": 1, "agent": "a1", "k": 0, "machine": "default", "ran": False},
           {"n": 2, "agent": "b2", "k": 0, "machine": "default", "ran": True,
            "before": {"/tmp": {"type": "dir", "owner": "root", "mtime": 1, "size": 4096},
                       "/tmp/ref": {"type": "file", "owner": "root", "size": 3, "text": "ref"},
                       "/var/tmp": {"type": "dir", "owner": "root", "mtime": 1, "size": 4096}},
            "after": {}},
           {"n": 3, "agent": "a1", "k": 1, "machine": "default", "ran": True, "before": {},
            "after": {"/tmp": {"type": "dir", "owner": "root", "mtime": 2, "size": 4096},
                      "/tmp/draft.md": {"type": "file", "owner": "a1", "size": 2, "text": "v1"}}},
           {"n": 4, "agent": "b2", "k": 1, "machine": "default", "ran": False},
           {"n": 5, "agent": "a1", "k": 2, "machine": "default", "ran": True,
            "before": {"/tmp/ref": None},
            "after": {"/tmp/draft.md": {"type": "file", "owner": "a1", "size": 2, "text": "v2"}}},
           {"n": 6, "agent": "a1", "k": 3, "machine": "default", "ran": True, "before": {},
            "after": {"/tmp/draft.md": None, "/tmp/sub": {"type": "dir", "owner": "b2", "mtime": 3, "size": 4096},
                      "/tmp/sub/x": {"type": "file", "owner": "b2", "size": 1}}}]}
TIMES = {"a1:0": 0.0, "b2:0": 1.0, "a1:1": 2.0, "b2:1": 3.0, "a1:2": 4.0, "a1:3": 5.0}


# ------------------------------------------------------------------------------------------------------------ route
@pytest.fixture
def served(tmp_path, monkeypatch):
    """A viewer on a free port over two runs: one with tmp_snapshots.json, one without."""
    with_file, without = tmp_path / "with", tmp_path / "without"
    with_file.mkdir()
    without.mkdir()
    (with_file / TMP_FILE).write_text(json.dumps(DOC))
    viewer = SimpleNamespace(dirs={"c/with": with_file, "c/without": without}, rows={}, measured={})
    monkeypatch.setattr(Handler, "viewer", viewer, raising=False)
    srv = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", with_file
    srv.shutdown()
    srv.server_close()


def get(url: str) -> tuple[int, bytes, str]:
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.read(), r.headers["Content-Type"]
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers["Content-Type"]


def test_api_tmp_serves_the_file_as_stored(served):
    base, run_dir = served
    status, body, ctype = get(f"{base}/api/tmp?id=c/with")
    assert status == 200
    assert ctype == "application/json"
    assert body == (run_dir / TMP_FILE).read_bytes()


def test_api_tmp_is_404_without_a_file_or_a_run(served):
    base, _ = served
    status, body, _ = get(f"{base}/api/tmp?id=c/without")
    assert status == 404
    assert json.loads(body) == {"error": f"no {TMP_FILE} in c/without"}
    status, body, _ = get(f"{base}/api/tmp?id=c/nothing")
    assert status == 404
    assert "no run" in json.loads(body)["error"]
    status, _, _ = get(f"{base}/api/tmp")
    assert status == 404


def test_the_board_payload_says_whether_the_run_has_snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr(transcript_board, "board_payload", lambda run_dir, levels: {"posts": []})
    monkeypatch.setattr(transcript_viewer.RunFiles, "read",
                        staticmethod(lambda run_dir: SimpleNamespace(levels=lambda measured: {})))
    assert board_detail({}, tmp_path, {}) == {"posts": [], "tmp": False}
    assert tmp_snapshots(tmp_path) is None
    (tmp_path / TMP_FILE).write_text("{}")
    assert board_detail({}, tmp_path, {}) == {"posts": [], "tmp": True}
    assert tmp_snapshots(tmp_path) == tmp_path / TMP_FILE


def test_the_file_name_is_the_snapshot_modules():
    from src.replay import tmp_snapshots as module
    assert TMP_FILE == module.FILE


def test_the_board_shows_the_panel_only_with_snapshots():
    board = (STATIC / "tv_view_board.js").read_text()
    run = (STATIC / "tv_view_run.js").read_text()
    assert "const tmp = d.tmp && opts.tmpUrl ? tmpPanel(opts.tmpUrl" in board
    assert "tmp ? tmp.el : null," in board
    assert "tmpUrl: tmpUrl(id)" in run
    panel = (STATIC / "tv_tmp_panel.js").read_text()
    assert "export const tmpUrl = id => `/api/tmp?id=${enc(id)}`;" in panel


# ------------------------------------------------------------------------------------------------- the state, in node
def run_state(script: str) -> dict:
    """Run a script against tv_tmp_state.js in node, with DOC as doc and TIMES as the clock; its printed JSON."""
    module = (STATIC / "tv_tmp_state.js").as_uri()
    code = (f"import * as S from {json.dumps(module)};\n"
            f"const doc = {json.dumps(DOC)};\nconst times = {json.dumps(TIMES)};\n"
            "const tl = S.tmpTimeline(doc, (agent, k) => times[`${agent}:${k}`]);\n" + script)
    done = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the tab's state module")


@node
def test_steps_are_the_calls_that_ran_on_the_boards_clock():
    got = run_state("console.log(JSON.stringify({steps: tl.steps.map(s => [s.n, s.t, s.first]), "
                    "machines: tl.machines, at: [-1, 0.5, 1, 2, 9].map(t => S.countAt(tl, t))}));")
    assert got["steps"] == [[2, 1, True], [3, 2, False], [5, 4, False], [6, 5, False]]
    assert got["machines"] == ["default"]
    assert got["at"] == [0, 0, 1, 2, 4]


@node
def test_the_baseline_holds_before_the_first_call_and_each_step_applies_in_order():
    got = run_state("console.log(JSON.stringify([0, 1, 2, 3, 4].map(c => {"
                    "const s = S.stateAt(tl, c).default; return Object.fromEntries(Object.entries(s)"
                    ".map(([p, e]) => [p, e.text ?? e.type]));})));")
    assert got[0] == got[1] == {"/tmp": "dir", "/tmp/ref": "ref", "/var/tmp": "dir"}
    assert got[2] == {"/tmp": "dir", "/tmp/ref": "ref", "/var/tmp": "dir", "/tmp/draft.md": "v1"}
    assert got[3] == {"/tmp": "dir", "/var/tmp": "dir", "/tmp/draft.md": "v2"}
    assert got[4] == {"/tmp": "dir", "/var/tmp": "dir", "/tmp/sub": "dir", "/tmp/sub/x": "file"}


@node
def test_changes_in_a_window_name_their_kind_and_call_and_skip_a_directorys_mtime():
    got = run_state("console.log(JSON.stringify([[0, 1], [1, 2], [2, 3], [3, 4], [1, 4]]"
                    ".map(([a, b]) => S.changesIn(tl, a, b).default)));")
    first, added, changed, last, whole = got
    assert first == {}   # the baseline is no change
    assert added == {"/tmp/draft.md": {"kind": "added", "by": {"n": 3, "agent": "a1", "k": 1}}}
    assert changed == {"/tmp/ref": {"kind": "removed", "by": None},
                       "/tmp/draft.md": {"kind": "changed", "by": {"n": 5, "agent": "a1", "k": 2}}}
    assert last == {"/tmp/draft.md": {"kind": "removed", "by": {"n": 6, "agent": "a1", "k": 3}},
                    "/tmp/sub": {"kind": "added", "by": {"n": 6, "agent": "a1", "k": 3}},
                    "/tmp/sub/x": {"kind": "added", "by": {"n": 6, "agent": "a1", "k": 3}}}
    # across the whole window draft.md came and went: no net change
    assert set(whole) == {"/tmp/ref", "/tmp/sub", "/tmp/sub/x"}


@node
def test_the_tree_lists_each_root_then_its_entries_with_removed_paths_kept():
    got = run_state("const ch = S.changesIn(tl, 3, 4).default;"
                    "console.log(JSON.stringify(S.treeRows(tl.dirs, S.stateAt(tl, 4).default, ch, "
                    "S.stateAt(tl, 3).default).map(r => [r.path, r.name, r.depth, r.change && r.change.kind, "
                    "r.entry.owner])));")
    assert got == [["/tmp", "/tmp", 0, None, "root"],
                   ["/tmp/draft.md", "draft.md", 1, "removed", "a1"],
                   ["/tmp/sub", "sub", 1, "added", "b2"],
                   ["/tmp/sub/x", "x", 2, "added", "b2"],
                   ["/var/tmp", "/var/tmp", 0, None, "root"]]


@node
def test_a_call_missing_from_the_clock_keeps_the_time_of_the_step_before():
    got = run_state("const t2 = S.tmpTimeline(doc, (agent, k) => (agent === 'a1' && k === 2 ? undefined "
                    ": times[`${agent}:${k}`]));"
                    "console.log(JSON.stringify(t2.steps.map(s => s.t)));")
    assert got == [1, 2, 2, 5]
