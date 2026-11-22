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

from src.measures.run_directory import FILES
from src.ui import transcript_board, transcript_viewer
from src.ui.transcript_viewer import Handler, Server, board_detail, tmp_snapshots
from tests.support.run_files import write_text

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
    write_text(with_file / "processed/tmp_snapshots.json", json.dumps(DOC))
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
    assert body == (run_dir / "processed/tmp_snapshots.json").read_bytes()


def test_api_tmp_is_404_without_a_file_or_a_run(served):
    base, _ = served
    status, body, _ = get(f"{base}/api/tmp?id=c/without")
    assert status == 404
    assert json.loads(body) == {"error": "no /tmp snapshots in c/without"}
    status, body, _ = get(f"{base}/api/tmp?id=c/nothing")
    assert status == 404
    assert "no run" in json.loads(body)["error"]
    status, _, _ = get(f"{base}/api/tmp")
    assert status == 404


def test_the_board_payload_says_whether_the_run_has_snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr(transcript_board, "board_payload", lambda run_dir, levels: {"posts": []})
    monkeypatch.setattr(transcript_viewer.RunFiles, "read",
                        staticmethod(lambda run_dir: SimpleNamespace(levels=lambda measured: {})))
    assert board_detail({}, tmp_path, {}) == {"posts": [], "tmp": False, "network": False}
    assert tmp_snapshots(tmp_path) is None
    write_text(tmp_path / "processed/tmp_snapshots.json", "{}")
    assert board_detail({}, tmp_path, {}) == {"posts": [], "tmp": True, "network": False}
    assert tmp_snapshots(tmp_path) == tmp_path / "processed/tmp_snapshots.json"


def test_the_file_name_is_the_snapshot_modules():
    from src.replay import tmp_snapshots as module
    assert Path(FILES["tmp_snapshots.json"]).name == module.FILE


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


@node
def test_a_path_removed_earlier_stays_gone_with_the_call_that_removed_it():
    got = run_state("console.log(JSON.stringify([2, 3, 4].map(c => Object.fromEntries(Object.entries("
                    "S.goneAt(tl, c).default || {}).map(([p, g]) => [p, [g.by, g.entry.text ?? g.entry.type]])))));")
    assert got[0] == {}
    assert got[1] == {"/tmp/ref": [None, "ref"]}                      # removed between calls
    assert got[2] == {"/tmp/ref": [None, "ref"], "/tmp/draft.md": [{"n": 6, "agent": "a1", "k": 3}, "v2"]}
    rows = run_state("console.log(JSON.stringify(S.treeRows(tl.dirs, S.stateAt(tl, 4).default, {}, "
                     "S.stateAt(tl, 4).default, S.goneAt(tl, 4).default).map(r => [r.path, r.gone])));")
    assert ["/tmp/draft.md", {"by": {"n": 6, "agent": "a1", "k": 3}}] in rows
    assert ["/tmp/ref", {"by": None}] in rows and ["/tmp/sub", None] in rows


def entry_js(mode: str, owner: str, uid: int, group: str, gid: int, acl: list[str] | None = None,
             kind: str = "file") -> dict:
    return {"type": kind, "mode": mode, "owner": owner, "uid": uid, "group": group, "gid": gid,
            **({"acl": acl} if acl else {})}


USERS = {"s7m7z": {"uid": 1005, "gid": 1005, "groups": ["s7m7z", "team"]},
         "s3x2j": {"uid": 1004, "gid": 1004, "groups": ["s3x2j", "team"]},
         "m9x5t": {"uid": 1001, "gid": 1001, "groups": ["m9x5t", "team"]}}
ACL_STATE = {
    "/tmp": entry_js("drwxrwxrwt", "root", 0, "root", 0, kind="dir"),
    "/tmp/ref": entry_js("drwxr-xr-x", "root", 0, "root", 0, kind="dir"),
    # creative_setup.grant: root's rw-r-----, one reader by ACL
    "/tmp/ref/part1": entry_js("-rw-r-----", "root", 0, "root", 0,
                               ["user::rw-", "user:s7m7z:r--", "group::---", "mask::r--", "other::---"]),
    "/tmp/ref/whole": entry_js("-rw-r-----", "root", 0, "root", 0),
    # a named reader under a mask that withholds read
    "/tmp/ref/masked": entry_js("-rw-------", "root", 0, "root", 0,
                                ["user::rw-", "user:s3x2j:r--", "group::---", "mask::---", "other::---"]),
    "/tmp/team.md": entry_js("-rw-r-----", "s7m7z", 1005, "team", 1000),
    "/tmp/mine.md": entry_js("-rw-------", "s3x2j", 1004, "s3x2j", 1004),
    "/tmp/open.md": entry_js("-rw-rw-r--", "s7m7z", 1005, "s7m7z", 1005),
    "/tmp/shut": entry_js("drwx------", "s3x2j", 1004, "s3x2j", 1004, kind="dir"),
    "/tmp/shut/in.md": entry_js("-rw-r--r--", "s3x2j", 1004, "s3x2j", 1004),
    "/tmp/old": {"type": "file", "mode": "-rw-r--r--", "owner": "root", "uid": 0},   # schema 1: no gid
}


@node
def test_readers_follow_the_owner_the_acl_under_its_mask_the_group_and_every_directory_above():
    got = run_state(f"const st = {json.dumps(ACL_STATE)}; const users = {json.dumps(USERS)};"
                    "console.log(JSON.stringify(Object.fromEntries(Object.keys(st).map(p => "
                    "[p, S.readersOf(st, p, users)]))));")
    assert got["/tmp/ref/part1"] == ["s7m7z"]
    assert got["/tmp/ref/whole"] == []
    assert got["/tmp/ref/masked"] == []
    assert got["/tmp/team.md"] == ["m9x5t", "s3x2j", "s7m7z"]
    assert got["/tmp/mine.md"] == ["s3x2j"]
    assert got["/tmp/open.md"] == ["m9x5t", "s3x2j", "s7m7z"]
    assert got["/tmp/shut/in.md"] == ["s3x2j"]          # 644, but under a 700 directory
    assert got["/tmp/old"] == []
    assert run_state("console.log(JSON.stringify(S.readersOf({}, '/tmp/x', {})));") == []


@node
def test_the_readers_are_the_machines_agents_and_an_equal_acl_is_no_change():
    doc = {**DOC, "machines": {"default": ["a1"]},
           "users": {"default": {"a1": {"uid": 1001, "gid": 1001, "groups": ["a1"]},
                                 "user": {"uid": 1000, "gid": 1000, "groups": ["user"]}}}}
    got = run_state(f"const t3 = S.tmpTimeline({json.dumps(doc)}, () => 0);"
                    "const acl = ['user::rw-', 'mask::r--'];"
                    "console.log(JSON.stringify([Object.keys(S.agentUsers(t3, 'default')), "
                    "Object.keys(S.agentUsers({users: t3.users, agents: {}}, 'default')).sort(), "
                    "S.sameEntry({type: 'file', acl}, {type: 'file', acl: [...acl]}), "
                    "S.sameEntry({type: 'file', acl}, {type: 'file', acl: ['user::rw-']})]));")
    assert got == [["a1"], ["a1", "user"], True, False]


def test_the_panel_shows_readers_and_marks_an_acl_and_names_the_machine_apart_from_agents():
    panel = (STATIC / "tv_tmp_panel.js").read_text()
    assert "readersChip(readersOf(now, r.path, users), people)" in panel
    assert "const modeText = e => (e.mode ? `${e.mode}${e.acl ? '+' : ''}` : '');" in panel
    assert "'Temp dirs'" in panel and "'/tmp')" not in panel
    assert "at this step" not in panel
    assert "h('span', { class: 'tmp-machine-name' }, m)" in panel
