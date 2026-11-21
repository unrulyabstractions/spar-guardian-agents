"""tmp_snapshots.json's parts that need no Docker: the listing script on a local folder, the recorder around a
replayer, and the per-machine delta encoding with its decoding at any step."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

from src.replay import tmp_snapshots as ts
from src.replay.recorded_calls import RecordedCall
from src.replay.replay_sandbox import Exec


def call(n: int, agent: str, tool: str = "shell", k: int | None = None) -> RecordedCall:
    return RecordedCall(n=n, agent=agent, k=n if k is None else k, id=f"c{n}", tool=tool, arguments={}, result="",
                        error=None, t0=float(n), t1=float(n) + 0.5)


def entry(sha: str, text: str | None = None) -> dict:
    return {"type": "file", "size": 1, "mode": "-rw-r--r--", "owner": "a", "uid": 1001, "mtime": 1.0,
            "sha256": sha, **({"text": text} if text is not None else {})}


def run_listing(root, **spec) -> dict:
    done = subprocess.run([sys.executable, "-I", "-c", ts.LISTING], capture_output=True, text=True, check=True,
                          input=json.dumps({**ts.listing_spec([str(root)]), **spec}))
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_the_listing_reads_every_entry_with_its_kind_mode_owner_hash_and_small_text(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "note.md").write_text("draft\n")
    (tmp_path / "bin").write_bytes(b"a\0b")
    (tmp_path / "big.txt").write_text("x" * (ts.TEXT_MAX_BYTES + 1))
    (tmp_path / "huge").write_bytes(b"y" * (ts.HASH_MAX_BYTES + 1))
    os.symlink("d/note.md", tmp_path / "ln")
    os.chmod(tmp_path / "d" / "note.md", 0o640)
    found = run_listing(tmp_path)
    root = str(tmp_path)
    assert set(found) == {root, f"{root}/d", f"{root}/d/note.md", f"{root}/bin", f"{root}/big.txt",
                          f"{root}/huge", f"{root}/ln"}
    note = found[f"{root}/d/note.md"]
    assert (note["type"], note["size"], note["mode"], note["text"]) == ("file", 6, "-rw-r-----", "draft\n")
    assert note["sha256"] == hashlib.sha256(b"draft\n").hexdigest()
    assert note["uid"] == os.getuid() and note["owner"] and isinstance(note["mtime"], float)
    assert found[f"{root}/d"]["type"] == "dir" and found[f"{root}/d"]["mode"].startswith("d")
    assert "text" not in found[f"{root}/bin"] and len(found[f"{root}/bin"]["sha256"]) == 64
    assert "text" not in found[f"{root}/big.txt"] and "sha256" in found[f"{root}/big.txt"]
    assert "sha256" not in found[f"{root}/huge"] and found[f"{root}/huge"]["size"] == ts.HASH_MAX_BYTES + 1
    assert (found[f"{root}/ln"]["type"], found[f"{root}/ln"]["target"]) == ("link", "d/note.md")


def test_the_listing_skips_a_missing_root(tmp_path):
    assert run_listing(tmp_path / "absent") == {}


def test_the_sha256_is_the_files_own(tmp_path):
    (tmp_path / "f").write_text("hello")
    assert run_listing(tmp_path)[f"{tmp_path}/f"]["sha256"] == hashlib.sha256(b"hello").hexdigest()


CALLS = [call(1, "a", "board_post"), call(2, "a"), call(3, "b"), call(4, "b", "board_read"), call(5, "a")]
TAKEN = {
    2: {"machine": "default", "before": {"/tmp": entry("d0")},
        "after": {"/tmp": entry("d1"), "/tmp/x.md": entry("x1", "one")}},
    3: {"machine": "default", "before": {"/tmp": entry("d1"), "/tmp/x.md": entry("x1", "one")},
        "after": {"/tmp": entry("d1"), "/tmp/x.md": entry("x2", "two")}},
    5: {"machine": "default", "before": {"/tmp": entry("d1"), "/tmp/x.md": entry("x2", "two")},
        "after": {"/tmp": entry("d2")}},
}


def machine_of(_call):
    return "default"


def test_encode_writes_the_first_state_whole_then_changes_and_a_removal_as_null():
    steps = ts.encode(CALLS, TAKEN, machine_of, {1: "recorded", 2: "exact"})
    assert [s["ran"] for s in steps] == [False, True, True, False, True]
    assert "before" not in steps[0] and steps[0]["match"] == "recorded"
    assert steps[1]["before"] == {"/tmp": entry("d0")}
    assert steps[1]["after"] == {"/tmp": entry("d1"), "/tmp/x.md": entry("x1", "one")}
    assert steps[2]["before"] == {} and steps[2]["after"] == {"/tmp/x.md": entry("x2", "two")}
    assert steps[4]["before"] == {} and steps[4]["after"] == {"/tmp": entry("d2"), "/tmp/x.md": None}
    assert [(s["n"], s["agent"], s["k"], s["call_id"]) for s in steps] == [(c.n, c.agent, c.k, c.id) for c in CALLS]


def test_decode_and_state_at_give_the_full_state_at_every_step():
    doc = {"steps": ts.encode(CALLS, TAKEN, machine_of)}
    assert ts.decode(doc) == TAKEN
    assert ts.state_at(doc, 1) == {}
    assert ts.state_at(doc, 2, before=True) == {"default": TAKEN[2]["before"]}
    assert ts.state_at(doc, 2) == {"default": TAKEN[2]["after"]}
    assert ts.state_at(doc, 4) == {"default": TAKEN[3]["after"]}
    assert ts.state_at(doc, 4, before=True) == {"default": TAKEN[3]["after"]}
    assert ts.state_at(doc, 5) == {"default": TAKEN[5]["after"]}


def test_a_before_that_differs_from_the_last_after_is_kept():
    taken = {2: TAKEN[2], 3: {**TAKEN[3], "before": {"/tmp": entry("d1"), "/tmp/x.md": entry("x9")}}}
    doc = {"steps": ts.encode(CALLS[:3], taken, machine_of)}
    assert doc["steps"][2]["before"] == {"/tmp/x.md": entry("x9")}
    assert ts.decode(doc) == taken


def test_machines_are_kept_apart():
    taken = {2: {"machine": "a", "before": {"/tmp": entry("a0")}, "after": {"/tmp": entry("a1")}},
             3: {"machine": "b", "before": {"/tmp": entry("b0")}, "after": {"/tmp": entry("b0")}}}
    doc = {"steps": ts.encode(CALLS[:3], taken, lambda c: c.agent)}
    assert doc["steps"][2]["before"] == {"/tmp": entry("b0")} and doc["steps"][2]["after"] == {}
    assert ts.state_at(doc, 3) == {"a": {"/tmp": entry("a1")}, "b": {"/tmp": entry("b0")}}


class FakeInner:
    task = "fake"

    def __init__(self, services):
        self._services, self.log = services, []

    def services(self):
        return {name: {"image": "x"} for name in self._services}

    def execute(self, box, c):
        self.log.append(("execute", c.n))
        return "out", False

    def after(self, box, c, output):
        self.log.append(("after", c.n))
        return output, {}, [], []

    def in_sandbox(self, c):
        return True


class FakeBox:
    def __init__(self, inner):
        self.inner, self.runs = inner, []

    def run(self, service, argv, *, stdin=None, timeout=None, user=None, workdir=None):
        self.runs.append((service, user, len(self.inner.log)))
        return Exec(0, json.dumps({"/tmp": entry(str(len(self.runs)))}).encode(), b"", 0.1)


def test_the_recorder_lists_the_calls_machine_around_the_call_as_root():
    inner = FakeInner(["default", "review"])
    recorder = ts.TmpRecorder(inner, CALLS)
    box = FakeBox(inner)
    assert recorder.execute(box, CALLS[1]) == ("out", False)
    assert recorder.after(box, CALLS[1], "out")[0] == "out"
    assert box.runs == [("default", "root", 0), ("default", "root", 2)]
    assert recorder.taken[2] == {"machine": "default", "before": {"/tmp": entry("1")}, "after": {"/tmp": entry("2")}}
    assert recorder.services() == {"default": {"mem_limit": ts.MEMORY_LIMIT, "image": "x"},
                                   "review": {"mem_limit": ts.MEMORY_LIMIT, "image": "x"}}
    assert recorder.task == "fake" and recorder.in_sandbox(CALLS[1])


def test_the_recorder_uses_the_agents_own_machine_where_it_has_one():
    recorder = ts.TmpRecorder(FakeInner(["a", "b", "grader"]), CALLS)
    assert [recorder.machine(c) for c in CALLS[1:3]] == ["a", "b"]


def test_write_refuses_to_replace_without_force(tmp_path):
    path = ts.write({"steps": []}, tmp_path / ts.FILE)
    try:
        ts.write({"steps": [1]}, path)
        raise AssertionError("replaced without force")
    except FileExistsError:
        pass
    assert json.loads(path.read_text()) == {"steps": []}
    ts.write({"steps": [1]}, path, force=True)
    assert json.loads(path.read_text()) == {"steps": [1]}
