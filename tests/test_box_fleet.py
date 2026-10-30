"""The vast.ai fleet without vast.ai: offer filtering and ranking on offers from a real search, the registry and the
launch pins, the create, status and destroy paths against a fake API (a launch cut short, a destroy vast.ai does not
carry out, the fleet's $/h cap), the key on a redirect, the destroy gate, run-args, the capture's sweep and comparison
on two fake trees (also end to end through a stand-in ssh and rsync, with the manifest, the snapshot and a conflict),
and the runner's hung-run guard, resume and status."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from scripts import screen
from scripts.box import box_capture, box_runner, box_sweep, fleet, fleet_layout
from scripts.box.box_sweep import Sweep
from src.configuration import settings

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
REQUIREMENTS = b"inspect-ai==0.3.263\n"
PIN = {"commit": "c0ffee" * 6 + "c0ff", "requirements": REQUIREMENTS,
       "requirements_sha256": hashlib.sha256(REQUIREMENTS).hexdigest(), "python_version": "3.11.13",
       "uv_version": "0.8.15"}
LAUNCH = "l0a1b2c"
LABEL = f"spar-screen-chess-gpt-6-luna-{LAUNCH}"


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
    """The API's replies for the calls fleet.py makes; every call is recorded. deletes=False plays a destroy that
    vast.ai accepts and does not carry out."""

    def __init__(self, offers=SAVED_SEARCH, instances=(), deletes=True):
        self.offers, self.instances, self.calls, self.next_id = list(offers), list(instances), [], 900
        self.deletes = deletes

    def __call__(self, call: fleet.Call) -> dict:
        self.calls.append(call)
        if call.path == "/api/v0/bundles/":
            return {"offers": self.offers}
        if call.path == "/api/v1/instances/":
            return {"instances": self.instances, "next_token": None, "total_instances": len(self.instances)}
        if call.path == "/api/v0/users/current/":
            return {"credit": 50.0, "api_key": "never-printed"}
        if call.method == "PUT" and call.path.startswith("/api/v0/asks/"):
            self.next_id += 1
            self.instances.append({"id": self.next_id, "label": call.body["label"], "start_date": time.time(),
                                   "dph_total": 0.5})
            return {"success": True, "new_contract": self.next_id, "instance_api_key": "never-stored"}
        if call.path.startswith("/api/v0/instances/"):
            instance_id = int(call.path.split("/")[-2])
            row = next((row for row in self.instances if row["id"] == instance_id), None)
            if call.method == "DELETE":
                if self.deletes and row:
                    self.instances.remove(row)
                return {"success": True}
            if row is None:
                raise urllib.error.HTTPError(call.url, 404, "no such instance", {}, None)
            return {"instances": row}
        raise AssertionError(f"unexpected call {call.show()}")


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """The registry, the box folders and the launch pins in tmp_path; a launch pins PIN and every task can run."""
    path = tmp_path / "runs" / "boxes.json"
    monkeypatch.setattr(fleet, "REGISTRY", path)
    monkeypatch.setattr(fleet, "BOXES", tmp_path / "boxes")
    monkeypatch.setattr(box_capture, "BOXES", tmp_path / "boxes")
    monkeypatch.setattr(fleet, "fleet_pin", lambda commit="HEAD": dict(PIN))
    monkeypatch.setattr(fleet, "layout_problems", lambda tasks: [])
    return path


def add_box(instance_id=7, task="chess", model="gpt-6-luna", label=LABEL, launch=LAUNCH, **fields) -> dict:
    """A registered box of a launch whose pins are written."""
    if not fleet.launch_dir(launch).exists():
        fleet.write_launch(launch, dict(PIN))
    entry = fleet.box_entry(instance_id, label, task, model, SAVED_SEARCH[3], launch) | fields
    fleet.register(entry)
    return entry


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
    assert '"label": "spar-screen-chess-claude-haiku-5-5-l' in out and '"vm": true' in out
    assert f"commit {PIN['commit']}" in out
    assert all(call.reads for call in fake.calls) and not registry.exists()
    assert not fleet.BOXES.exists()                                           # no launch pinned on a dry run
    assert "never-printed" not in out


def test_the_dry_run_names_the_boxes_no_offer_is_left_for(registry, monkeypatch, capsys):
    monkeypatch.setattr(fleet, "http", FakeVast())
    assert fleet.main(["create", "--count", "15", "--dry-run"]) == 1
    out = capsys.readouterr().out
    assert out.count("would send: PUT") == 4 and "4 of 15 requests printed" in out


def test_the_dry_run_stops_at_the_fleets_cap(registry, monkeypatch, capsys):
    monkeypatch.setattr(fleet, "http", FakeVast())
    assert fleet.main(["create", "--count", "3", "--tasks", "chess", "--max-fleet-dph", "1.0", "--dry-run"]) == 1
    out = capsys.readouterr().out
    assert "!!: the fleet would bill $1.210/h, past its cap $1.00/h" in out
    assert out.count("would send: PUT") == 2 and "takes the fleet to $1.210/h" in out


def test_create_refuses_past_the_fleets_cap_counting_the_boxes_it_holds(registry, monkeypatch, capsys):
    fake = FakeVast()
    monkeypatch.setattr(fleet, "http", fake)
    add_box(5, label=f"spar-screen-protein_design-gpt-6-luna-{LAUNCH}", task="protein_design", dph=14.5)
    assert fleet.main(["create", "--count", "3", "--tasks", "chess"]) == 1
    assert "refused: the fleet would bill $15.710/h" in capsys.readouterr().out
    assert all(call.reads for call in fake.calls)


def test_create_refuses_tasks_that_cannot_run(registry, monkeypatch, capsys):
    fake = FakeVast()
    monkeypatch.setattr(fleet, "http", fake)
    monkeypatch.setattr(fleet, "layout_problems", lambda tasks: ["chess: examples/chess needs one script per kind"])
    assert fleet.main(["create", "--count", "3", "--tasks", "chess"]) == 1
    assert "refused: a box rented for these tasks could not run them" in capsys.readouterr().out
    assert fake.calls == []


def test_create_registers_each_instance_pins_its_launch_and_passes_its_audit(registry, monkeypatch, capsys):
    fake = FakeVast()
    monkeypatch.setattr(fleet, "http", fake)
    assert fleet.main(["create", "--count", "2", "--tasks", "chess", "--models", "gpt-6-luna",
                       "claude-haiku-5-5"]) == 0
    boxes = json.loads(registry.read_text())["boxes"]
    launch = boxes[0]["launch"]
    assert fleet_layout.LAUNCH.match(launch) and {b["launch"] for b in boxes} == {launch}
    assert [(b["id"], b["label"], b["offer"], b["suffix"]) for b in boxes] == [
        (901, f"spar-screen-chess-gpt-6-luna-{launch}", 57970273, "luna"),
        (902, f"spar-screen-chess-claude-haiku-5-5-{launch}", 57143771, "haiku")]
    pins = fleet.load_launch(launch)
    assert pins["commit"] == PIN["commit"] and Path(pins["requirements"]).read_bytes() == REQUIREMENTS
    assert "never-stored" not in registry.read_text() and "never-stored" not in capsys.readouterr().out


def test_create_refuses_a_count_that_is_not_the_boxes_asked_for(registry, monkeypatch):
    fake = FakeVast()
    monkeypatch.setattr(fleet, "http", fake)
    assert fleet.main(["create", "--count", "14"]) == 2
    assert fake.calls == []


def test_create_refuses_a_box_the_account_holds_from_another_launch(registry, monkeypatch, capsys):
    fake = FakeVast(instances=[{"id": 5, "label": "spar-screen-chess-gpt-6-luna-l999999"}])
    monkeypatch.setattr(fleet, "http", fake)
    assert fleet.main(["create", "--count", "1", "--tasks", "chess", "--models", "gpt-6-luna"]) == 1
    assert "holds boxes of these (task, model) already: 5" in capsys.readouterr().out
    assert all(call.reads for call in fake.calls)


@pytest.mark.parametrize("error", [http.client.IncompleteRead(b"par"), json.JSONDecodeError("not JSON", "<", 0),
                                   ConnectionResetError("reset")])
def test_a_rent_whose_reply_is_lost_is_registered_by_the_audit(registry, monkeypatch, capsys, error):
    """vast.ai made the instance, the reply broke: the launch stops and its audit registers the instance."""
    fake = FakeVast()

    def transport(call):
        reply = fake(call)
        if call.method == "PUT":
            raise error
        return reply
    monkeypatch.setattr(fleet, "http", transport)
    assert fleet.main(["create", "--count", "2", "--tasks", "chess", "--models", "gpt-6-luna",
                       "claude-haiku-5-5"]) == 1
    boxes = json.loads(registry.read_text())["boxes"]
    assert [(b["id"], b["note"]) for b in boxes] == [(901, "registered by the audit")]
    assert sum(call.method == "PUT" for call in fake.calls) == 1
    assert "unknown: offer 57970273" in capsys.readouterr().out


def test_an_interrupted_launch_is_still_audited(registry, monkeypatch):
    fake = FakeVast()

    def transport(call):
        reply = fake(call)
        if call.method == "PUT":
            raise KeyboardInterrupt
        return reply
    monkeypatch.setattr(fleet, "http", transport)
    with pytest.raises(KeyboardInterrupt):
        fleet.main(["create", "--count", "1", "--tasks", "chess", "--models", "gpt-6-luna"])
    assert [b["id"] for b in json.loads(registry.read_text())["boxes"]] == [901]


def test_the_audit_adopts_only_its_own_launchs_labels(registry, capsys):
    asked = {LABEL: ("chess", "gpt-6-luna")}
    fake = FakeVast(instances=[{"id": 7, "label": LABEL, "dph_total": 0.4}])
    assert fleet.audit(fleet.Vast(writes=False, transport=fake), asked) == 0
    assert [(b["id"], b["launch"], b["dph"]) for b in json.loads(registry.read_text())["boxes"]] == [(7, LAUNCH, 0.4)]
    assert fleet.audit(fleet.Vast(writes=False, transport=fake), asked) == 0    # registered already: no error
    other_launch = FakeVast(instances=[{"id": 8, "label": "spar-screen-chess-gpt-6-luna-l999999"}])
    assert fleet.audit(fleet.Vast(writes=False, transport=other_launch), asked) == 1
    assert "UNREGISTERED 8" in capsys.readouterr().out
    assert [b["id"] for b in json.loads(registry.read_text())["boxes"]] == [7]


def test_the_registry_refuses_an_id_twice(registry):
    entry = fleet.box_entry(3, LABEL, "chess", "gpt-6-luna", SAVED_SEARCH[3], LAUNCH)
    assert fleet.register(entry)
    with pytest.raises(ValueError):
        fleet.register(entry)
    assert fleet.register(entry, exist_ok=True) is False
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


@pytest.mark.parametrize("old_way, same_host, key_at_target", [
    (False, False, None),                                  # another host: the key stays behind
    (False, True, "Bearer test-key-not-real"),             # the API's own host, as vast.ai's 301 to a trailing slash
    (True, False, "Bearer test-key-not-real"),             # the old request leaked it: the test can fail
])
def test_the_api_key_follows_a_redirect_only_to_the_api(tmp_path, monkeypatch, old_way, same_host, key_at_target):
    seen = {}

    def answer(handler, code: int, location: str | None = None) -> None:
        handler.send_response(code)
        if location:
            handler.send_header("Location", location)
        handler.send_header("Content-Length", "0" if location else "2")
        handler.end_headers()
        if not location:
            handler.wfile.write(b"{}")

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            seen["target"] = self.headers.get("Authorization")
            answer(self, 200)

        def log_message(self, *args):
            pass

    target = HTTPServer(("127.0.0.1", 0), Target)

    class Origin(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.endswith("/"):
                seen["target"] = self.headers.get("Authorization")
                answer(self, 200)
                return
            seen["origin"] = self.headers.get("Authorization")
            host = f"127.0.0.1:{self.server.server_port}" if same_host else f"127.0.0.1:{target.server_port}"
            answer(self, 301, f"http://{host}{self.path}/")

        def log_message(self, *args):
            pass

    origin = HTTPServer(("127.0.0.1", 0), Origin)
    for server in (target, origin):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    key = tmp_path / "key"
    key.write_text("test-key-not-real\n")
    monkeypatch.setattr(fleet, "KEY_FILE", key)
    monkeypatch.setattr(fleet, "API", f"http://127.0.0.1:{origin.server_port}")
    monkeypatch.setenv("no_proxy", "*")
    if old_way:
        monkeypatch.setattr(fleet, "build_request", lambda call, k: urllib.request.Request(
            call.url, method=call.method, headers={"Authorization": f"Bearer {k}"}))
    try:
        assert fleet.http(fleet.Call("GET", "/api/v0/users/current")) == {}
    finally:
        for server in (target, origin):
            server.shutdown()
            server.server_close()
    assert seen == {"origin": "Bearer test-key-not-real", "target": key_at_target}


# ---- status and destroy ----

def fresh_marker(**changes):
    return {"id": 7, "epoch": 900.0, "swept": 12, "lost": 0} | changes


def gate(marker, live_label=LABEL, entry_label=LABEL, now=1000.0):
    entry = {"id": 7, "label": entry_label, "destroyed": None}
    return fleet.destroy_refusals(7, entry, {"label": live_label}, marker, now)


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
    fake = FakeVast(instances=[{"id": 7, "label": LABEL}])
    monkeypatch.setattr(fleet, "http", fake)
    add_box()
    assert fleet.main(["destroy", "7"]) == 1
    assert "no capture marker" in capsys.readouterr().out
    assert not any(call.method == "DELETE" for call in fake.calls)


@pytest.fixture
def passing_capture(monkeypatch):
    """A capture that passes and a snapshot that holds, without a box."""
    marker = fresh_marker(epoch=time.time(), time="now", captured=12, excluded={}, docker_accepted=None)
    monkeypatch.setattr(box_capture, "read_marker", lambda instance_id: marker)
    monkeypatch.setattr(box_capture, "check", lambda instance_id, pull: marker)
    monkeypatch.setattr(box_capture, "snapshot", lambda instance_id: {"path": "snapshot.tar.gz", "files": 12})
    monkeypatch.setattr(fleet, "DESTROY_WAIT_S", 0)
    return marker


def test_a_destroy_vast_ai_does_not_carry_out_keeps_the_box_in_sight(registry, monkeypatch, capsys,
                                                                      passing_capture):
    fake = FakeVast(instances=[{"id": 7, "label": LABEL, "start_date": time.time() - 3600, "dph_total": 0.5}],
                    deletes=False)
    monkeypatch.setattr(fleet, "http", fake)
    add_box()
    assert fleet.main(["destroy", "7"]) == 1
    assert "still lists it" in capsys.readouterr().out
    box = fleet.registered(fleet.load_registry(), 7)
    assert box["destroyed"] is None and box["destroy_sent"]
    assert json.loads((fleet.BOXES / "destroyed.jsonl").read_text())["gone_from_account"] is False
    assert fleet.main(["ids"]) == 0 and capsys.readouterr().out.split() == ["7"]
    assert fleet.main(["status"]) == 1 and "destroy sent at" in capsys.readouterr().out
    fake.instances.clear()                                                    # vast.ai carries it out later
    assert fleet.main(["status"]) == 0 and "marked destroyed" in capsys.readouterr().out
    assert fleet.main(["ids"]) == 0 and capsys.readouterr().out == ""


def test_a_destroy_marks_the_box_destroyed_once_it_is_gone(registry, monkeypatch, capsys, passing_capture):
    fake = FakeVast(instances=[{"id": 7, "label": LABEL, "start_date": time.time() - 3600, "dph_total": 0.5}])
    monkeypatch.setattr(fleet, "http", fake)
    add_box()
    assert fleet.main(["destroy", "7"]) == 0
    out = capsys.readouterr().out
    assert "never swept, gone with the box: Docker's images" in out
    assert fleet.registered(fleet.load_registry(), 7)["destroyed"]
    log = json.loads((fleet.BOXES / "destroyed.jsonl").read_text())
    assert log["gone_from_account"] and log["snapshot"]["files"] == 12 and log["files_lost"] == 0


def test_status_flags_a_destroyed_box_the_account_still_lists(registry, monkeypatch, capsys):
    monkeypatch.setattr(fleet, "http", FakeVast(instances=[{"id": 7, "label": LABEL, "dph_total": 0.5}]))
    add_box(destroyed="2026-10-30T00:00:00Z")
    assert fleet.main(["status"]) == 1
    assert "marked destroyed at 2026-10-30T00:00:00Z, still on the account" in capsys.readouterr().out


def test_status_flags_a_box_vast_ai_cannot_start_and_create_avoids_its_machine(registry, monkeypatch, capsys):
    error = "Error: GPU error, unable to start instance."
    fake = FakeVast(instances=[{"id": 7, "label": LABEL, "dph_total": 0.5, "status_msg": error}])
    monkeypatch.setattr(fleet, "http", fake)
    add_box()                                                                   # on 57970273, the cheapest offer
    assert fleet.main(["status"]) == 1
    assert f"!! 7 {LABEL}: vast.ai cannot start it ({error})" in capsys.readouterr().out
    assert json.loads(registry.read_text())["boxes"][0]["start_error"] == error
    assert fleet.main(["create", "--count", "1", "--tasks", "chess", "--models", "gpt-6-luna", "--label-prefix",
                       "spar-screen-retry"]) == 0
    assert json.loads(registry.read_text())["boxes"][-1]["offer"] == 57143771


def test_accept_loss_takes_a_reason_and_logs_what_is_lost(registry, monkeypatch, capsys):
    fake = FakeVast(instances=[{"id": 7, "label": LABEL, "start_date": time.time() - 3600, "dph_total": 0.5}])
    monkeypatch.setattr(fleet, "http", fake)
    monkeypatch.setattr(fleet, "DESTROY_WAIT_S", 0)
    add_box()
    assert fleet.main(["destroy", "7", "--accept-loss", "gone"]) == 2
    assert not any(call.method == "DELETE" for call in fake.calls)

    def unreachable(instance_id, pull):
        raise FileNotFoundError("no ssh config")
    monkeypatch.setattr(box_capture, "run_check", unreachable)
    assert fleet.main(["destroy", "7", "--accept-loss", "the host lost the disk on 10-31"]) == 0
    log = json.loads((fleet.BOXES / "destroyed.jsonl").read_text())
    assert log["accepted_loss"] == "the host lost the disk on 10-31" and log["lost"]["reachable"] is False


def test_entry_prints_a_boxs_fields_and_its_launchs_pins(registry, capsys):
    add_box()
    assert fleet.main(["entry", "7", "task", "commit", "requirements_sha256", "min_ram_gb"]) == 0
    assert capsys.readouterr().out.split() == ["chess", PIN["commit"], PIN["requirements_sha256"], "57"]
    (fleet.launch_dir(LAUNCH) / "requirements.txt").write_bytes(b"inspect-ai==9\n")    # changed after the launch
    assert fleet.main(["entry", "7", "task"]) == 1
    assert "not the package list launch" in capsys.readouterr().err


@pytest.fixture
def chess_cells(monkeypatch, tmp_path):
    monkeypatch.setattr(fleet, "cells", lambda task, kinds: [f"chess.game_{kind}" for kind in kinds])
    monkeypatch.setattr(fleet, "ROOT", tmp_path / "repo")
    return tmp_path / "repo"


def test_run_args_come_from_the_registry(registry, chess_cells, capsys):
    add_box()
    assert fleet.main(["run-args", "7", "--samples", "1", "--kinds", "solo"]) == 0
    assert capsys.readouterr().out.split("\n")[:-1] == [
        "--task", "chess", "--model", "gpt-6-luna", "--suffix", "luna", "--commit", PIN["commit"], "--samples", "1",
        "--kinds", "solo"]
    assert fleet.main(["run-args", "7", "--suffix", "lunapilot"]) == 0 and "lunapilot" in capsys.readouterr().out
    assert fleet.main(["run-args", "7", "--suffix", "haiku"]) == 1
    assert "starting with 'luna'" in capsys.readouterr().err
    assert fleet.main(["run-args", "8"]) == 1


def test_run_args_mock_says_so_in_the_run_names(registry, chess_cells, capsys):
    add_box()
    assert fleet.main(["run-args", "7", "--mock", "--samples", "1", "--kinds", "solo"]) == 0
    assert capsys.readouterr().out.split("\n")[:-1] == [
        "--task", "chess", "--model", "mockllm/model", "--suffix", "lunamock", "--commit", PIN["commit"], "--samples",
        "1", "--kinds", "solo"]
    assert fleet.main(["run-args", "7", "--mock", "--suffix", "lunapilot"]) == 1  # would pass for the screen's runs
    assert "must say mock" in capsys.readouterr().err


def test_run_args_refuse_a_run_name_made_elsewhere(registry, chess_cells, capsys):
    add_box()
    run_dir = chess_cells / "runs" / "game_solo_luna1"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({"eval_log": "/Users/someone/logs/game_solo_luna1/x.eval"}))
    assert fleet.main(["run-args", "7"]) == 1
    assert "runs/game_solo_luna1" in capsys.readouterr().err
    (run_dir / "run.json").write_text(json.dumps({"eval_log": "/root/work/logs/game_solo_luna1/x.eval"}))
    assert fleet.main(["run-args", "7"]) == 0                                 # this box's own, pulled by the watch
    log = chess_cells / "logs" / "screen" / "game_solo_luna2.log"
    log.parent.mkdir(parents=True)
    log.write_text("/Users/someone/.venv/bin/python -m examples.chess.game_solo --name game_solo_luna2\n")
    assert fleet.main(["run-args", "7"]) == 1
    assert "logs/screen/game_solo_luna2.log" in capsys.readouterr().err


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


def test_two_tasks_with_one_cell_stem_are_refused(tmp_path):
    for package in ("chess", "creative_writing"):
        (tmp_path / package).mkdir()
        for kind in fleet_layout.KINDS:
            (tmp_path / package / f"game_{kind}.py").write_text("")
    problems = fleet_layout.layout_problems(["chess", "creative_writing"], tmp_path)
    assert len(problems) == len(fleet_layout.KINDS) and "run names collide" in problems[0]
    assert fleet_layout.layout_problems(["chess"], tmp_path) == []
    assert "needs one script per kind" in fleet_layout.layout_problems(["knowledge_work"], tmp_path)[0]


def test_boxes_labels_and_the_fleet_prefix():
    assert len(fleet_layout.boxes(list(fleet_layout.TASKS), list(fleet_layout.MODELS))) == 15
    with pytest.raises(ValueError):
        fleet_layout.boxes(["checkers"], ["gpt-6-luna"])
    assert fleet_layout.box_label("spar-screen", "chess", "gpt-6-luna", LAUNCH) == LABEL
    with pytest.raises(ValueError):
        fleet_layout.box_label("spar-screen", "chess", "gpt-6-luna", "x")
    assert fleet_layout.label_base(LABEL) == "spar-screen-chess-gpt-6-luna"
    assert fleet_layout.label_base("spar-screen-chess-gpt-6-luna") == "spar-screen-chess-gpt-6-luna"
    assert fleet_layout.is_fleet_label("spar-screen-pilot-chess-gpt-6-luna")
    assert not fleet_layout.is_fleet_label("spar-screens") and not fleet_layout.is_fleet_label(None)


def test_run_names_are_the_screens():
    assert fleet_layout.run_name("chess.game_solo", "luna", 2) == screen.run_name("chess.game_solo", "luna", 2)


# ---- the capture ----

def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


EMPTY_DOCKER = {"docker": {"running": [], "containers": [], "volumes": []}}


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
        home / ".box_env.new": "export KEY=sec", home / "box_id": "7\n",
        home / ".cache" / "uv" / "wheel": "w", home / ".bashrc": "# image\n",
        work / "orbit.egg-info" / "PKG-INFO": "m", home / ".cache" / "matplotlib" / "fontlist.json": "{}",
        home / ".config" / "uv" / "uv-receipt.json": "{}", tmp / "data-gym-cache" / "fb37": "bpe",
        tmp / "inspect.tmp": "t", tmp / "systemd-private-abc" / "x": "s",
    }
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (work / ".gitignore").write_text("runs/\nlogs/\nout/\n.venv/\nbuild/\n__pycache__/\n*.egg-info/\n")
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
    return {"work": work, "home": home, "tmp": tmp, "local": local, "folder": local / "logs" / "boxes" / "7",
            "root": tmp_path}


def sweep(box, proc=None, docker=lambda: EMPTY_DOCKER) -> list[dict]:
    return list(Sweep([str(box["work"]), str(box["home"]), str(box["tmp"])], str(box["work"]), str(box["home"]),
                      str(box["tmp"]), proc=proc or str(box["tmp"] / "no-proc"), docker=docker).lines())


def compare(box, lines, baseline=frozenset(), on_origin=lambda sha: True, **options):
    return box_capture.compare(lines, lambda path: box_capture.local_paths(path, box["folder"]), baseline, on_origin,
                               **options)


def copy_missing(box, report) -> None:
    for path, _ in report.missing:
        target = box_capture.local_path(path, box["folder"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


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
                        trees[f"{home}/.cache/uv"]: 1, box_sweep.KEYS: 2, files[f"{home}/box_id"]: 1,
                        "systemd's private /tmp folders of host services": 1,
                        "the editable install's metadata, written by box_setup.sh": 1,
                        trees[f"{home}/.cache/matplotlib"]: 1, trees[f"{home}/.config/uv"]: 1,
                        trees[f"{box['tmp']}/data-gym-cache"]: 1}
    assert not any("secret" in json.dumps(line) or "sec\"" in json.dumps(line) for line in lines)
    assert {"box_id": "7"} in lines and EMPTY_DOCKER in lines
    assert lines[-1]["done"] is True


def test_the_check_requires_every_other_file_under_the_box_folder(box):
    report = compare(box, sweep(box), bashrc_baseline(box))
    assert sorted(os.path.basename(path) for path, _ in report.missing) == [
        "edited.py", "inspect.tmp", "new.py", "screen.log"]
    assert report.captured == 3 and report.image == 1 and not report.ok
    copy_missing(box, report)
    report = compare(box, sweep(box), bashrc_baseline(box), instance_id=7)
    assert report.ok and report.captured == 7 and report.lost == 0
    assert report.swept == report.captured + report.image + sum(n["files"] for n in report.excluded.values())
    assert len(report.entries) == 8 and {e["kind"] for e in report.entries} == {"file", "image"}


def test_a_baseline_never_counts_the_clones_files_as_the_hosts(box):
    """A baseline swept after the screen holds the outputs; they stay required."""
    late = frozenset((line["file"], line["size"], line["sha256"]) for line in sweep(box) if "file" in line)
    report = compare(box, sweep(box), late)
    assert sorted(os.path.basename(path) for path, _ in report.missing) == ["edited.py", "new.py"]
    assert report.image == 3                                       # .bashrc, screen.log, inspect.tmp: outside the clone


def test_the_check_catches_a_copy_cut_short_or_changed_in_place(box):
    copy_missing(box, compare(box, sweep(box)))
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
    (lambda lines: [line for line in lines if "docker" not in line], "nothing of Docker"),
    (lambda lines: [{"docker": None, "error": "no docker"} if "docker" in line else line for line in lines],
     "cannot tell what Docker holds"),
    (lambda lines: [{"docker": {"running": ["a1"], "containers": ["grader Up 3 hours"], "volumes": ["team"]}}
                    if "docker" in line else line for line in lines], "--accept-docker"),
    (lambda lines: [{"box_id": "8"} if "box_id" in line else line for line in lines], "names itself '8'"),
    (lambda lines: [line for line in lines if "box_id" not in line], "nothing of the box's id"),
])
def test_the_check_fails_on_anything_it_cannot_vouch_for(box, change, words):
    lines = sweep(box)
    copy_missing(box, compare(box, lines))
    assert compare(box, lines, instance_id=7).ok
    report = compare(box, change(lines), instance_id=7)
    assert not report.ok and any(words in problem for problem in report.problems)


def test_a_clone_that_never_was_is_no_failure_and_a_missing_home_is(box):
    shutil.rmtree(box["work"])                                                  # a setup stopped before the clone
    lines = sweep(box)
    copy_missing(box, compare(box, lines))
    assert compare(box, lines, instance_id=7).ok
    shutil.rmtree(box["home"])
    assert {line["unreadable"] for line in sweep(box) if "unreadable" in line} == {str(box["work"]), str(box["home"])}


def test_docker_s_containers_may_go_only_for_a_stated_reason(box):
    held = {"docker": {"running": [], "containers": ["agent1 Exited (137)"], "volumes": ["team"]}}
    lines = sweep(box, docker=lambda: held)
    copy_missing(box, compare(box, lines))
    assert not compare(box, lines).ok
    report = compare(box, lines, accept_docker="saved under /root/docker_saved and pulled")
    assert report.ok and report.docker_accepted["state"] == held["docker"]


def test_a_link_out_of_the_swept_roots_fails_the_check(box):
    elsewhere = box["root"] / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "data.jsonl").write_text("{}\n")
    (box["work"] / "runs" / "r1" / "data").symlink_to(elsewhere)
    (box["work"] / "runs" / "r1" / "inside").symlink_to(box["work"] / "src")
    lines = sweep(box)
    assert [line["outside_link"] for line in lines if "outside_link" in line] == [
        str(box["work"] / "runs" / "r1" / "data")]
    report = compare(box, lines)
    assert any("outside the swept roots" in problem for problem in report.problems)
    assert any(path.endswith("inside") for path, _ in report.missing)    # a link inside is an ordinary link


def fake_proc(box, flags: str, target: Path) -> str:
    proc = box["root"] / "proc"
    (proc / "4242" / "fd").mkdir(parents=True)
    (proc / "4242" / "fdinfo").mkdir()
    (proc / "4242" / "cmdline").write_bytes(b"python3\0-c\0pass\0")
    (proc / "4242" / "fd" / "5").symlink_to(target)
    (proc / "4242" / "fdinfo" / "5").write_text(f"pos:\t0\nflags:\t{flags}\nmnt_id:\t1\n")
    return str(proc)


@pytest.mark.parametrize("flags, counts", [("0100001", True), ("0100002", True), ("0100000", False)])
def test_a_process_holding_an_output_open_for_writing_is_producing(box, flags, counts):
    proc = fake_proc(box, flags, box["work"] / "runs" / "r1" / "run.json")
    producing = [line for line in sweep(box, proc=proc) if "producing" in line]
    assert len(producing) == (2 if counts else 0)                  # before and after the walk
    if counts:
        assert producing[0]["writing"] == [str(box["work"] / "runs" / "r1" / "run.json")]


def test_a_host_service_writing_its_private_tmp_is_not_producing(box):
    proc = fake_proc(box, "0100001", box["tmp"] / "systemd-private-abc" / "x")
    assert not [line for line in sweep(box, proc=proc) if "producing" in line]


def test_the_clone_must_be_at_a_commit_origin_holds(box):
    lines = sweep(box)
    assert lines[0]["tree"] == str(box["work"]) and lines[0]["recorded"] is None
    report = compare(box, lines, on_origin=lambda sha: False)
    assert any("origin/main lacks" in problem for problem in report.problems)


@pytest.fixture
def stand_in_ssh(box, tmp_path, monkeypatch):
    """ssh replaced by a script that runs the command here, docker by one that holds nothing, and the box's ssh
    config written."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ssh").write_text('#!/bin/sh\n# drop "-F <config> box", run the rest here\nshift 3\nexec sh -c "$*"\n')
    (bin_dir / "docker").write_text("#!/bin/sh\nexit 0\n")
    for name in ("ssh", "docker"):
        (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(box_capture, "on_origin", lambda sha: True)
    box["folder"].mkdir(parents=True)
    (box["folder"] / "ssh_config").write_text("Host box\n")
    return bin_dir


def test_the_check_end_to_end_through_a_stand_in_ssh(box, stand_in_ssh, capsys):
    """check --pull: the sweep goes over standard input, rsync fetches what is missing, the second sweep passes, the
    manifest and the marker are written and satisfy the destroy gate, and the snapshot reads back to the manifest."""
    assert box_capture.check(7, pull=False) is None and not box_capture.marker_path(7).exists()
    marker = box_capture.check(7, pull=True)
    assert marker is not None and marker["lost"] == 0 and marker["captured"] == 8 and marker["box_id"] == "7"
    assert (box["folder"] / "files" / str(box["home"] / "screen.log").lstrip("/")).read_text() == "== screen\n"
    assert not (box["folder"] / "files" / str(box["home"] / ".box_env.new").lstrip("/")).exists()
    assert box_capture.read_marker(7) == marker
    manifest = [json.loads(line) for line in box_capture.manifest_path(7).read_text().splitlines()]
    assert len(manifest) == marker["manifest"]["lines"] == 8
    entry = {"id": 7, "label": LABEL, "destroyed": None}
    assert fleet.destroy_refusals(7, entry, {"label": LABEL}, marker, time.time()) == []
    record = box_capture.snapshot(7)
    snapshot = Path(record["path"])
    assert record["files"] == 8 and not os.access(snapshot, os.W_OK)
    with tarfile.open(snapshot) as tar:
        assert tar.extractfile(str(box["work"] / "runs" / "r1" / "run.json").lstrip("/")).read() == b'{"a": 1}\n'
    (box["local"] / "runs" / "r1" / "run.json").write_text('{"a": 9}\n')     # changed after the check
    with pytest.raises(RuntimeError, match="does not hold the manifest"):
        box_capture.snapshot(7)


def test_a_newer_local_copy_stays_and_the_boxs_bytes_go_beside_it(box, stand_in_ssh, capsys):
    assert box_capture.check(7, pull=True)
    local = box["local"] / "runs" / "r1" / "run.json"
    local.write_text('{"a": 2, "rejudged": true}\n')
    os.utime(local, (time.time() + 600, time.time() + 600))                  # a local rewrite, newer than the box's
    (box["local"] / "runs" / "r1" / "stale.json").write_text("{}\n")
    marker = box_capture.check(7, pull=True)
    out = capsys.readouterr().out
    assert marker is not None and len(marker["conflicts"]) == 1 and marker["local_only"] == 1
    assert local.read_text() == '{"a": 2, "rejudged": true}\n'
    assert (box["folder"] / "conflicts" / "runs" / "r1" / "run.json").read_text() == '{"a": 1}\n'
    assert "!! CONFLICT" in out and "LOCAL ONLY runs/r1/stale.json" in out


def test_a_baseline_after_the_setup_is_refused(box, stand_in_ssh, tmp_path, monkeypatch):
    assert box_capture.baseline(7) == 3                                       # the clone and screen.log are there
    assert not (box["folder"] / "baseline.jsonl").exists()
    fresh = tmp_path / "fresh"
    (fresh / "root").mkdir(parents=True)
    (fresh / "tmp").mkdir()
    (fresh / "root" / ".bashrc").write_text("# image\n")
    monkeypatch.setattr(box_capture, "HOME", str(fresh / "root"))
    monkeypatch.setattr(box_capture, "TMP", str(fresh / "tmp"))
    assert box_capture.baseline(7) == 0
    assert box_capture.read_baseline(7) == {(str(fresh / "root" / ".bashrc"), 8,
                                             box_capture.sha256(fresh / "root" / ".bashrc"))}


def test_the_sweep_runs_from_standard_input_with_the_standard_library_alone(box, stand_in_ssh):
    with open(Path(box_capture.SWEEP), "rb") as script:
        result = subprocess.run([sys.executable, "-I", "-", "sweep", str(box["work"]), str(box["home"]),
                                 str(box["tmp"])], stdin=script, capture_output=True, check=True)
    lines = [json.loads(text) for text in result.stdout.decode().splitlines()]
    assert lines[-1]["done"] is True and sum("file" in line for line in lines) == 8
    assert EMPTY_DOCKER in lines


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


def test_the_limit_is_the_tasks_domains(monkeypatch):
    """Only SWE grades after the agents stop: a non-SWE run is hung 90 minutes in, not 110."""
    protein, swe = box_runner.inspect_limit_s("protein_design"), box_runner.inspect_limit_s("software_engineering")
    assert swe - protein == 1800 - settings.HARD_TIME_GRACE_S
    assert (protein + box_runner.GRACE_S) / 60 == 90
    assert box_runner.inspect_limit_s("chess") == protein
    monkeypatch.setitem(box_runner.TASKS, "go", "go")
    with pytest.raises(ValueError, match="no domain"):
        box_runner.inspect_limit_s("go")


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


# ---- what a run left, resume and status ----

@pytest.fixture
def runner_dirs(tmp_path, monkeypatch):
    """runs/, logs/screen/ and the pid file in tmp_path; the clone at PIN's commit."""
    monkeypatch.setattr(settings, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(settings, "ROOT", tmp_path)
    monkeypatch.setattr(box_runner, "SCREEN_LOGS", tmp_path / "logs" / "screen")
    monkeypatch.setattr(box_runner, "ATTEMPTS", tmp_path / "logs" / "screen" / "attempts")
    monkeypatch.setattr(box_runner, "PID_FILE", tmp_path / "screen.pid")
    monkeypatch.setattr(box_runner, "clone_head", lambda: PIN["commit"])
    return tmp_path


def left_run(root: Path, name: str, status: str = "success", failures=(None,)) -> None:
    run_dir = root / "runs" / name
    run_dir.mkdir(parents=True)
    (run_dir / "signals.jsonl").write_text("{}\n")
    eval_log = root / "logs" / name / f"{status}.eval"
    eval_log.parent.mkdir(parents=True, exist_ok=True)
    eval_log.write_text("")
    (run_dir / "run.json").write_text(json.dumps({"eval_log": str(eval_log)}))
    (run_dir / "episodes.jsonl").write_text("".join(json.dumps({"failure": f}) + "\n" for f in failures))


def test_a_run_is_done_only_when_its_eval_succeeded_and_no_episode_failed(runner_dirs, monkeypatch):
    monkeypatch.setattr(box_runner, "eval_status", lambda path: Path(path).stem)
    left_run(runner_dirs, "ok")
    left_run(runner_dirs, "erred", status="error")
    left_run(runner_dirs, "crashed", failures=(None, "harness"))
    assert box_runner.outcome("ok") == ("finished", "its eval succeeded")
    assert box_runner.outcome("erred") == ("failed", "its eval ended error")
    assert box_runner.outcome("crashed") == ("incomplete", "1 episodes failed (harness)")
    assert box_runner.outcome("never") == ("failed", "no signals.jsonl")


class Ended:
    """A launched run that has already ended with code."""

    def __init__(self, code: int):
        self.pid, self.code = 4242, code

    def poll(self) -> int:
        return self.code


def test_a_resume_reruns_what_did_not_finish_and_keeps_the_earlier_attempt(runner_dirs, monkeypatch, capsys):
    logs = runner_dirs / "logs" / "screen"
    logs.mkdir(parents=True)
    monkeypatch.setenv("OPENROUTER_API_KEY", "x" * 30)
    monkeypatch.setattr(box_runner, "cells", lambda task, kinds: [f"protein_design.binder_{k}" for k in kinds])
    monkeypatch.setattr(box_runner, "POLL_S", 0)
    finished = {"binder_solo_dsflash1"}
    monkeypatch.setattr(box_runner, "outcome", lambda name: ("finished", "") if name in finished else
                        ("failed", "its eval ended error"))
    launched = []

    def launch(cell, name, model):
        with open(logs / f"{name}.log", "x") as log:
            log.write("second attempt\n")
        launched.append(name)
        finished.add(name)
        return Ended(0)
    monkeypatch.setattr(box_runner, "launch", launch)
    (logs / "binder_solo_cheat_dsflash1.log").write_text("first attempt: rc=3\n")
    (logs / "box_protein_design_dsflash.json").write_text('{"first": "plan"}\n')
    code = box_runner.main(["run", "--task", "protein_design", "--model", "deepseek-flash-openrouter", "--suffix",
                            "dsflash", "--commit", PIN["commit"], "--samples", "1", "--kinds", "solo", "solo_cheat"])
    assert code == 0 and launched == ["binder_solo_cheat_dsflash1"]
    attempts = logs / "attempts"
    assert (attempts / "binder_solo_cheat_dsflash1.1.log").read_text() == "first attempt: rc=3\n"
    assert (attempts / "box_protein_design_dsflash.1.json").read_text() == '{"first": "plan"}\n'
    plan = json.loads((logs / "box_protein_design_dsflash.json").read_text())
    assert plan["earlier_plan"].endswith("box_protein_design_dsflash.1.json")
    assert plan["runs"]["binder_solo_dsflash1"]["ended"] == box_runner.BEFORE
    assert plan["runs"]["binder_solo_cheat_dsflash1"]["again"].startswith("its eval ended error")
    assert box_runner.set_aside(logs / "binder_solo_cheat_dsflash1.log").name == "binder_solo_cheat_dsflash1.2.log"


def test_the_runner_refuses_a_clone_away_from_the_pinned_commit(runner_dirs, capsys):
    assert box_runner.main(["run", "--task", "protein_design", "--model", "deepseek-flash-openrouter", "--suffix",
                            "t", "--commit", "0" * 40]) == 1
    assert "the fleet pinned 0000" in capsys.readouterr().out and not (runner_dirs / "screen.pid").exists()


def test_the_runner_refuses_without_the_models_key(runner_dirs, monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(box_runner, "cells", lambda task, kinds: ["protein_design.binder_solo"])
    assert box_runner.main(["run", "--task", "protein_design", "--model", "deepseek-flash-openrouter", "--suffix",
                            "t", "--commit", PIN["commit"]]) == 1
    assert "OPENROUTER_API_KEY" in capsys.readouterr().out and not (runner_dirs / "screen.pid").exists()


def test_the_runner_refuses_a_task_without_every_cell(runner_dirs, monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "x" * 30)
    monkeypatch.setattr(box_runner, "cells", lambda task, kinds: fleet_layout.cells(task, kinds, Path("/nowhere")))
    assert box_runner.main(["run", "--task", "chess", "--model", "deepseek-flash-openrouter", "--suffix", "t",
                            "--commit", PIN["commit"]]) == 1
    assert "refused: examples/chess needs one script per kind" in capsys.readouterr().out
    assert not (runner_dirs / "screen.pid").exists()


def test_build_refuses_a_task_without_build_steps(capsys, monkeypatch):
    assert set(box_runner.BUILDS) == set(box_runner.TASKS)
    monkeypatch.setitem(box_runner.TASKS, "go", "go")
    assert box_runner.main(["build", "--task", "go"]) == 1
    assert "no build steps for go" in capsys.readouterr().out


def test_status_names_each_runs_state(monkeypatch):
    states = {"a": "finished", "c": "incomplete"}
    monkeypatch.setattr(box_runner, "outcome", lambda name: (states.get(name, "failed"), ""))

    def record(**fields):
        return {"pid": None, "exit": None, "hung": None} | fields

    assert box_runner.state("a", record(exit=0)) == "finished"
    assert box_runner.state("b", record(exit=0)) == "failed"            # ended well without its signals
    assert box_runner.state("c", record(exit=0)) == "incomplete"        # an episode failed in the harness
    assert box_runner.state("b", record(exit=1)) == "failed"
    assert box_runner.state("b", record(exit=-9, hung="nothing written")) == "hung"
    assert box_runner.state("b", record()) == "waiting"
    assert box_runner.state("b", record(pid=os.getpid())) == "lost"     # a live pid that is not this run


def write_plan(runner_dirs: Path, runs: dict) -> None:
    logs = runner_dirs / "logs" / "screen"
    logs.mkdir(parents=True, exist_ok=True)
    plan = {"task": "protein_design", "model": "m", "suffix": "s", "runner_pid": 999_999_999,
            "runs": {name: {"pid": None, "started": None, "ended": None, "exit": None, "hung": None} | fields
                     for name, fields in runs.items()}}
    (logs / "box_protein_design_s.json").write_text(json.dumps(plan))


def test_status_flags_a_runner_that_ended_with_runs_unwatched(runner_dirs, monkeypatch, capsys):
    monkeypatch.setattr(box_runner, "outcome", lambda name: ("finished", ""))
    write_plan(runner_dirs, {"a": {"exit": 0, "ended": "2026-10-30T06:00:00Z"}, "b": {}})
    assert box_runner.main(["status"]) == 1
    assert "!! the runner ended with 0 runs running and 1 waiting" in capsys.readouterr().out


def test_status_says_when_every_run_has_ended(runner_dirs, monkeypatch, capsys):
    monkeypatch.setattr(box_runner, "outcome", lambda name: ("finished", ""))
    write_plan(runner_dirs, {"a": {"exit": 0, "ended": "2026-10-30T06:00:00Z"},
                             "b": {"exit": 0, "ended": box_runner.BEFORE}})
    assert box_runner.main(["status"]) == 0
    assert "all runs ended, the last at 2026-10-30T06:00:00Z" in capsys.readouterr().out


# ---- the watch ----

@pytest.mark.parametrize("every", ["abc", "0", ""])
def test_the_watch_refuses_an_interval_that_is_no_whole_minute(every):
    result = subprocess.run(["bash", str(Path(box_capture.SWEEP).with_name("watch.sh")), "--once", "--every", every],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 2 and "whole minutes" in result.stdout
