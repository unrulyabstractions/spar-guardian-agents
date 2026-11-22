"""The run's own /tmp snapshots (src/sandbox/live_tmp_capture.py), their export (src/export/_export_tmp_snapshots.py),
the listing they share with the replay (src/sandbox/tmp_listing.py) and the turn rules' hook (mark_machine, AFTER_CALL,
_settle naming the shots). No Docker: a fake machine answers each listing."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from types import SimpleNamespace

from inspect_ai.model import ChatMessageAssistant
from inspect_ai.tool import ToolCall, ToolDef

from src.export import _export_tmp_snapshots as export
from src.replay.recorded_calls import RecordedCall
from src.replay.tmp_snapshots import decode
from src.sandbox import live_tmp_capture as cap
from src.sandbox import tmp_listing
from src.swarm import turn_rules as rules
from src.swarm.turn_rules import _turn_rounds
from src.swarm.turn_rules._turn_state import Live
from tests.support.fakes import FakeStore


def entry(text: str) -> dict:
    return {"type": "file", "size": len(text), "mode": "-rw-r--r--", "owner": "k7q2m", "uid": 1001, "group": "k7q2m",
            "gid": 1001, "mtime": 1.0, "text": text}


ROOT = {"type": "dir", "size": 4096, "mode": "drwxrwxrwt", "owner": "root", "uid": 0, "group": "root", "gid": 0,
        "mtime": 1.0}


# ------------------------------------------------------------------------------------------------ the listing

def listed(spec: dict) -> str:
    return subprocess.run([sys.executable, "-I", "-c", tmp_listing.LISTING], capture_output=True, text=True,
                          check=True, input=json.dumps(spec)).stdout


def test_the_listing_never_opens_a_watched_file_nor_its_neighbours(tmp_path):
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "candidate.md").write_text("the reference")
    (ref / "beside.md").write_text("a neighbour")
    (tmp_path / "notes.txt").write_text("mine")
    spec = tmp_listing.listing_spec([str(tmp_path)], no_read=[str(ref / "candidate.md")], no_read_dirs=[str(ref)],
                                    lines=True)
    found, truncated = tmp_listing.parse_lines(listed(spec))
    assert not truncated
    for name in ("candidate.md", "beside.md"):
        got = found[str(ref / name)]
        assert got["unread"] is True and "sha256" not in got and "text" not in got and got["size"] > 0
    assert found[str(tmp_path / "notes.txt")]["text"] == "mine" and found[str(ref)]["type"] == "dir"
    # one line per entry and a count: an Inspect SandboxEvent keeps 20 lines, so a long listing stays out of the log
    assert len(listed(spec).splitlines()) == len(found) + 1


def test_the_listing_stops_at_its_cap_and_a_cut_output_is_refused(tmp_path):
    for i in range(5):
        (tmp_path / f"f{i}").write_text("x")
    found, truncated = tmp_listing.parse_lines(listed(tmp_listing.listing_spec([str(tmp_path)], lines=True,
                                                                               max_entries=3)))
    assert truncated and len(found) == 3
    out = listed(tmp_listing.listing_spec([str(tmp_path)], lines=True))
    try:
        tmp_listing.parse_lines("\n".join(out.splitlines()[:-2]))
    except RuntimeError:
        pass
    else:
        raise AssertionError("a listing cut before its count line must be refused")


# ------------------------------------------------------------------------------------------------ the capture

class FakeMachine:
    """A machine whose listings come from a list of states; a state of None fails its exec."""

    def __init__(self, states: list[dict | None], users: dict | None = None) -> None:
        self.states, self.users, self.specs, self.execs = list(states), users or {}, [], []

    async def exec(self, argv, input=None, user=None, timeout=None, concurrency=True):
        self.execs.append({"user": user, "concurrency": concurrency})
        if argv[-1] == tmp_listing.USERS:
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.users) + "\n", stderr="")
        self.specs.append(json.loads(input))
        state = self.states.pop(0)
        if state is None:
            return SimpleNamespace(returncode=1, stdout="", stderr="python3: not found")
        lines = [json.dumps([path, item]) for path, item in state.items()]
        return SimpleNamespace(returncode=0, stdout="\n".join([*lines, json.dumps({"entries": len(state),
                                                                                    "truncated": False})]), stderr="")


def captured(monkeypatch, machines: dict, access: dict | None = None) -> FakeStore:
    st = FakeStore()
    if access is not None:
        st.set(cap.ACCESS_STORE, access)
    monkeypatch.setattr(cap, "store", lambda: st)
    monkeypatch.setattr(cap, "sandbox", lambda service=None: machines[service])
    monkeypatch.setattr(cap, "_SAMPLES", {})
    return st


def test_setup_lists_each_machine_and_every_call_after_it_keeps_its_change(monkeypatch):
    s0 = {"/tmp": ROOT, "/tmp/seed.txt": entry("seed")}
    s1 = {**s0, "/tmp/x": entry("one")}
    s3 = {"/tmp": ROOT, "/tmp/x": entry("two")}
    box = FakeMachine([s0, s1, None, s3], users={"k7q2m": {"uid": 1001, "gid": 1001, "groups": ["k7q2m"]}})
    access = {"objects": [{"machine": "default", "path": "/tmp/grading/ref/ab/c.md", "kind": "file", "packed": False},
                          {"machine": "default", "path": "/app/.git/objects/1a", "kind": "blob", "packed": False}]}
    st = captured(monkeypatch, {None: box}, access)
    setup = cap.tmp_capture_setup({"default": None}, {"k7q2m": "default"})
    asyncio.run(setup(SimpleNamespace(), None))
    held = st[cap.STORE]
    assert held["on"] and held["initial"]["default"]["state"] == s0 and held["users"]["default"]["k7q2m"]["uid"] == 1001
    assert box.specs[0]["no_read"] == ["/tmp/grading/ref/ab/c.md"] and box.specs[0]["no_read_dirs"] == [
        "/tmp/grading/ref/ab"]
    assert all(e["user"] == "root" and e["concurrency"] is False for e in box.execs)
    runs = [{"tool": "shell", "t0": 1.0, "t1": 2.0, "machines": {None: box}} for _ in range(3)]
    for run in runs:
        asyncio.run(cap.after_call("k7q2m", run))
    assert [run["tmp"] for run in runs] == [[1], [2], [3]]
    first, failed, third = held["shots"]
    assert first["change"] == {"/tmp/x": entry("one")} and first["ms"] is not None
    assert "change" not in failed and "python3: not found" in failed["error"] and held["error_count"] == 1
    # the shot after a failed one is against the last listing that worked
    assert third["change"] == {"/tmp/seed.txt": None, "/tmp/x": entry("two")}


def test_a_listing_that_raises_is_recorded_and_the_call_goes_on(monkeypatch):
    class Broken:
        async def exec(self, *args, **kwargs):
            raise TimeoutError("the exec timed out")

    st = captured(monkeypatch, {None: Broken()})
    asyncio.run(cap.tmp_capture_setup({"default": None}, {"a": "default"})(SimpleNamespace(), None))
    run = {"tool": "shell", "t0": 1.0, "t1": 2.0, "machines": {None: Broken()}}
    asyncio.run(cap.after_call("a", run))
    held = st[cap.STORE]
    assert held["initial"] == {} and held["error_count"] == 2 and "timed out" in held["shots"][0]["error"]


def test_no_capture_without_the_setup(monkeypatch):
    box = FakeMachine([{"/tmp": ROOT}])
    st = captured(monkeypatch, {None: box})
    run = {"tool": "shell", "machines": {None: box}}
    asyncio.run(cap.after_call("a", run))
    assert "tmp" not in run and box.execs == [] and cap.STORE not in st


def test_machines_of_a_roster():
    seats = {"k7q2m": cap.Seat(user="k7q2m", service="k7q2m"), "m3p4x": cap.Seat(user="m3p4x", service="m3p4x")}
    assert cap.machines_of(seats, list(seats)) == ({"k7q2m": "k7q2m", "m3p4x": "m3p4x"},
                                                   {"k7q2m": "k7q2m", "m3p4x": "m3p4x"})
    assert cap.machines_of(None, ["a"]) == ({"default": None}, {"a": "default"})


# ------------------------------------------------------------------------------------------------ the turn rules' hook

def gated(monkeypatch):
    live = Live()
    turn = SimpleNamespace(round_time_s=0, time_limit_s=None, started_at=None, ended={}, calls_per_round=10,
                           rounds_used={})
    st = FakeStore()
    monkeypatch.setattr(_turn_rounds, "live_record", lambda: live)
    monkeypatch.setattr(_turn_rounds, "current_round", lambda: 1)
    monkeypatch.setattr(_turn_rounds, "store_as", lambda model: turn if model is rules.TurnState
                        else SimpleNamespace(agent=None))
    monkeypatch.setattr(_turn_rounds, "store", lambda: st)
    return live, st


def shell_tool(mark: bool):
    async def execute(command: str) -> str:
        if mark:
            rules.mark_machine("k7q2m", "BOX")
        return f"ran {command}"
    return ToolDef(execute, name="shell", description="d", parameters={"command": "c"})


def test_a_call_that_ran_a_command_takes_its_shot_and_its_batch_names_it(monkeypatch):
    live, st = gated(monkeypatch)
    seen = []

    async def hook(agent, run):
        seen.append((agent, dict(run["machines"]), run["t1"] is not None))
        run.setdefault("tmp", []).append(7)

    async def broken(agent, run):
        raise RuntimeError("never reaches the call")

    monkeypatch.setattr(_turn_rounds, "AFTER_CALL", [broken, hook])
    wrapped = _turn_rounds.counted("a01")(shell_tool(mark=True))
    assert asyncio.run(wrapped.tool(command="ls")) == "ran ls"
    assert seen == [("a01", {"k7q2m": "BOX"}, True)]
    host = _turn_rounds.counted("a01")(shell_tool(mark=False))       # no machine: no shot
    asyncio.run(host.tool(command="board"))
    assert len(seen) == 1
    reply = ChatMessageAssistant(content="", tool_calls=[ToolCall(id="call_1", function="shell", arguments={}),
                                                          ToolCall(id="call_2", function="shell", arguments={})])
    _turn_rounds._settle(live.rounds["a01"], [reply])
    assert st[rules.TMP_CALLS_STORE] == {"7": "call_1"}


# ------------------------------------------------------------------------------------------------ the export

def call(n, agent, k, t0, t1, tool="shell"):
    return RecordedCall(n=n, agent=agent, k=k, id=f"c{n}", tool=tool, arguments={}, result="", error=None, t0=t0, t1=t1)


def shot(seq, agent, t, change, machine="default", **extra):
    return {"seq": seq, "machine": machine, "agent": agent, "tool": "shell", "t0": t - 1, "t1": t, "t": t, "ms": 40.0,
            "change": change, "truncated": False, **extra}


def test_the_export_orders_by_call_decodes_to_each_shot_and_names_what_overlapped():
    s0 = {"/tmp": ROOT}
    held = {"on": True, "dirs": tmp_listing.TMP_DIRS, "machines": {"default": None},
            "agents": {"a": "default", "b": "default"}, "users": {"default": {}},
            "initial": {"default": {"t": 10.0, "state": s0, "truncated": False}},
            # b's call (n2) ended and listed before a's (n1), which started first and ran beside it
            "shots": [shot(1, "b", 13.0, {"/tmp/b": entry("b")}), shot(2, "a", 15.0, {"/tmp/a": entry("a")}, t0=11.0),
                      {"seq": 3, "machine": "default", "agent": "a", "tool": "shell", "t0": 19.0, "t1": 20.0,
                       "t": 20.0, "error": "RuntimeError: rc=1"},
                      shot(4, "b", 31.0, {"/tmp/b": None})],
            "errors": ["x"], "error_count": 1}
    calls = [call(1, "a", 0, 11.0, 15.0), call(2, "b", 0, 12.0, 13.0), call(3, "a", 1, 19.0, 20.0),
             call(4, "b", 1, 30.0, 31.0), call(5, "a", 2, 32.0, 32.5, tool="board_read")]
    named = {"1": "c2", "2": "c1", "3": "c3"}            # shot 4 has no name: matched by agent, tool and start
    jobs = {"j1": {"id": "j1", "agent": "a", "machine": None, "call_id": "c3", "started": 19.5, "ended": None}}
    doc = export.build(held, calls, named, jobs, "run", "creative_writings")
    steps = {s["n"]: s for s in doc["steps"]}
    assert [s["n"] for s in doc["steps"]] == [1, 2, 3, 4, 5] and doc["clock"] == "run" and doc["schema"] == 2
    # a's window opens at b's shot: b had ended, but a ran before it, so part of a's change may sit in b's shot
    assert steps[1]["overlapping"] == [] and steps[1]["began_before_window"] and steps[1]["attribution"] == "shared"
    assert steps[2]["overlapping"] == [1] and steps[2]["window"] == [10.0, 13.0]
    assert steps[3]["ran"] is False and steps[3]["snapshot_error"] == "RuntimeError: rc=1"
    assert steps[4]["jobs"] == ["j1"] and steps[4]["attribution"] == "shared"
    assert steps[5]["ran"] is False and "snapshot_error" not in steps[5]
    full = decode(doc)
    assert full[1]["after"] == {**s0, "/tmp/b": entry("b"), "/tmp/a": entry("a")}
    assert full[2]["before"] == s0 and full[2]["after"] == {**s0, "/tmp/b": entry("b")}
    assert full[4]["after"] == {**s0, "/tmp/a": entry("a")}
    assert doc["capture"]["errors"] == 1 and doc["capture"]["ms"]["median"] == 40.0


def test_a_lone_call_on_its_machine_is_its_own():
    held = {"on": True, "machines": {"k7q2m": "k7q2m"}, "agents": {"k7q2m": "k7q2m"},
            "initial": {"k7q2m": {"t": 1.0, "state": {"/tmp": ROOT}, "truncated": False}},
            "shots": [shot(1, "k7q2m", 3.0, {"/tmp/x": entry("x")}, machine="k7q2m")]}
    doc = export.build(held, [call(1, "k7q2m", 0, 2.0, 3.0)], {"1": "c1"}, None, "run", "software_engineering")
    [step] = doc["steps"]
    assert step["attribution"] == "call" and step["before"] == {"/tmp": ROOT} and step["after"] == {
        "/tmp/x": entry("x")}


def view_of(config: dict | None, held: dict | None = None):
    stores = {cap.STORE: held}
    return SimpleNamespace(metadata={"cheatbench": {"config": config}}, store_get=lambda key: stores.get(key))


def test_the_block_follows_the_switch_and_a_failure_is_its_error(tmp_path):
    assert export.write_tmp_snapshots(view_of({}), tmp_path) is None              # a run before the switch
    assert export.write_tmp_snapshots(view_of({"tmp_snapshots": False}), tmp_path) == {"status": "off"}
    assert export.write_tmp_snapshots(view_of({"tmp_snapshots": True}), tmp_path) == {"status": "none"}
    block = export.write_tmp_snapshots(view_of({"tmp_snapshots": True}, {"on": True, "shots": []}), tmp_path)
    assert block["status"] == "error" and not (tmp_path / export.FILE).exists()


def test_the_switch_is_on_by_default():
    from src.configuration.swarm_config import SwarmConfig
    assert SwarmConfig().tmp_snapshots is True and SwarmConfig(tmp_snapshots=False).tmp_snapshots is False
