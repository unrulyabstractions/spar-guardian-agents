""".claude/box_teardown.sh with stand-ins for the capture, ssh, uv, docker and systemctl: a Docker that cannot answer is
restarted once and then saved, a Docker still dead after its restart gets the box stopped, a save that saves nothing
fails, and Docker is never touched while a process still produces. No box, no network."""
from __future__ import annotations

import os
import subprocess

import pytest

from scripts.box.fleet_layout import ROOT

UNKNOWN = "!! cannot tell what Docker holds (CalledProcessError: docker ps -q): a container or volume may hold the only"
HOLDS = ("!! Docker holds 0 running and 1 containers in all, 1 volumes (c1; volume v1): a hung or stopped run's "
         "containers and volumes hold its workspace")
PASSES = "== the capture check passes"
PRODUCING = "!! still producing: pid 42 .venv/bin/python -m scripts.study.run_study plan.yaml"

CAPTURE = """#!/usr/bin/env bash
# the capture: call n prints $CAPTURES/<n> after its first line, which is the exit code
n=$(( $(cat "$CAPTURES/n" 2> /dev/null || echo 0) + 1 ))
echo "$n" > "$CAPTURES/n"
echo "capture $*" >> "$CALLS"
tail -n +2 "$CAPTURES/$n"
exit "$(head -n 1 "$CAPTURES/$n")"
"""
SSH = """#!/usr/bin/env bash
# the box: the script on standard input runs here, its /root/ the fake box's home
shift
sed "s#/root/#$FAKE_ROOT/#g" | "$@"
"""
UV = """#!/usr/bin/env bash
echo "uv $*" >> "$CALLS"
"""
DOCKER = """#!/usr/bin/env bash
# a Docker that answers only while $DOCKER_STATE/alive exists
[ -f "$DOCKER_STATE/alive" ] || { echo "Cannot connect to the Docker daemon" >&2; exit 1; }
case "$1 ${2:-}" in
  "ps -aq") cat "$DOCKER_STATE/containers" ;;
  "volume ls") cat "$DOCKER_STATE/volumes" ;;
  "volume inspect") echo "$DOCKER_STATE/volume/${@: -1}" ;;
  "inspect -f")
    case $3 in
      *Name*) echo "/${@: -1}" ;;
      *) echo "$DOCKER_STATE/upper/${@: -1}" ;;
    esac ;;
  "stop -t") echo "docker stop ${@: -1}" >> "$CALLS" ;;
  "info "*|"info") ;;
esac
"""
SYSTEMCTL = """#!/usr/bin/env bash
echo "systemctl $*" >> "$CALLS"
[ ! -f "$DOCKER_STATE/revives" ] || touch "$DOCKER_STATE/alive"
"""


@pytest.fixture
def teardown(tmp_path):
    """The teardown in a stand-in repository; run(captures, docker alive, revives) gives (exit code, output, calls)."""
    repo, bin_dir, state, home = tmp_path / "repo", tmp_path / "bin", tmp_path / "docker", tmp_path / "box_root"
    for folder in (repo / ".claude", repo / "scripts" / "box", bin_dir, state / "upper" / "c1" / "root",
                   state / "volume" / "v1", home, tmp_path / "captures"):
        folder.mkdir(parents=True)
    (repo / ".claude" / "box_teardown.sh").write_bytes((ROOT / ".claude" / "box_teardown.sh").read_bytes())
    for path, text in ((repo / "scripts/box/capture_check.sh", CAPTURE), (repo / "scripts/box/box_ssh.sh", SSH),
                       (bin_dir / "uv", UV), (bin_dir / "docker", DOCKER), (bin_dir / "systemctl", SYSTEMCTL),
                       (bin_dir / "sleep", "#!/bin/sh\n")):
        path.write_text(text)
        path.chmod(0o755)
    (home / "box_id").write_text("7\n")
    (state / "containers").write_text("c1\n")
    (state / "volumes").write_text("v1\n")
    (state / "upper" / "c1" / "root" / "answer.go").write_text("package main\n")
    (state / "volume" / "v1" / "data.txt").write_text("workspace\n")
    calls = tmp_path / "calls"

    def run(captures: list[tuple[int, str]], alive: bool, revives: bool = False) -> tuple[int, str, str]:
        for n, (code, text) in enumerate(captures, 1):
            (tmp_path / "captures" / str(n)).write_text(f"{code}\n{text}\n")
        for flag, on in (("alive", alive), ("revives", revives)):
            (state / flag).touch() if on else (state / flag).unlink(missing_ok=True)
        env = os.environ | {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "CALLS": str(calls),
                            "CAPTURES": str(tmp_path / "captures"), "FAKE_ROOT": str(home), "DOCKER_STATE": str(state)}
        done = subprocess.run(["bash", str(repo / ".claude" / "box_teardown.sh"), "vast", "7"], env=env,
                              capture_output=True, text=True, timeout=60)
        return done.returncode, done.stdout + done.stderr, calls.read_text() if calls.exists() else ""

    run.home = home
    return run


def test_a_docker_that_cannot_answer_is_restarted_once_then_saved_and_the_box_destroyed(teardown):
    code, out, calls = teardown([(1, UNKNOWN), (1, HOLDS), (0, PASSES)], alive=False, revives=True)
    assert code == 0, out
    assert calls.count("systemctl restart docker") == 1 and "Docker answers after its restart" in out
    assert "saved: 1 containers, 1 volumes, 3 files" in out, out
    assert (teardown.home / "docker_saved/containers/c1/root/answer.go").read_text() == "package main\n"
    assert (teardown.home / "docker_saved/volumes/v1/data.txt").read_text() == "workspace\n"
    captures = [line for line in calls.splitlines() if line.startswith("capture ")]
    assert len(captures) == 3 and "--accept-docker" in captures[2] and "--accept-docker" not in captures[1]
    assert calls.splitlines()[-1] == "uv run python -m scripts.box.fleet destroy 7"


def test_a_docker_still_dead_after_its_restart_gets_the_box_stopped_not_destroyed(teardown):
    code, out, calls = teardown([(1, UNKNOWN), (1, UNKNOWN)], alive=False)
    assert code == 1
    assert "the box is stopped, not destroyed" in out and "saved:" not in out
    assert "uv run python -m scripts.box.fleet stop 7" in calls and "destroy" not in calls
    assert calls.count("systemctl restart docker") == 1 and not (teardown.home / "docker_saved").exists()


def test_a_save_that_cannot_list_docker_saves_nothing_and_says_so(teardown):
    """The capture saw Docker's containers, then Docker died: the save fails, and nothing claims a save."""
    code, out, calls = teardown([(1, HOLDS)], alive=False)
    assert code == 1
    assert "Docker cannot list its containers: nothing saved" in out and "saved:" not in out
    assert "saving the containers failed: the box stays" in out
    assert "--accept-docker" not in calls and "fleet" not in calls


def test_a_save_of_no_container_and_no_volume_fails(teardown):
    (teardown.home.parent / "docker" / "containers").write_text("")
    (teardown.home.parent / "docker" / "volumes").write_text("")
    code, out, calls = teardown([(1, HOLDS)], alive=True)
    assert code == 1 and "Docker lists no container and no volume: nothing saved" in out
    assert "--accept-docker" not in calls


@pytest.mark.parametrize("problem", [PRODUCING, "!! the box that answered names itself '8' in /root/box_id, not 7"])
def test_docker_is_never_touched_while_a_process_produces_or_on_another_box(teardown, problem):
    code, out, calls = teardown([(1, f"{UNKNOWN}\n{problem}")], alive=False, revives=True)
    assert code == 1 and "Docker is left as it is" in out
    assert "systemctl" not in calls and "fleet" not in calls


def test_another_box_behind_the_endpoint_is_neither_restarted_nor_stopped(teardown):
    (teardown.home / "box_id").write_text("8\n")
    code, out, calls = teardown([(1, UNKNOWN)], alive=False, revives=True)
    assert code == 1 and "the endpoint reaches another box" in out
    assert "systemctl" not in calls and "fleet" not in calls


def test_only_the_exact_docker_problem_leads_to_a_save(teardown):
    """A failed capture whose problems only mention Docker in passing leaves the box alone."""
    code, out, calls = teardown([(1, "!! MISSING logs/x/Docker holds.txt")], alive=True)
    assert code == 1 and "the capture check failed: the box stays" in out
    assert "docker stop" not in calls and "fleet" not in calls
