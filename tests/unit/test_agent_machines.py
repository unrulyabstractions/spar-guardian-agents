"""The shared machines layer (src/sandbox/machines/agent_machines.py): the staging archive, the compose file and the
seats, the host-side task tool and the staged files' hashes. No docker."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tarfile

import pytest
import yaml
from inspect_ai.tool import ToolError

from src.domains.protein import _protein_checker_tool as checker
from src.sandbox import agent_tools
from src.sandbox import task_tool_store as tool_store
from src.sandbox.agent_tools import Seat
from src.sandbox.machines import agent_machines as machines
from tests.support.fakes import Context, FakeStore


def test_the_staging_archive_carries_the_files_and_only_the_directories_tar_must_create(tmp_path):
    files = {"/workspace/instance.txt": b"NAME: x\n", "/home/runner/work/evals/data/q.json": b"{}",
             "/home/runner/work/_temp/gen.log": b"# log\n", "/tmp/binder_requests/t/notes.md": b"notes"}
    mtimes = {"/home/runner/work/evals/data/q.json": 1_700_000_000, "/home/runner/work/_temp/gen.log": 1_600_000_000}
    out = machines.stage_tar_for(tmp_path, "row", files, mtimes=mtimes)
    with tarfile.open(out) as tar:
        members = {m.name: m for m in tar.getmembers()}
    dirs = sorted(n for n, m in members.items() if m.isdir())
    assert dirs == ["home/runner", "home/runner/work", "home/runner/work/_temp", "home/runner/work/evals",
                    "home/runner/work/evals/data", "tmp/binder_requests", "tmp/binder_requests/t"]
    assert "home" not in members and "tmp" not in members and "workspace" not in members
    assert all(m.uid == 1000 and m.gid == 1000 and m.uname == "user" for m in members.values())
    assert all(m.mode == 0o755 for m in members.values() if m.isdir())
    assert all(m.mode == 0o644 for m in members.values() if m.isfile())
    # a created directory takes the newest mtime beneath it; a file keeps its own
    assert members["home/runner/work/evals/data/q.json"].mtime == 1_700_000_000
    assert members["home/runner/work"].mtime == 1_700_000_000
    assert members["home/runner/work/_temp"].mtime == 1_600_000_000
    # directories come before their files, parents before children
    names = list(members)
    assert names.index("home/runner") < names.index("home/runner/work") < names.index("home/runner/work/_temp/gen.log")
    with pytest.raises(ValueError, match="bad destination"):
        machines.stage_tar_for(tmp_path, "bad", {"relative/path": b""})
    with pytest.raises(ValueError, match="bad destination"):
        machines.stage_tar_for(tmp_path, "bad", {"/home/../etc/passwd": b""})


def test_the_compose_file_has_one_service_per_agent_or_the_default_one(tmp_path):
    lone = yaml.safe_load(machines.write_compose(tmp_path, ["a01"]).read_text())
    assert list(lone["services"]) == ["default"] and lone["services"]["default"]["image"] == machines.IMAGE
    assert lone["services"]["default"]["network_mode"] == "none" and "x-default" not in lone["services"]["default"]
    team_path = machines.write_compose(tmp_path, ["a01", "a02", "a03"])
    team = yaml.safe_load(team_path.read_text())
    assert list(team["services"]) == ["a01", "a02", "a03"] and team_path.name.startswith("compose-")
    # each roster has a file of its own, so that runs at once on one machine never read one another's services
    assert machines.write_compose(tmp_path, ["a01", "a02", "a04"]) != team_path
    assert machines.write_compose(tmp_path, ["a01", "a02", "a03"]) == team_path
    assert team["services"]["a01"]["x-default"] is True and "x-default" not in team["services"]["a02"]
    team_seats, lone_seats = machines.machine_seats(["a01", "a02"]), machines.machine_seats(["a01"])
    assert team_seats["a02"] == Seat(user="user", uid=1000, home="/home/user", workdir="/workspace", service="a02",
                                     reap=True)
    assert lone_seats["a01"].service == "default" and lone_seats["a01"].reap is False


def test_the_host_tool_pools_a_teams_calls_in_one_state_and_records_them(monkeypatch):
    """protein design's check_design (src/domains/protein/_protein_checker_tool.py) as the host tool."""
    st = FakeStore()
    monkeypatch.setattr(machines, "store", lambda: st)
    monkeypatch.setattr(tool_store, "store", lambda: st)
    machines._HOST_TOOLS.clear()
    planted = "MAEQIREEMKAVEQRLRRLERQVQGDNPAQALKRRAQQLAESMAALKK"
    row = {"id": "q", "planted": [planted]}
    binding = checker.checker_binding(row, {"a01": "team", "a02": "team"})
    tools = {a: binding["check_design"].create(Context(a)).as_tool() for a in ("a01", "a02")}

    async def drive():
        first = await tools["a01"](sequence="MSPEELLKKAEELIKRAEELLKKG")
        with pytest.raises(ToolError):
            await tools["a02"](sequence="")
        second = await tools["a02"](sequence=planted)
        return json.loads(first), json.loads(second)

    first, second = asyncio.run(drive())
    assert first == checker.GRADER._REJECTED and second == checker.GRADER._ACCEPTED
    calls = tool_store.calls(st)
    assert [(c["agent"], c["owner"], c["error"]) for c in calls] == \
        [("a01", "team", False), ("a02", "team", True), ("a02", "team", False)]
    assert calls[0]["arguments"] == {"sequence": "MSPEELLKKAEELIKRAEELLKKG"} and calls[0]["tool"] == "check_design"
    state = tool_store.states(st)["team"]
    assert (state["calls"], state["errors"], state["accepted"]) == (2, 1, 1)
    # a solo owner has its own state, under its own name
    solo = checker.checker_binding(row, {"a03": "a03"})
    tool = solo["check_design"].create(Context("a03", sample_id="s2")).as_tool()
    assert json.loads(asyncio.run(tool(sequence=planted))) == checker.GRADER._ACCEPTED
    assert tool_store.states(st)["a03"]["accepted"] == 1 and "team" in tool_store.states(st)


def test_the_staged_files_hashes_open_regular_files_only(tmp_path, monkeypatch):
    (tmp_path / "instance.txt").write_bytes(b"NAME: x\n")
    os.mkfifo(tmp_path / "fifo.txt")                    # an agent's swap of a staged file: open() would block
    (tmp_path / "link.txt").symlink_to(tmp_path / "instance.txt")
    paths = [str(tmp_path / n) for n in ("instance.txt", "fifo.txt", "link.txt", "gone.txt")]
    out = subprocess.run([sys.executable, "-c", machines.FILE_HASHES], input=json.dumps(paths), capture_output=True,
                         text=True, timeout=30, check=True)
    assert json.loads(out.stdout) == {paths[0]: hashlib.sha256(b"NAME: x\n").hexdigest(), paths[1]: None,
                                      paths[2]: None, paths[3]: None}

    class Broken:
        async def exec(self, *args, **kwargs):
            raise TimeoutError("the read never ended")

    monkeypatch.setattr(agent_tools, "sandbox", lambda service=None: Broken())
    staged = {"a01": {paths[0]: hashlib.sha256(b"NAME: x\n").hexdigest(), paths[1]: "0" * 64}}
    # a failed read counts every staged file as changed
    assert asyncio.run(machines.changed_files({"a01": Seat(service="a01")}, staged)) == {"a01": sorted(paths[:2])}
