"""The replay plants a seed pack's files where the run planted them (replay_run.plant_seed). No Docker: a fake box
records each exec."""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from src.replay.recorded_calls import ReplayRefused
from src.replay.replay_run import SEED_PLANT, plant_seed
from src.sandbox import staging


class Box:
    def __init__(self) -> None:
        self.runs = []

    def must_run(self, service, argv, what, *, stdin=None, user=None):
        self.runs.append({"service": service, "argv": argv, "stdin": stdin, "user": user})
        return b""


def run_of(seed: dict | None):
    return SimpleNamespace(run_json={"seed": seed} if seed is not None else {})


PLANTED = {"path": "/tmp/notes.md", "owner": "root", "name": "notes.md"}
SEED = {"files": [{"place": "tmp", "name": "notes.md", "text": "# notes\n"}],
        "planted": [{"agent": "k7q2m", "service": "k7q2m", **PLANTED},
                    {"agent": "m3p4x", "service": "m3p4x", **PLANTED}]}


def test_each_planted_file_goes_to_its_machine_as_root():
    box = Box()
    got = plant_seed(box, run_of(SEED), {"k7q2m", "m3p4x"})
    assert [r["service"] for r in box.runs] == ["k7q2m", "m3p4x"]
    assert all(r["user"] == "root" and r["stdin"] == b"# notes\n" and r["argv"][-2:] == ["/tmp/notes.md", "root"]
               for r in box.runs)
    assert got == [{"service": "k7q2m", "path": "/tmp/notes.md", "owner": "root"},
                   {"service": "m3p4x", "path": "/tmp/notes.md", "owner": "root"}]


def test_a_shared_machine_is_the_default_and_no_seed_plants_nothing():
    box = Box()
    shared = {**SEED, "planted": [{**SEED["planted"][0], "service": None}]}
    plant_seed(box, run_of(shared), {"default"})
    assert box.runs[0]["service"] == "default"
    assert plant_seed(Box(), run_of(None), {"default"}) == []


def test_a_file_without_text_or_machine_refuses_the_replay():
    with pytest.raises(ReplayRefused, match="no text"):
        plant_seed(Box(), run_of({**SEED, "files": []}), {"k7q2m", "m3p4x"})
    with pytest.raises(ReplayRefused, match="machine the replay has not"):
        plant_seed(Box(), run_of(SEED), {"k7q2m"})


def test_the_plant_command_writes_owns_and_sets_the_mode(tmp_path):
    path = tmp_path / "notes.md"
    owner = subprocess.run(["id", "-un"], capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["sh", "-c", SEED_PLANT, "sh", str(path), owner], input=b"# notes\n", check=True)
    assert path.read_bytes() == b"# notes\n" and oct(path.stat().st_mode & 0o777) == "0o644"
    assert json.dumps(SEED)          # the record round-trips as run.json keeps it


def test_a_file_in_the_clones_root_is_kept_out_of_git_again():
    """A seed file the run planted in the root of an agent's clone and kept out of git (git_excluded) is planted, then
    kept out of git on the same machine as root, as src/swarm/seed_setup.py did; a file without the mark is not."""
    clone = {"path": "/app/NOTES.md", "owner": "root", "name": "NOTES.md", "git_excluded": True}
    seed = {"files": [{"place": "repo", "name": "NOTES.md", "text": "# Notes\n"}, *SEED["files"]],
            "planted": [{"agent": "k7q2m", "service": "k7q2m", **clone}, SEED["planted"][1]]}
    box = Box()
    got = plant_seed(box, run_of(seed), {"k7q2m", "m3p4x"})
    assert [(r["service"], r["argv"][2], r["user"]) for r in box.runs] == [
        ("k7q2m", SEED_PLANT, "root"), ("k7q2m", staging.kept_out_of_git("/app/NOTES.md"), "root"),
        ("m3p4x", SEED_PLANT, "root")]
    assert box.runs[0]["stdin"] == b"# Notes\n" and box.runs[1]["stdin"] is None
    assert got == [{"service": "k7q2m", "path": "/app/NOTES.md", "owner": "root"},
                   {"service": "m3p4x", "path": "/tmp/notes.md", "owner": "root"}]
