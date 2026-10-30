"""The vast.ai fleet without vast.ai: offer filtering and ranking on offers from a real search, the registry, the create
and destroy paths against a fake API, the destroy gate, the capture's sweep and comparison on two fake trees (also
end to end through a stand-in ssh and rsync), and the hung-run guard."""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts.box import box_capture, box_runner, box_sweep, fleet, fleet_layout
from scripts.box.box_sweep import Sweep

# Offers from the searches of 2026-10-30 (POST /api/v0/bundles/), cut to the fields the filter reads, and one made-up
# offer that fails four rules. Four can hold a box, 58339037 at a higher price cap; the rest fail as the test says.
OFFER_FIELDS = ("id", "machine_id", "dph_total", "cpu_cores", "cpu_cores_effective", "cpu_ram", "disk_space",
                "reliability", "vms_enabled", "is_vm_deverified", "direct_port_count", "cpu_arch", "geolocation")
SAVED_SEARCH = [dict(zip(OFFER_FIELDS, row)) for row in [
    (58352110, 8512, 0.962, 32, 32.0, 257_000, 6552.0, 0.9972, True, False, 256, "amd64", "Japan, JP"),
    (57143771, 141173, 0.366, 64, 32.0, 257_921, 940.0, 0.9933, True, False, 49, "amd64", "Texas, US"),
    (58339037, 51697, 1.522, 128, 64.0, 258_000, 1156.0, 0.9817, True, False, 256, "amd64", "Quebec, CA"),
    (57970273, 153208, 0.322, 44, 44.0, 128_703, 1528.0, 0.9939, True, False, 198, "amd64", "Quebec, CA"),
    (58340174, 29332, 0.522, 88, 44.0, 257_928, 1196.6, 0.9928, True, False, 256, "amd64", "Quebec, CA"),
    (54902714, 55267, 0.759, 256, 32.0, 64_465, 915.3, 0.9977, True, False, 124, "amd64", "Romania, RO"),
    (58272073, 150231, 0.970, 256, 64.0, 128_939, 380.8, 0.9939, True, False, 24, "amd64", "Sichuan, CN"),
    (46831472, 35137, 0.216, 80, 40.0, 128_889, 654.5, 0.9974, False, False, 256, "amd64", "United Kingdom, GB"),
    (52898820, 40176, 0.562, 128, 32.0, 128_903, 571.2, 0.9967, True, True, 249, "amd64", "Denmark, DK"),
    (53783013, 152872, 0.314, 40, 40.0, 128_361, 639.6, 0.9897, False, False, 98, "amd64", "Vietnam, VN"),
    (99999901, 1, 0.400, 64, 32.0, 257_000, 150.0, 0.9500, True, False, 0, "arm64", "Ohio, US"),     # made up
]]


# ---- offers ----

def test_candidates_keep_what_can_hold_a_box_cheapest_first():
    assert [o["id"] for o in fleet.candidates(SAVED_SEARCH)] == [57970273, 57143771, 58340174, 58352110]
    assert [o["id"] for o in fleet.candidates(SAVED_SEARCH, max_dph=2.0)][-1] == 58339037


def test_each_rejected_offer_says_why():
    why = {o["id"]: fleet.rejections(o) for o in SAVED_SEARCH}
    assert why[58339037] == ["dearer than $1.00/h"]
    assert why[54902714] == ["less than 64 GB of RAM in its share"]          # 32 of 256 cores: 8 GB of 64
    assert why[58272073] == ["less than 64 GB of RAM in its share", "a country the model providers do not serve"]
    assert why[46831472] == ["no VM support"]
    assert why[52898820] == ["VM support deverified", "less than 64 GB of RAM in its share"]
    assert why[53783013] == ["no VM support"]
    assert why[99999901] == ["not x86_64", "less than 200 GB of disk", "reliability below 0.98",
                             "no open port for direct ssh"]


def test_equal_prices_rank_the_more_reliable_first():
    twin = SAVED_SEARCH[3] | {"id": 1, "reliability": 0.999}
    assert [o["id"] for o in fleet.candidates([SAVED_SEARCH[3], twin])] == [1, 57970273]


def test_the_search_is_a_read_and_asks_for_what_the_box_needs():
    call = fleet.search_call()
    assert call.reads and call.method == "POST" and call.path == "/api/v0/bundles/"
    assert call.body["vms_enabled"] == {"eq": True} and call.body["allocated_storage"] == 200
    assert call.body["order"] == [["dph_total", "asc"]]


# ---- a fake vast.ai ----

class FakeVast:
    """The API's replies for the calls fleet.py makes; every call is recorded."""

    def __init__(self, offers=SAVED_SEARCH, instances=()):
        self.offers, self.instances, self.calls, self.next_id = list(offers), list(instances), [], 900

    def __call__(self, call: fleet.Call) -> dict:
        self.calls.append(call)
        if call.path == "/api/v0/bundles/":
            return {"offers": self.offers}
        if call.path == "/api/v1/instances/":
            return {"instances": self.instances, "next_token": None, "total_instances": len(self.instances)}
        if call.path == "/api/v0/users/current":
            return {"credit": 50.0, "api_key": "never-printed"}
        if call.method == "PUT" and call.path.startswith("/api/v0/asks/"):
            self.next_id += 1
            self.instances.append({"id": self.next_id, "label": call.body["label"], "start_date": time.time()})
            return {"success": True, "new_contract": self.next_id, "instance_api_key": "never-stored"}
        raise AssertionError(f"unexpected call {call.show()}")


@pytest.fixture
def registry(tmp_path, monkeypatch):
    path = tmp_path / "runs" / "boxes.json"
    monkeypatch.setattr(fleet, "REGISTRY", path)
    return path


def test_a_client_without_writes_refuses_all_but_reads():
    fake = FakeVast()
    vast = fleet.Vast(writes=False, transport=fake)
    for call in (fleet.create_call(1, "spar-screen-x"), fleet.Call("DELETE", "/api/v0/instances/1/")):
        with pytest.raises(PermissionError):
            vast.send(call)
    assert fake.calls == []
    assert vast.send(fleet.search_call())["offers"]


def test_the_dry_run_prints_every_request_and_sends_none(registry, monkeypatch, capsys):
    fake = FakeVast()
    monkeypatch.setattr(fleet, "http", fake)
    code = fleet.main(["create", "--count", "3", "--tasks", "chess", "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    assert out.count("would send: PUT https://console.vast.ai/api/v0/asks/") == 3
    assert '"label": "spar-screen-chess-claude-haiku-5-5"' in out and '"vm": true' in out
    assert all(call.reads for call in fake.calls) and not registry.exists()
    assert "never-printed" not in out


def test_the_dry_run_names_the_boxes_no_offer_is_left_for(registry, monkeypatch, capsys):
    monkeypatch.setattr(fleet, "http", FakeVast())
    assert fleet.main(["create", "--count", "15", "--dry-run"]) == 1
    out = capsys.readouterr().out
    assert out.count("would send: PUT") == 4 and "4 of 15 requests printed" in out


def test_create_registers_each_instance_and_passes_its_audit(registry, monkeypatch, capsys):
    fake = FakeVast()
    monkeypatch.setattr(fleet, "http", fake)
    assert fleet.main(["create", "--count", "2", "--tasks", "chess", "--models", "gpt-6-luna",
                       "claude-haiku-5-5"]) == 0
    boxes = json.loads(registry.read_text())["boxes"]
    assert [(b["id"], b["label"], b["offer"], b["suffix"]) for b in boxes] == [
        (901, "spar-screen-chess-gpt-6-luna", 57970273, "luna"),
        (902, "spar-screen-chess-claude-haiku-5-5", 57143771, "haiku")]
    assert "never-stored" not in registry.read_text() and "never-stored" not in capsys.readouterr().out


def test_create_refuses_a_count_that_is_not_the_boxes_asked_for(registry, monkeypatch):
    fake = FakeVast()
    monkeypatch.setattr(fleet, "http", fake)
    assert fleet.main(["create", "--count", "14"]) == 2
    assert fake.calls == []


def test_create_refuses_labels_the_account_holds(registry, monkeypatch):
    fake = FakeVast(instances=[{"id": 5, "label": "spar-screen-chess-gpt-6-luna"}])
    monkeypatch.setattr(fleet, "http", fake)
    assert fleet.main(["create", "--count", "1", "--tasks", "chess", "--models", "gpt-6-luna"]) == 1
    assert all(call.reads for call in fake.calls)


def test_the_audit_registers_an_instance_the_launch_did_not_hear_back_about(registry, capsys):
    asked = {"spar-screen-chess-gpt-6-luna": ("chess", "gpt-6-luna")}
    fake = FakeVast(instances=[{"id": 7, "label": "spar-screen-chess-gpt-6-luna", "start_date": time.time()}])
    assert fleet.audit(fleet.Vast(writes=False, transport=fake), asked, time.time() - 5) == 0
    assert [b["id"] for b in json.loads(registry.read_text())["boxes"]] == [7]
    stranger = FakeVast(instances=[{"id": 8, "label": "spar-screen-other", "start_date": time.time()}])
    assert fleet.audit(fleet.Vast(writes=False, transport=stranger), asked, time.time()) == 1
    assert "UNREGISTERED 8" in capsys.readouterr().out


def test_the_registry_refuses_an_id_twice(registry):
    entry = fleet.box_entry(3, "spar-screen-chess-gpt-6-luna", "chess", "gpt-6-luna", SAVED_SEARCH[3])
    fleet.register(entry)
    with pytest.raises(ValueError):
        fleet.register(entry)
    assert fleet.registered(fleet.load_registry(), 3)["ram_gb"] == 128.7


def test_every_page_of_the_instances_is_read():
    pages = iter([{"instances": [{"id": 1}], "next_token": "t", "total_instances": 2},
                  {"instances": [{"id": 2}], "next_token": None, "total_instances": 2}])
    rows = fleet.list_instances(fleet.Vast(writes=False, transport=lambda call: next(pages)))
    assert [r["id"] for r in rows] == [1, 2]
    short = iter([{"instances": [{"id": 1}], "next_token": None, "total_instances": 2}])
    with pytest.raises(RuntimeError):
        fleet.list_instances(fleet.Vast(writes=False, transport=lambda call: next(short)))


def test_ssh_goes_direct_when_the_box_maps_its_port(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet, "BOXES", tmp_path)
    record = {"public_ipaddr": "213.97.25.93", "ports": {"22/tcp": [{"HostPort": "22444"}]},
              "ssh_host": "ssh1.vast.ai", "ssh_port": 35410}
    ssh = fleet.ssh_endpoint(record)
    assert ssh == {"direct": {"host": "213.97.25.93", "port": 22444}, "proxy": {"host": "ssh1.vast.ai", "port": 35410}}
    config = fleet.write_ssh_config(42, ssh).read_text()
    assert "HostName 213.97.25.93\n  Port 22444\n" in config and "BatchMode yes" in config
    assert fleet.ssh_endpoint({"ssh_host": "ssh1.vast.ai", "ssh_port": 35410})["direct"] is None


# ---- the destroy gate ----

def gate(marker, live_label="spar-screen-chess-gpt-6-luna", entry_label="spar-screen-chess-gpt-6-luna", now=1000.0):
    entry = {"id": 7, "label": entry_label, "destroyed": None}
    return fleet.destroy_refusals(7, entry, {"label": live_label}, marker, now)


def fresh_marker(**changes):
    return {"id": 7, "epoch": 900.0, "swept": 12, "lost": 0} | changes


def test_the_destroy_gate_holds_with_a_fresh_marker_for_a_fleet_box():
    assert gate(fresh_marker()) == []


@pytest.mark.parametrize("marker, words", [
    (None, "no capture marker"),
    (fresh_marker(epoch=0.0), "minutes ago"),
    (fresh_marker(id=8), "marker is for 8"),
    (fresh_marker(lost=2), "2 files lost"),
    (fresh_marker(swept=0), "lost of 0"),
])
def test_the_destroy_gate_refuses_without_a_fresh_clean_marker(marker, words):
    refusals = gate(marker)
    assert len(refusals) == 1 and words in refusals[0]


def test_the_destroy_gate_refuses_another_workstreams_box():
    assert "not a box of this fleet" in gate(fresh_marker(), live_label="lucy-gpu-box")[0]
    assert "not a box of this fleet" in gate(fresh_marker(), live_label="spar-screen-chess-claude-haiku-5-5")[0]
    assert "not in the registry" in fleet.destroy_refusals(7, None, {"label": "x"}, fresh_marker(), 1000.0)[0]


def test_destroy_without_a_marker_sends_no_delete(registry, tmp_path, monkeypatch, capsys):
    fake = FakeVast(instances=[{"id": 7, "label": "spar-screen-chess-gpt-6-luna"}])
    fake_instance = {"instances": {"id": 7, "label": "spar-screen-chess-gpt-6-luna"}}
    monkeypatch.setattr(fleet, "http",
                        lambda call: fake_instance if call.path == "/api/v0/instances/7/" else fake(call))
    monkeypatch.setattr(box_capture, "BOXES", tmp_path / "boxes")
    fleet.register(fleet.box_entry(7, "spar-screen-chess-gpt-6-luna", "chess", "gpt-6-luna", None))
    assert fleet.main(["destroy", "7"]) == 1
    assert "no capture marker" in capsys.readouterr().out
    assert not any(call.method == "DELETE" for call in fake.calls)


# ---- the layout ----

def test_a_task_has_one_cell_per_kind_or_none(tmp_path):
    package = tmp_path / "chess"
    package.mkdir()
    for kind in fleet_layout.KINDS:
        (package / f"game_{kind}.py").write_text("")
    assert fleet_layout.cells("chess", examples=tmp_path) == [f"chess.game_{k}" for k in fleet_layout.KINDS]
    (package / "game_minimal_regulated.py").unlink()
    with pytest.raises(ValueError, match="minimal_regulated: 0 scripts"):
        fleet_layout.cells("chess", examples=tmp_path)
    (package / "game_minimal_regulated.py").write_text("")
    (package / "other_solo.py").write_text("")
    with pytest.raises(ValueError, match="solo: 2 scripts"):
        fleet_layout.cells("chess", examples=tmp_path)


def test_boxes_labels_and_the_fleet_prefix():
    assert len(fleet_layout.boxes(list(fleet_layout.TASKS), list(fleet_layout.MODELS))) == 15
    with pytest.raises(ValueError):
        fleet_layout.boxes(["checkers"], ["gpt-6-luna"])
    assert fleet_layout.box_label("spar-screen", "chess", "gpt-6-luna") == "spar-screen-chess-gpt-6-luna"
    assert fleet_layout.is_fleet_label("spar-screen-pilot-chess-gpt-6-luna")
    assert not fleet_layout.is_fleet_label("spar-screens") and not fleet_layout.is_fleet_label(None)


# ---- the capture ----

def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A fake box (home/work is the clone, a git repository; home; tmp) and a fake local repository."""
    home, tmp, local = tmp_path / "box" / "root", tmp_path / "box" / "tmp", tmp_path / "local"
    work = home / "work"
    files = {
        work / "src" / "tracked.py": "print(1)\n", work / "src" / "edited.py": "print(2)\n",
        work / "runs" / "r1" / "run.json": '{"a": 1}\n', work / "logs" / "r1" / "x.eval": "eval bytes",
        work / "out" / "r1" / "fig.png": "png", work / "src" / "__pycache__" / "x.pyc": "pyc",
        work / ".venv" / "lib" / "x.py": "venv", work / "build" / "rows.jsonl": "{}\n",
        home / "screen.log": "== screen\n", home / ".box_env": "export KEY=secret\n",
        home / ".cache" / "uv" / "wheel": "w", home / ".bashrc": "# image\n",
        tmp / "inspect.tmp": "t", tmp / "systemd-private-abc" / "x": "s",
    }
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (work / ".gitignore").write_text("runs/\nlogs/\nout/\n.venv/\nbuild/\n__pycache__/\n")
    git(work, "init", "-q")
    git(work, "add", ".gitignore", "src/tracked.py", "src/edited.py")
    git(work, "commit", "-q", "-m", "c")
    (work / "src" / "edited.py").write_text("print(3)\n")
    (work / "src" / "new.py").write_text("new\n")
    for rel in ("runs/r1/run.json", "logs/r1/x.eval", "out/r1/fig.png"):
        (local / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(work / rel, local / rel)
    monkeypatch.setattr(box_capture, "REMOTE_REPO", str(work))
    monkeypatch.setattr(box_capture, "HOME", str(home))
    monkeypatch.setattr(box_capture, "TMP", str(tmp))
    monkeypatch.setattr(box_capture, "ROOT", local)
    monkeypatch.setattr(box_capture, "BOXES", local / "logs" / "boxes")
    return {"work": work, "home": home, "tmp": tmp, "local": local, "folder": local / "logs" / "boxes" / "7"}


def sweep(box) -> list[dict]:
    lines = list(Sweep([str(box["work"]), str(box["home"]), str(box["tmp"])], str(box["work"]), str(box["home"]),
                       str(box["tmp"]), proc=str(box["tmp"] / "no-proc")).lines())
    return [line for line in lines if "containers" not in line]


def compare(box, lines, baseline=frozenset(), on_origin=lambda sha: True):
    return box_capture.compare(lines, lambda path: box_capture.local_path(path, box["folder"]), baseline, on_origin)


def bashrc_baseline(box) -> frozenset:
    path = str(box["home"] / ".bashrc")
    return frozenset({(path, 8, box_capture.sha256(Path(path)))})


def test_the_sweep_lists_what_is_not_reproducible_and_counts_the_rest(box):
    lines = sweep(box)
    listed = sorted(os.path.relpath(line["file"], box["home"].parent) for line in lines if "file" in line)
    assert listed == ["root/.bashrc", "root/screen.log", "root/work/logs/r1/x.eval", "root/work/out/r1/fig.png",
                      "root/work/runs/r1/run.json", "root/work/src/edited.py", "root/work/src/new.py",
                      "tmp/inspect.tmp"]
    work, home = str(box["work"]), str(box["home"])
    trees, files = box_sweep.excluded_trees(work, home, str(box["tmp"])), box_sweep.excluded_files(home)
    excluded = {line["excluded"]: line["files"] for line in lines if "excluded" in line}
    assert excluded.pop(box_sweep.GIT_STORES) > 0
    assert excluded == {"tracked files unchanged from their work tree's HEAD": 2,      # .gitignore, src/tracked.py
                        box_sweep.CACHE_NAMES["__pycache__"]: 1, trees[f"{work}/.venv"]: 1, trees[f"{work}/build"]: 1,
                        trees[f"{home}/.cache/uv"]: 1, files[f"{home}/.box_env"]: 1,
                        "systemd's private /tmp folders of host services": 1}
    assert not any("secret" in json.dumps(line) for line in lines)
    assert lines[-1]["done"] is True


def test_the_check_requires_every_other_file_under_the_box_folder(box):
    report = compare(box, sweep(box), bashrc_baseline(box))
    assert sorted(os.path.basename(path) for path, _ in report.missing) == [
        "edited.py", "inspect.tmp", "new.py", "screen.log"]
    assert report.captured == 3 and report.image == 1 and not report.ok
    for path, _ in report.missing:
        target = box_capture.local_path(path, box["folder"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    report = compare(box, sweep(box), bashrc_baseline(box))
    assert report.ok and report.captured == 7 and report.lost == 0
    assert report.swept == report.captured + report.image + sum(n["files"] for n in report.excluded.values())


def test_the_check_catches_a_copy_cut_short_or_changed_in_place(box):
    for path in [p for p, _ in compare(box, sweep(box)).missing]:
        target = box_capture.local_path(path, box["folder"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    assert compare(box, sweep(box)).ok
    (box["local"] / "logs" / "r1" / "x.eval").write_text("eval")                 # cut short
    (box["local"] / "runs" / "r1" / "run.json").write_text('{"a": 2}\n')         # same size, other bytes
    report = compare(box, sweep(box))
    assert sorted(os.path.basename(path) for path, _ in report.differ) == ["run.json", "x.eval"] and not report.ok


@pytest.mark.parametrize("change, words", [
    (lambda lines: [line for line in lines if "done" not in line], "cut short"),
    (lambda lines: [{"done": True, "roots": []}], "found no files"),
    (lambda lines: [{"producing": 12, "command": "python -m examples.chess.game_solo"}, *lines], "still producing"),
    (lambda lines: [{"unreadable": "/root/x", "error": "denied"}, *lines], "could not read"),
    (lambda lines: [{"tree": "/root/work/submodules/orbit", "head": "a", "recorded": "b"}, *lines], "records b"),
])
def test_the_check_fails_on_anything_it_cannot_vouch_for(box, change, words):
    lines = sweep(box)
    for path in [p for p, _ in compare(box, lines).missing]:
        target = box_capture.local_path(path, box["folder"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    report = compare(box, change(lines))
    assert not report.ok and any(words in problem for problem in report.problems)


def test_the_clone_must_be_at_a_commit_origin_holds(box):
    lines = sweep(box)
    assert lines[0]["tree"] == str(box["work"]) and lines[0]["recorded"] is None
    report = compare(box, lines, on_origin=lambda sha: False)
    assert any("origin/main lacks" in problem for problem in report.problems)


def test_the_check_end_to_end_through_a_stand_in_ssh(box, tmp_path, monkeypatch):
    """check --pull with ssh replaced by a script that runs the command here: the sweep goes over standard input,
    rsync fetches what is missing, the second sweep passes and the marker satisfies the destroy gate."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ssh").write_text('#!/bin/sh\n# drop "-F <config> box", run the rest here\nshift 3\nexec sh -c "$*"\n')
    (bin_dir / "ssh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(box_capture, "on_origin", lambda sha: True)
    box["folder"].mkdir(parents=True)
    (box["folder"] / "ssh_config").write_text("Host box\n")
    assert box_capture.check(7, pull=False) is None and not box_capture.marker_path(7).exists()
    marker = box_capture.check(7, pull=True)
    assert marker is not None and marker["lost"] == 0 and marker["captured"] == 8
    assert (box["folder"] / "files" / str(box["home"] / "screen.log").lstrip("/")).read_text() == "== screen\n"
    assert box_capture.read_marker(7) == marker
    entry = {"id": 7, "label": "spar-screen-chess-gpt-6-luna", "destroyed": None}
    assert fleet.destroy_refusals(7, entry, {"label": entry["label"]}, marker, time.time()) == []


def test_the_sweep_runs_from_standard_input_with_the_standard_library_alone(box):
    with open(Path(box_capture.SWEEP), "rb") as script:
        result = subprocess.run([sys.executable, "-I", "-", "sweep", str(box["work"]), str(box["home"]),
                                 str(box["tmp"])], stdin=script, capture_output=True, check=True)
    lines = [json.loads(text) for text in result.stdout.decode().splitlines()]
    assert lines[-1]["done"] is True and sum("file" in line for line in lines) == 8


# ---- the hung-run guard ----

LIMIT = 5400


@pytest.mark.parametrize("alive_min, quiet_min, hung", [
    (60, 60, False),                   # quiet, but inside Inspect's limit
    (110, 25, True),                   # 20 min past the limit, nothing written for 20 min
    (110, 5, False),                   # past the limit, still writing (the export)
    (219, 1, False),
    (220, 1, True),                    # twice the limit and the grace, whatever it writes
])
def test_a_run_is_hung_past_its_limit_when_it_writes_nothing(alive_min, quiet_min, hung):
    now = 100_000.0
    started, last = now - alive_min * 60, now - quiet_min * 60
    assert (box_runner.hung(now, started, last, LIMIT) is not None) == hung


def test_a_run_that_never_wrote_is_quiet_since_its_start():
    now = 100_000.0
    assert box_runner.hung(now, now - 111 * 60, 0.0, LIMIT) is not None
    assert box_runner.hung(now, now - 89 * 60, 0.0, LIMIT) is None


def test_stop_ends_the_whole_process_group_even_when_it_ignores_sigterm(monkeypatch):
    monkeypatch.setattr(box_runner, "STOP_WAIT_S", 1)
    process = subprocess.Popen(["sh", "-c", 'trap "" TERM; sleep 600 & wait'], start_new_session=True)
    time.sleep(0.3)
    box_runner.stop(process)
    assert process.poll() == -signal.SIGKILL
    deadline = time.time() + 5
    while time.time() < deadline:                      # the orphaned sleep is reaped by the system
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    with pytest.raises(ProcessLookupError):
        os.killpg(process.pid, 0)


@pytest.fixture
def no_launch(monkeypatch, tmp_path):
    """The runner's refusals, with launching made impossible and the pid file in tmp_path."""
    def refuse(*args):
        raise AssertionError("a run was launched")
    monkeypatch.setattr(box_runner, "launch", refuse)
    monkeypatch.setattr(box_runner, "PID_FILE", tmp_path / "screen.pid")
    return tmp_path / "screen.pid"


def test_the_runner_refuses_without_the_models_key(no_launch, monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert box_runner.main(["run", "--task", "chess", "--model", "deepseek-flash-openrouter", "--suffix", "t"]) == 1
    assert "OPENROUTER_API_KEY" in capsys.readouterr().out and not no_launch.exists()


def test_the_runner_refuses_a_task_without_every_cell(no_launch, monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "x" * 30)
    monkeypatch.setattr(box_runner, "cells", lambda task, kinds: fleet_layout.cells(task, kinds, Path("/nowhere")))
    assert box_runner.main(["run", "--task", "chess", "--model", "deepseek-flash-openrouter", "--suffix", "t"]) == 1
    assert "refused: examples/chess needs one script per kind" in capsys.readouterr().out and not no_launch.exists()


def test_build_refuses_a_task_without_build_steps(capsys):
    assert box_runner.main(["build", "--task", "chess"]) == 1
    assert "no build steps for chess" in capsys.readouterr().out


def test_status_names_each_runs_state(monkeypatch):
    monkeypatch.setattr(box_runner, "done", lambda name: name == "a")

    def record(**fields):
        return {"pid": None, "exit": None, "hung": None} | fields

    assert box_runner.state("a", record(exit=0)) == "finished"
    assert box_runner.state("b", record(exit=0)) == "failed"            # ended well without its signals
    assert box_runner.state("b", record(exit=1)) == "failed"
    assert box_runner.state("b", record(exit=-9, hung="nothing written")) == "hung"
    assert box_runner.state("b", record()) == "waiting"
    assert box_runner.state("b", record(pid=os.getpid())) == "lost"     # a live pid that is not this run
