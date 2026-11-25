"""The guard's progress adapter on recorded inputs: box_probe.py's reading of a fake /proc and a fake clone (the
launcher and its cells by their command lines, the newest write, the runs stored, the key), its verdict on each state of
a box (busy, runs ended, launcher dead, nothing written for long, another box, no clone, an exhausted key, a setup live
or failed), and .claude/box_progress.sh end to end through a stand-in ssh that runs the probe on the fake box."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts.box import box_probe
from scripts.box.fleet_layout import ROOT

PROBE = Path(box_probe.__file__)
LAUNCHER = [".venv/bin/python", "-m", "scripts.study.run_study", "studies/plans/x.yaml", "--yes", "--only", "a"]
CELL = ["uv", "run", "python", "-m", "studies.cells.swe.ian_flipt_minimal", "--model", "m", "--no-judge", "--name",
        "a_none_s1", "--set", "seed=1"]
CELL_PYTHON = ["/root/work/.venv/bin/python3", "-m", "studies.cells.swe.ian_flipt_minimal", "--name", "a_none_s1"]
SHELL = ["bash", "-c", "STUDY=x exec uv run python -m studies.cells.swe.ian_flipt_minimal --name b_none_s1"]
SETUP = ["bash", "/root/ship/box_setup_remote.sh", "c0ffee", "3.11.13"]
BOOT, TICKS = 1_700_000_000, os.sysconf("SC_CLK_TCK")


def fake_proc(root: Path, processes: dict[int, list[str]], started_s: int = 100) -> str:
    """A /proc with these processes, each started started_s after boot."""
    proc = root / "proc"
    proc.mkdir(parents=True, exist_ok=True)
    (proc / "stat").write_text(f"cpu  1 2 3\nbtime {BOOT}\n")
    for pid, args in processes.items():
        (proc / str(pid)).mkdir()
        (proc / str(pid) / "cmdline").write_bytes(b"\0".join(arg.encode() for arg in args) + b"\0")
        fields = ["S"] + ["0"] * 18 + [str(started_s * TICKS)] + ["0"] * 5
        (proc / str(pid) / "stat").write_text(f"{pid} (py (thon)) {' '.join(fields)}\n")
    return str(proc)


@pytest.fixture
def box(tmp_path):
    """A fake box: its home with box_id, the clone with one stored run and a cell's console log."""
    home, repo = tmp_path / "root", tmp_path / "root" / "work"
    (repo / "runs" / "x" / "a_none_s1" / "processed").mkdir(parents=True)
    (repo / "runs" / "x" / "a_none_s1" / "processed/run.json").write_text("{}")
    (repo / "logs" / "x" / "run_study").mkdir(parents=True)
    (repo / "logs" / "x" / "run_study" / "b_none_s1.log").write_text("working\n")
    (home / "box_id").write_text("7\n")
    return {"home": str(home), "repo": str(repo), "tmp": tmp_path}


def age(path: str, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then))


def every_file(repo: str) -> list[str]:
    return [os.path.join(d, name) for d, _, names in os.walk(repo) for name in names]


def test_the_probe_finds_the_launcher_and_each_cell_once_by_its_command_line(box):
    proc = fake_proc(box["tmp"], {10: LAUNCHER, 11: CELL, 12: CELL_PYTHON, 13: SHELL, 14: ["sleep", "60"]})
    reading = box_probe.probe(box["repo"], box["home"], proc)
    assert reading["launcher"] == [10] and reading["cells"] == {"a_none_s1": 11}
    assert reading["launcher_started"] == BOOT + 100
    assert (reading["box_id"], reading["work"], reading["stored"], reading["key"]) == ("7", True, {"x": 1}, "unread")
    assert abs(reading["newest"] - time.time()) < 60


def test_the_key_is_read_from_the_box_env_and_only_what_is_left_comes_back(box):
    env = Path(box["home"]) / ".box_env"
    assert box_probe.key_left(str(env)) == "nokey"
    env.write_text("export OPENROUTER_API_KEY=sk-or-box-key-for-one-launch\n")
    seen = []

    def opener(request, timeout):
        seen.append(request.headers["Authorization"])
        return io.BytesIO(json.dumps({"data": {"limit_remaining": 0.0}}).encode())

    assert box_probe.key_left(str(env), opener) == 0.0 and seen == ["Bearer sk-or-box-key-for-one-launch"]
    assert box_probe.key_left(str(env), lambda request, timeout: io.BytesIO(b'{"data": {}}')) == "inf"
    assert box_probe.key_left(str(env), lambda request, timeout: 1 / 0) == "err"


def test_the_probe_reads_the_setup_by_its_pid_file_and_its_command_line(box):
    home = Path(box["home"])
    assert box_probe.probe(box["repo"], box["home"], fake_proc(box["tmp"] / "p0", {}))["setup"] == {
        "live": False, "log": None, "exit": None}
    (home / "setup.pid").write_text("20\n")
    (home / "setup.log").write_text("== 3. the .venv\n")
    age(str(home / "setup.log"), 300)
    setup = box_probe.probe(box["repo"], box["home"], fake_proc(box["tmp"] / "p1", {20: SETUP}))["setup"]
    assert setup["live"] and abs(setup["log"] - (time.time() - 300)) < 5 and setup["exit"] is None
    reused = box_probe.probe(box["repo"], box["home"], fake_proc(box["tmp"] / "p2", {20: ["sleep", "60"]}))
    assert reused["setup"]["live"] is False                     # the pid is another process's now
    (home / "setup.exit").write_text("1\n")
    assert box_probe.probe(box["repo"], box["home"], fake_proc(box["tmp"] / "p3", {}))["setup"]["exit"] == 1


def reading(**changes) -> dict:
    now = 1_800_000_000.0
    return {"box_id": "7", "work": True, "launcher": [10], "launcher_started": now - 7200, "cells": {"a_none_s1": 11},
            "newest": now - 120, "now": now, "stored": {"x": 1}, "key": 12.5,
            "setup": {"live": False, "log": now - 9000, "exit": 0}} | changes


IDLE = {"launcher": [], "cells": {}}


@pytest.mark.parametrize("changes, said", [
    ({}, "progress 120"),                                                         # busy
    ({"launcher": [], "cells": {}}, "ended launcher-ended"),                      # runs ended, the launcher with them
    ({"launcher": []}, "progress 120"),                                           # a cell outlives its launcher
    ({"cells": {}, "newest": 1_800_000_000.0 - 3 * 3600}, "progress 7200"),       # waits, nothing written for long
    ({"cells": {}, "newest": None}, "progress 7200"),                             # nothing written since its start
    ({"launcher": [], "cells": {}, "key": 0.0}, "blocked the-box-key-has-no-limit-left"),
    ({"box_id": "8 x;rm"}, "unreachable wrong-box 8xrm"),
    ({"box_id": None}, "unreachable wrong-box None"),
    ({"work": False, "launcher": [], "cells": {}}, "ended no-clone-on-the-box"),
    ({"key": "err"}, "progress 120"),
    ({**IDLE, "work": False, "setup": {"live": True, "log": 1_800_000_000.0 - 300, "exit": None}}, "progress 300"),
    ({**IDLE, "setup": {"live": True, "log": 1_800_000_000.0 - 300, "exit": None}}, "progress 300"),   # cloned
    ({**IDLE, "setup": {"live": True, "log": None, "exit": None}}, "progress 0"),
    ({**IDLE, "setup": {"live": False, "log": 1_800_000_000.0 - 300, "exit": 1}}, "ended setup-failed"),
    ({**IDLE, "setup": {"live": False, "log": None, "exit": None}}, "ended launcher-ended"),
    ({"setup": {"live": False, "log": None, "exit": 1}}, "progress 120"),        # a launcher runs all the same
    ({**IDLE, "setup": {"live": True, "log": None, "exit": None}, "key": 0.0}, "blocked the-box-key-has-no-limit-left"),
])
def test_the_verdict_of_each_state(changes, said):
    assert box_probe.verdict(reading(**changes), "7") == said


def test_watch_reads_all_runs_ended_only_when_nothing_lives():
    assert box_probe.status(reading(launcher=[], cells={}))[-1] == "all runs ended: capture this box and destroy it"
    during_setup = box_probe.status(reading(**IDLE, setup={"live": True, "log": None, "exit": None}))
    assert during_setup[-1] == "   setup running" and not any("all runs ended" in line for line in during_setup)
    assert not any("all runs ended" in line for line in box_probe.status(reading(launcher=[])))
    assert box_probe.status(reading())[0].startswith("launcher alive (pid 10); 1 cells running; last write 2 min ago")


STAND_IN_SSH = """#!/bin/sh
# the probe on the fake box: standard input is box_probe.py, the last argument the command
exec env PROBE_REPO="$FAKE_REPO" PROBE_HOME="$FAKE_HOME" PROBE_PROC="$FAKE_PROC" "$PYTHON" -c '
import json, os
import scripts.box.box_probe as probe
reading = probe.probe(os.environ["PROBE_REPO"], os.environ["PROBE_HOME"], os.environ["PROBE_PROC"], key=True)
print("motd noise")
print(json.dumps(reading))
'
"""


@pytest.fixture
def progress(box, tmp_path):
    """.claude/box_progress.sh with box 7 registered and an ssh that runs the probe on the fake box."""
    root = tmp_path / "repo"
    (root / ".claude").mkdir(parents=True)
    (root / "scripts" / "box").mkdir(parents=True)
    for rel in (".claude/box_progress.sh", "scripts/box/box_probe.py"):
        (root / rel).write_bytes((ROOT / rel).read_bytes())
    (root / "runs").mkdir()
    (root / "runs" / "boxes.json").write_text(json.dumps({"boxes": [{"id": 7, "destroyed": None}]}))
    (root / "logs" / "boxes" / "7").mkdir(parents=True)
    (root / "logs" / "boxes" / "7" / "ssh_config").write_text("Host box\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ssh").write_text(STAND_IN_SSH)
    (bin_dir / "ssh").chmod(0o755)

    def run(processes: dict[int, list[str]], instance: str = "7") -> str:
        proc = fake_proc(box["tmp"] / f"p{len(list(box['tmp'].glob('p*')))}", processes)
        env = os.environ | {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_REPO": box["repo"],
                            "FAKE_HOME": box["home"], "FAKE_PROC": proc, "PYTHON": sys.executable,
                            "PYTHONPATH": str(ROOT)}
        done = subprocess.run(["bash", str(root / ".claude" / "box_progress.sh"), "vast", instance], env=env,
                              capture_output=True, text=True, timeout=60)
        return done.stdout.strip()

    return run


def test_box_progress_says_busy_ended_and_idle_through_the_probe(box, progress):
    assert progress({10: LAUNCHER, 11: CELL}).startswith("progress ")
    assert progress({14: ["sleep", "60"]}) == "ended launcher-ended"
    for path in every_file(box["repo"]):
        age(path, 3 * 3600)
    said = progress({10: LAUNCHER})
    assert said.startswith("progress ") and int(said.split()[1]) >= 3 * 3600 - 60
    assert progress({10: LAUNCHER}, "8") == "unknown"                       # not a box of the registry
    (Path(box["home"]) / "setup.pid").write_text("20\n")
    (Path(box["home"]) / "setup.log").write_text("== 4. box_build\n")
    said = progress({20: SETUP})
    assert said.startswith("progress ") and int(said.split()[1]) < 60       # a long setup after the clone is progress
    (Path(box["home"]) / "setup.exit").write_text("1\n")
    assert progress({}) == "ended setup-failed"
    assert progress({10: LAUNCHER}, "7;x") == "unknown"
