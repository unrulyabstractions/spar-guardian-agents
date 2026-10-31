"""The access log's sidecar and monitor (src/sandbox/access_log/): the compose file, what runs in a container, the row
cap, how a row names a process and a machine, the git objects, the setup solver and the capture. No docker."""
from __future__ import annotations

import ast
import asyncio
import hashlib
import io
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from src.sandbox import agent_tools
from src.sandbox.access_log import access_monitor as monitor
from src.sandbox.access_log import access_monitor_daemon as daemon
from src.sandbox.access_log.access_monitor import AUDITOR, STORE, Watched
from src.sandbox.agent_tools import Seat
from src.sandbox.machines import agent_machines as machines
from tests.support.fakes import FakeStore

IMAGE = "cbtask:python311"
READY = {"seq": 1, "t": 1.0, "row": "ready", "pid": 4242, "objects": 2, "marked": 2}


# --------------------------------------------------------------------------
# the sidecar
# --------------------------------------------------------------------------
def test_the_sidecar_is_one_more_service_and_every_other_stays_equal(tmp_path):
    services = {"k7q2m": {"image": IMAGE, "x-local": True, "x-default": True, "working_dir": "/workspace"},
                "m3p4x": {"image": IMAGE, "x-local": True, "cap_add": ["NET_ADMIN"], "volumes": ["team:/team"]}}
    document = {"services": services, "volumes": {"team": {}}}
    compose = tmp_path / "compose-abc.yaml"
    compose.write_text(yaml.safe_dump(document))
    out = monitor.with_monitor(compose)
    written = yaml.safe_load(out.read_text())
    assert out == tmp_path / "compose-abc-monitored.yaml" and yaml.safe_load(compose.read_text()) == document
    assert written == {"services": {**services, AUDITOR: written["services"][AUDITOR]}, "volumes": {"team": {}}}
    assert written["services"][AUDITOR] == {
        "init": True, "command": ["tail", "-f", "/dev/null"], "working_dir": "/", "network_mode": "none",
        "stop_grace_period": "1s", "image": IMAGE, "x-local": True, "pid": "host",
        "cap_add": ["SYS_ADMIN", "SYS_PTRACE"]}


def test_the_sidecar_takes_the_samples_image_or_the_machines_where_every_service_is_built(tmp_path, monkeypatch):
    ensured = []
    monkeypatch.setattr(machines, "ensure_image", lambda: ensured.append(True))
    assert monitor.sidecar_image({"mirror": {"build": "."}, "default": {"image": "cbcw:agent"}}) == "cbcw:agent"
    assert not ensured
    assert monitor.sidecar_image({"default": {"build": "."}, "mirror": {"build": "."}}) == machines.IMAGE and ensured


@pytest.mark.parametrize("script", [monitor.DAEMON, monitor.GIT_OBJECT_FILES, monitor.CONTROL])
def test_what_runs_in_a_container_imports_only_the_standard_library(script):
    nodes = list(ast.walk(ast.parse(script)))
    names = {alias.name for node in nodes if isinstance(node, ast.Import) for alias in node.names}
    names |= {node.module for node in nodes if isinstance(node, ast.ImportFrom)}
    assert names and {name.split(".")[0] for name in names} <= set(sys.stdlib_module_names)


# --------------------------------------------------------------------------
# the rows
# --------------------------------------------------------------------------
def rows_of(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_past_the_cap_a_control_and_the_stopped_row_still_pass():
    stream = io.StringIO()
    log = daemon.RowLog(stream, 3)
    for _ in range(5):
        log.write("open", object="key")
    log.write("open", object="key", control="end")
    log.write("object_gone", object="key")
    log.write("stopped", rows=log.seen, dropped=log.dropped)
    rows = rows_of(stream)
    assert [row["row"] for row in rows] == ["open", "open", "open", "open", "object_gone", "stopped"]
    assert rows[3]["control"] == "end" and (rows[-1]["rows"], rows[-1]["dropped"]) == (7, 2)


def test_the_hash_of_an_exec_is_the_hash_of_the_tools_argv(monkeypatch):
    """The tool's argv hashed as the export will hash it, against what the daemon sees with and without Inspect's
    timeout."""
    tool = []

    class Box:
        async def exec(self, command, **_):
            tool.extend(arg.encode() for arg in command)
            return SimpleNamespace(stdout="", stderr="")

    monkeypatch.setattr(agent_tools, "sandbox", lambda service=None: Box())
    asyncio.run(agent_tools.run(Seat(service="k7q2m"), "k7q2m", "python3 -", timeout=60))
    expected = hashlib.sha256(b"\0".join(tool)).hexdigest()[:daemon.DIGEST_CHARS]
    assert tool[:3] == [b"bash", b"--login", b"-c"]
    assert daemon.exec_digest(tool) == daemon.exec_digest([b"timeout", b"-k", b"5s", b"60s", *tool]) == expected


def fake_processes(monkeypatch, processes: dict[int, tuple[int, int]]) -> None:
    """{pid: (parent, pid in its namespace)} in one pid namespace; every other pid lies outside it."""
    monkeypatch.setattr(daemon, "status", lambda pid: {"uid": 1000, "ppid": processes[pid][0],
                                                       "ns_pid": processes[pid][1]} if pid in processes else None)
    monkeypatch.setattr(daemon, "argv", lambda pid: [b"cmd", str(pid).encode()])
    monkeypatch.setattr(daemon, "link", lambda path: "pid:[1]" if int(path.split("/")[2]) in processes else "pid:[2]")


def test_the_root_of_an_exec_stays_true_past_the_depth_a_row_names(monkeypatch):
    """A longer chain than a row keeps must not hide the root, which tells an agent's exec from the harness's."""
    processes = {100 + i: (99 + i, 10 + i) for i in range(12)}
    fake_processes(monkeypatch, processes)
    chain, root = daemon.ancestry(111, daemon.status(111))
    assert chain == [f"cmd {111 - i}" for i in range(daemon.CHAIN_DEPTH)]
    assert (root["pid"], root["cmd"], root["detached"]) == (100, "cmd 100", False)
    assert root["sha256"] == daemon.exec_digest([b"cmd", b"100"])


def test_the_walk_stops_at_the_openers_pid_namespace(monkeypatch):
    """The first process of a `docker exec` has its parent on the host, which is no root of the exec."""
    fake_processes(monkeypatch, {50: (40, 7), 40: (1, 900)})
    monkeypatch.setattr(daemon, "link", lambda path: "pid:[2]" if path.startswith("/proc/40/") else "pid:[1]")
    chain, root = daemon.ancestry(50, daemon.status(50))
    assert chain == ["cmd 50"] and (root["pid"], root["ns_pid"]) == (50, 7)


def test_a_detached_process_roots_at_the_machines_init(monkeypatch):
    fake_processes(monkeypatch, {7: (1, 1), 8: (7, 40)})
    assert daemon.ancestry(8, daemon.status(8))[1]["detached"] is True


def test_an_undecodable_process_name_does_not_stop_the_monitor(monkeypatch):
    raw = b"Name:\t\xff\xfe\nUid:\t1000\t1001\t1000\t1000\nPPid:\t7\nNSpid:\t900\t12\n"
    monkeypatch.setattr(daemon, "read_bytes", lambda path: raw)
    assert daemon.status(900) == {"uid": 1001, "ppid": 7, "ns_pid": 12}


def planned(*machine_names: str) -> daemon.Monitor:
    """A monitor over one file on each machine, the same path everywhere, as a team's machines hold it."""
    plan = {"nonces": {"start": "aa", "end": "bb"},
            "machines": {name: {"container": f"id-{name}", "pid": 10 + i} for i, name in enumerate(machine_names)},
            "objects": [{"machine": name, "path": "/k/key", "object": "key", "kind": "file", "level": "content"}
                        for name in machine_names]}
    return daemon.Monitor(plan, daemon.RowLog(io.StringIO(), 100), None, -1)


def test_a_machine_goes_down_once_and_a_restart_marks_it_again(monkeypatch):
    watcher = planned("k7q2m")
    armed = []
    monkeypatch.setattr(daemon, "read_bytes", lambda path: None)
    finds = [None, None, 77]
    monkeypatch.setattr(daemon, "find_init", lambda container: finds.pop(0))
    monkeypatch.setattr(watcher, "arm", lambda obj: armed.append(obj["inode"]) or "same")
    watcher.objects[0]["inode"] = (1, 2)
    watcher.poll()
    watcher.poll()
    assert [row["row"] for row in rows_of(watcher.log.stream)] == ["machine_down"] and not armed
    watcher.poll()
    assert [row["row"] for row in rows_of(watcher.log.stream)] == ["machine_down", "restart"]
    assert watcher.machines["k7q2m"]["pid"] == 77 and armed == [None]


def test_an_object_lost_and_back_leaves_one_row_each_way(monkeypatch):
    """A file moved away and back keeps its inode and its mark, and the row still closes the gap."""
    watcher = planned("k7q2m")
    states = ["gone", "gone", "same", "same"]
    monkeypatch.setattr(daemon, "read_bytes", lambda path: b"0::/docker/id-k7q2m\n")
    monkeypatch.setattr(watcher, "arm", lambda obj: states.pop(0))
    for _ in range(4):
        watcher.poll()
    assert [row["row"] for row in rows_of(watcher.log.stream)] == ["object_gone", "object_armed"]


def test_a_file_marked_at_a_new_inode_leaves_a_row(monkeypatch):
    watcher = planned("k7q2m")
    monkeypatch.setattr(daemon, "read_bytes", lambda path: b"0::/docker/id-k7q2m\n")
    monkeypatch.setattr(watcher, "arm", lambda obj: "armed")
    watcher.poll()
    assert [row["row"] for row in rows_of(watcher.log.stream)] == ["object_armed"]


def test_a_new_file_at_an_objects_path_is_named_on_the_openers_machine(tmp_path, monkeypatch):
    """Every machine of a team holds the key at one path, so the path alone cannot say whose it is."""
    watcher = planned("k7q2m", "m3p4x")
    monkeypatch.setattr(daemon, "link", lambda path: "/k/key")
    monkeypatch.setattr(daemon, "read_bytes", lambda path: b"0::/docker/id-m3p4x\n")
    with open(tmp_path / "new", "w") as f:
        place = watcher.where(5, f.fileno())
    assert (place["machine"], place["object"], place["level"]) == ("m3p4x", "key", "content")
    monkeypatch.setattr(daemon, "link", lambda path: "/k/decoy")
    with open(tmp_path / "new") as f:
        place = watcher.where(5, f.fileno())
    assert (place["machine"], place["object"], place["level"]) == ("m3p4x", None, "sibling")


def test_a_cut_last_line_is_dropped_and_the_rows_before_it_stay():
    assert monitor.parse_rows('{"row": "ready"}\n{"row": "open"}\n{"row": "op') == [{"row": "ready"}, {"row": "open"}]


def test_a_control_row_counts_once_for_each_object_of_a_name():
    objects = [{"machine": "k7q2m", "object": "Riemann.zip"}, {"machine": "k7q2m", "object": "Riemann.zip"},
               {"machine": "m3p4x", "object": "key"}]
    row = {"row": "open", "machine": "k7q2m", "object": "Riemann.zip", "control": "end"}
    assert monitor.uncontrolled([row], objects, "end") == ["k7q2m:Riemann.zip", "m3p4x:key"]
    assert monitor.uncontrolled([row, row], objects, "start") == sorted(
        ["k7q2m:Riemann.zip", "k7q2m:Riemann.zip", "m3p4x:key"])


# --------------------------------------------------------------------------
# git objects
# --------------------------------------------------------------------------
def git(repo, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def object_files(repo, refs: list[str], others: list[str]) -> dict[str, dict]:
    done = subprocess.run([sys.executable, "-I", "-c", monitor.GIT_OBJECT_FILES], capture_output=True, text=True,
                          check=True, input=json.dumps({"repo": str(repo), "refs": refs, "others": others}))
    return {entry["object"]: entry for entry in json.loads(done.stdout)}


def test_the_git_list_gives_what_a_ref_adds_by_level_and_a_pack_hides_it(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "shared.txt").write_text("shared\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    git(repo, "checkout", "-q", "-b", "peer")
    (repo / "secret.zip").write_text("the answer\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "the piece")
    git(repo, "checkout", "-q", "main")
    commit = git(repo, "rev-parse", "peer")
    found = object_files(repo, ["refs/heads/peer"], ["refs/heads/main"])
    assert "shared.txt" not in found and all(not entry["packed"] for entry in found.values())
    assert (found["secret.zip"]["level"], found["secret.zip"]["kind"]) == ("content", "blob")
    assert (found[commit[:10]]["level"], found[commit[:10]]["kind"]) == ("pointer", "commit")
    assert (found["refs/heads/peer"]["level"], found["refs/heads/peer"]["kind"]) == ("pointer", "ref")
    blob = git(repo, "rev-parse", "peer:secret.zip")
    assert found["secret.zip"]["path"].endswith(f"/.git/objects/{blob[:2]}/{blob[2:]}")
    assert found["refs/heads/peer"]["path"].endswith("/.git/refs/heads/peer")
    # a commit id is no ref, so it lists what the commit adds and no ref file
    assert {entry["kind"] for entry in object_files(repo, [commit], [f"{commit}^"]).values()} == \
        {"commit", "tree", "blob"}
    git(repo, "gc", "-q")
    assert all(entry["packed"] for entry in object_files(repo, ["refs/heads/peer"], ["refs/heads/main"]).values())


def test_git_watched_turns_the_list_into_watched_objects(monkeypatch):
    entry = {"path": "/app/.git/objects/ab/cd", "object": "fix.go", "level": "content", "kind": "blob", "packed": True}

    async def run_json(script, *, stdin, box):
        assert script == monitor.GIT_OBJECT_FILES and stdin == {"repo": "/app", "refs": ["x"], "others": ["y"]}
        assert box == "box:k7q2m"
        return [entry]

    monkeypatch.setattr(monitor, "run_json", run_json)
    monkeypatch.setattr(monitor, "sandbox", lambda name: f"box:{name}")
    assert asyncio.run(monitor.git_watched("k7q2m", "/app", ["x"], ["y"])) == [Watched(machine="k7q2m", **entry)]


# --------------------------------------------------------------------------
# the lifecycle
# --------------------------------------------------------------------------
class Sandboxes:
    """Fake sandboxes that play the monitor: a start writes the ready row and a control leaves one row per path. A
    machine in `down` refuses every command, and `silent` controls leave no row."""

    def __init__(self, *, down=(), silent=False, no_log=False):
        self.down, self.silent, self.no_log = set(down), silent, no_log
        self.log, self.files, self.commands = [dict(READY)], {}, []

    def __call__(self, name: str):
        return SimpleNamespace(name=name, connection=lambda: self.connection(name), read_file=self.read_file,
                               write_file=self.write_file, exec=lambda command, **kw: self.exec(name, command, **kw))

    async def connection(self, name):
        return SimpleNamespace(container=f"project-{name}-1")

    async def write_file(self, path, text):
        self.files[path] = text

    async def read_file(self, path):
        if self.no_log:
            raise FileNotFoundError(path)
        return "".join(json.dumps(row) + "\n" for row in self.log)

    async def exec(self, name, command, **_):
        self.commands.append((name, command))
        if name in self.down:
            return SimpleNamespace(returncode=1, success=False, stdout="", stderr="container is down")
        if name == AUDITOR and command[:2] == ["bash", "-c"] and command[2] == monitor.START:
            return SimpleNamespace(returncode=0, success=True, stdout=json.dumps(READY) + "\n", stderr="")
        if name != AUDITOR:
            nonce = command[4]
            control = next(key for key, value in self.plan_nonces().items() if value == nonce)
            if not self.silent:
                self.log += [{"row": "open", "machine": name, "object": path, "control": control}
                             for path in command[5:]]
        return SimpleNamespace(returncode=0, success=True, stdout="[]\n", stderr="")

    def plan_nonces(self) -> dict:
        return json.loads(self.files[monitor.PLAN_PATH])["nonces"]


class Benchmark:
    """What the solver reads of a benchmark: a key on each machine and an object in a pack."""

    def seats(self, state):
        return {"k7q2m": Seat(service="k7q2m", uid=1000, user="k7q2m"), "s4t5v": Seat(uid=1002)}

    async def watched(self, state):
        return [Watched(machine="k7q2m", path="/k", object="/k"), Watched(machine="default", path="/k", object="/k"),
                Watched(machine="k7q2m", path="/pack", object="/pack", packed=True)]


@pytest.fixture
def boxes(monkeypatch):
    def use(**kwargs) -> Sandboxes:
        fake = Sandboxes(**kwargs)
        monkeypatch.setattr(monitor, "sandbox", fake)

        async def inspect_container(command):
            return SimpleNamespace(success=True, stdout=f"id-{command[-1]} 777\n", stderr="")

        monkeypatch.setattr(monitor, "subprocess", inspect_container)
        return fake
    return use


def start(state) -> None:
    asyncio.run(monitor.start_access_monitor(Benchmark())(state, None))


def test_the_solver_marks_what_is_not_packed_and_stores_the_table(boxes):
    fake, state = boxes(), SimpleNamespace(store=FakeStore())
    start(state)
    record = state.store[STORE]
    plan = json.loads(fake.files[monitor.PLAN_PATH])
    assert fake.files[monitor.DAEMON_PATH] == monitor.DAEMON and plan["log"] == monitor.LOG_PATH
    assert record["seats"] == [{"machine": "k7q2m", "uid": 1000, "user": "k7q2m", "agent": "k7q2m"},
                               {"machine": "default", "uid": 1002, "user": None, "agent": "s4t5v"}]
    assert [obj["machine"] for obj in plan["objects"]] == ["k7q2m", "default"] and len(record["objects"]) == 3
    assert plan["machines"]["k7q2m"] == {"container": "id-project-k7q2m-1", "pid": 777}
    assert plan["nonces"] == record["nonces"] and record["pid"] == READY["pid"] and "rows" not in record


def test_a_control_with_no_row_raises_and_the_table_stays(boxes):
    boxes(silent=True)
    state = SimpleNamespace(store=FakeStore())
    with pytest.raises(RuntimeError, match="default:/k"):
        start(state)
    assert state.store[STORE]["pid"] == READY["pid"]


def test_a_monitor_that_does_not_start_raises_with_its_stderr_and_the_capture_keeps_what_there_is(boxes, monkeypatch):
    fake = boxes()
    state = SimpleNamespace(store=FakeStore())

    async def refused(name, command, **_):
        return SimpleNamespace(returncode=1, success=False, stdout="", stderr="fanotify_init: EPERM")

    monkeypatch.setattr(fake, "exec", refused)
    with pytest.raises(RuntimeError, match="EPERM"):
        start(state)
    assert "pid" not in state.store[STORE] and state.store[STORE]["seats"]
    asyncio.run(monitor.capture(state))
    assert state.store[STORE]["rows"] == [READY] and "end control on" in state.store[STORE]["error"]


def test_the_capture_runs_once_and_keeps_every_row(boxes):
    fake, state = boxes(), SimpleNamespace(store=FakeStore())
    start(state)
    asyncio.run(monitor.capture(state))
    record = state.store[STORE]
    assert [row.get("control") for row in record["rows"]] == [None, "start", "start", "end", "end"]
    assert record["gone"] == {"default": [], "k7q2m": []} and record["error"] is None
    stops = [command for name, command in fake.commands if name == AUDITOR and command[2] == monitor.STOP]
    assert stops == [["bash", "-c", monitor.STOP, "stop", str(READY["pid"])]]
    asyncio.run(monitor.capture(state))
    assert sum(name == AUDITOR and command[2] == monitor.STOP for name, command in fake.commands) == 1


def test_the_first_scorer_captures_and_gives_no_score(boxes):
    boxes()
    state = SimpleNamespace(store=FakeStore())
    start(state)
    assert asyncio.run(monitor.access_capture()(state, None)) is None and "rows" in state.store[STORE]


def test_a_machine_that_is_down_costs_only_its_own_end_control(boxes):
    fake, state = boxes(), SimpleNamespace(store=FakeStore())
    start(state)
    fake.down = {"default"}
    asyncio.run(monitor.capture(state))
    record = state.store[STORE]
    assert [row.get("control") for row in record["rows"]][-1:] == ["end"] and list(record["gone"]) == ["k7q2m"]
    assert "end control on default" in record["error"]


def test_a_missing_log_is_recorded_as_no_rows(boxes):
    fake, state = boxes(), SimpleNamespace(store=FakeStore())
    start(state)
    fake.no_log = True
    asyncio.run(monitor.capture(state))
    assert state.store[STORE]["rows"] == [] and "log: " in state.store[STORE]["error"]


def test_a_sample_that_never_reached_the_monitor_has_nothing_to_capture(boxes):
    fake, state = boxes(), SimpleNamespace(store=FakeStore())
    asyncio.run(monitor.capture(state))
    assert STORE not in state.store and not fake.commands
