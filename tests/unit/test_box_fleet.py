"""The vast.ai fleet without vast.ai: offer filtering and ranking on offers from a real search, the registry and the
launch pins, the create, status and destroy paths against a fake API (a launch cut short, a destroy vast.ai does not
carry out, the fleet's $/h cap), the key on a redirect, the destroy gate, run-args, entry and repin, the labels, the
capture's sweep and comparison on two fake trees (also end to end through a stand-in ssh with the real rsync, with the
manifest, the snapshot and a conflict), the capture's places and its refusal of a run made elsewhere, and the watch's
interval check. The boxes are those of studies/plans/disregulator_existence_2.yaml on two boxes."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shutil
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

from scripts.box import (
    box_capture,
    box_sweep,
    fleet,
    fleet_create,
    fleet_destroy,
    fleet_layout,
    fleet_plan,
    fleet_registry,
    fleet_status,
    vast_api,
    vast_offers,
)
from scripts.box.box_sweep import Sweep
from scripts.study import run_study
from tests.support.run_files import write_text

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
PLAN = fleet_layout.ROOT / "studies" / "plans" / "disregulator_existence_2.yaml"
STUDY = "disregulator_existence_2"
ASSIGNMENT = fleet_plan.assign(PLAN, boxes=2)
BOX = ASSIGNMENT.boxes[0]
LABEL = f"spar-screen-{BOX.name}-{LAUNCH}"
TWO_BOXES = ("--plan", str(PLAN), "--boxes", "2")
ONE_BOX = (*TWO_BOXES, "--only", BOX.name)


# ---- offers ----

def test_candidates_keep_what_can_hold_a_box_cheapest_first():
    assert [o["id"] for o in vast_offers.candidates(SAVED_SEARCH)] == [57970273, 57143771, 58340174, 58352110]
    assert [o["id"] for o in vast_offers.candidates(SAVED_SEARCH, max_dph=2.0)][-1] == 58339037


def test_each_task_rents_at_its_own_floor():
    assert fleet_layout.floor_of(["swe"]) == (32, 64)
    assert fleet_layout.floor_of(["creative_writing"]) == (16, 32)
    assert fleet_layout.floor_of(["swe", "creative_writing"]) == (32, 64)
    assert fleet_registry.minimums([32, 64])["min_cores"] == 30
    assert fleet_registry.minimums([16, 32])["min_cores"] == 14
    small = {**SAVED_SEARCH[0], "cpu_cores_effective": 16, "cpu_cores": 16, "cpu_ram": 40000}
    assert vast_offers.rejections(small, floor=fleet_layout.floor_of(["creative_writing"])) == []
    assert vast_offers.rejections(small, floor=fleet_layout.floor_of(["swe"])) == [
        "fewer than 32 cores", "less than 64 GB of RAM in its share"]


def test_each_rejected_offer_says_why():
    why = {o["id"]: vast_offers.rejections(o, floor=(32, 64)) for o in SAVED_SEARCH}
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
    assert [o["id"] for o in vast_offers.candidates([SAVED_SEARCH[3], twin])] == [1, 57970273]


def test_the_search_is_a_read_and_asks_for_what_the_box_needs():
    call = vast_offers.search_call()
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

    def __call__(self, call: vast_api.Call) -> dict:
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
    monkeypatch.setattr(fleet_registry, "REGISTRY", path)
    monkeypatch.setattr(fleet_registry, "BOXES", tmp_path / "boxes")
    monkeypatch.setattr(box_capture, "BOXES", tmp_path / "boxes")
    monkeypatch.setattr(fleet_registry, "fleet_pin", lambda commit="HEAD": dict(PIN))
    monkeypatch.setattr(fleet_create, "guard_alive", lambda: True)   # the cloud box guard's daemon, never the real one
    monkeypatch.setattr(run_study, "RUNS", tmp_path / "runs")         # no run of this checkout's runs/ is held here
    return path


def add_box(instance_id=7, box=BOX, label=LABEL, launch=LAUNCH, **fields) -> dict:
    """A registered box of a launch whose pins are written."""
    if not fleet_registry.launch_dir(launch).exists():
        fleet_registry.write_launch(launch, dict(PIN))
    entry = fleet_registry.box_entry(instance_id, label, ASSIGNMENT, box, SAVED_SEARCH[3], launch, PIN["commit"])
    entry |= fields
    fleet_registry.register(entry)
    return entry


def test_a_client_without_writes_refuses_all_but_reads():
    fake = FakeVast()
    vast = vast_api.Vast(writes=False, transport=fake)
    for call in (vast_offers.create_call(1, "spar-screen-x"), vast_api.Call("DELETE", "/api/v0/instances/1/")):
        with pytest.raises(PermissionError):
            vast.send(call)
    assert fake.calls == []
    assert vast.send(vast_offers.search_call())["offers"]


def test_the_dry_run_prints_every_request_and_sends_none(registry, monkeypatch, capsys):
    fake = FakeVast()
    monkeypatch.setattr(vast_api, "http", fake)
    code = fleet.main(["create", *TWO_BOXES, "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    assert out.count("would send: PUT https://console.vast.ai/api/v0/asks/") == 2
    assert '"label": "spar-screen-disregulator-existence-2-2-l' in out and '"vm": true' in out
    assert "disregulator-existence-2-1: 5 runs, 18 agents in all, 44 at once; $45.00" in out
    assert f"commit {PIN['commit']}" in out
    assert all(call.reads for call in fake.calls) and not registry.exists()
    assert not fleet_registry.BOXES.exists()                                           # no launch pinned on a dry run
    assert "never-printed" not in out


def test_create_takes_no_offer_from_a_skipped_machine(registry, monkeypatch, capsys):
    """A VM can come up smaller than its offer; its machine's next offer then goes untaken."""
    monkeypatch.setattr(vast_api, "http", FakeVast())
    assert fleet.main(["create", *ONE_BOX, "--dry-run"]) == 0
    first = re.search(r"/asks/(\d+)/", capsys.readouterr().out).group(1)
    machine = next(o["machine_id"] for o in SAVED_SEARCH if str(o["id"]) == first)
    assert fleet.main(["create", *ONE_BOX, "--skip-machine", str(machine), "--dry-run"]) == 0
    assert f"/asks/{first}/" not in capsys.readouterr().out


def test_the_dry_run_names_the_boxes_no_offer_is_left_for(registry, monkeypatch, capsys):
    """Six SWE boxes and four offers that hold one."""
    monkeypatch.setattr(vast_api, "http", FakeVast())
    assert fleet.main(["create", "--plan", str(PLAN), "--boxes", "6", "--dry-run"]) == 1
    out = capsys.readouterr().out
    assert out.count("would send: PUT") == 4 and "4 of 6 requests printed" in out


def test_the_dry_run_stops_at_the_fleets_cap(registry, monkeypatch, capsys):
    monkeypatch.setattr(vast_api, "http", FakeVast())
    assert fleet.main(["create", *TWO_BOXES, "--max-fleet-dph", "0.5", "--dry-run"]) == 1
    out = capsys.readouterr().out
    assert "!!: the fleet would bill $0.688/h, past its cap $0.50/h" in out
    assert out.count("would send: PUT") == 1 and "takes the fleet to $0.688/h" in out


def test_create_refuses_past_the_fleets_cap_counting_the_boxes_it_holds(registry, monkeypatch, capsys):
    fake = FakeVast()
    monkeypatch.setattr(vast_api, "http", fake)
    add_box(5, dph=14.5, study="regulator_scale_1")
    assert fleet.main(["create", *TWO_BOXES]) == 1
    assert "refused: the fleet would bill $15.188/h" in capsys.readouterr().out
    assert all(call.reads for call in fake.calls)


def test_create_refuses_a_model_on_another_key(registry, monkeypatch, capsys):
    fake = FakeVast()
    monkeypatch.setattr(vast_api, "http", fake)
    monkeypatch.setattr(fleet_plan, "key_problems", lambda box: ["openai/x needs OPENAI_API_KEY"])
    assert fleet.main(["create", *TWO_BOXES]) == 1
    out = capsys.readouterr().out
    assert "!! openai/x needs OPENAI_API_KEY" in out and "refused: a box rented for these runs" in out
    assert fake.calls == []


def test_create_refuses_a_run_held_here_and_a_target_another_open_box_runs(registry, monkeypatch, capsys):
    """A box makes every run of its targets that its own runs/ lacks, so a run held here would come back as a second
    run of its name; and two boxes of one study never run the same target."""
    fake = FakeVast()
    monkeypatch.setattr(vast_api, "http", fake)
    name = BOX.runs[0].name
    write_text(run_study.RUNS / STUDY / name / "processed/run.json", "{}")
    assert fleet.main(["create", *TWO_BOXES]) == 1
    assert f"runs/{STUDY}/{name} is here already" in capsys.readouterr().out
    (run_study.RUNS / STUDY / name / "processed/run.json").unlink()
    add_box(5)
    assert fleet.main(["create", *TWO_BOXES]) == 1
    assert f"box 5 of {STUDY} runs {', '.join(sorted(BOX.targets))} already" in capsys.readouterr().out
    assert fake.calls == []


def test_a_package_list_pins_inspect_to_a_release_or_a_git_commit():
    """ORBIT 2.0 names inspect-ai by git commit, which uv freezes as a direct reference; a launch's list must hold
    one exact pin either way."""
    commit = "c060c201c5681dd09dbe3edb5f01e5947218bfb9"
    url = "git+https://github.com/UKGovernmentBEIS/inspect_ai.git"
    assert fleet_registry.pins_inspect(b"anyio==4.15.1\ninspect-ai==0.3.263\nopenai==3.37.0\n")
    assert fleet_registry.pins_inspect(f"anyio==4.15.1\ninspect-ai @ {url}@{commit}\nopenai==3.37.0\n".encode())
    assert not fleet_registry.pins_inspect(b"anyio==4.15.1\nopenai==3.37.0\n")
    assert not fleet_registry.pins_inspect(f"inspect-ai @ {url}@main\n".encode())            # a branch moves
    assert not fleet_registry.pins_inspect(f"inspect-ai @ {url}\n".encode())
    assert not fleet_registry.pins_inspect(b"inspect-ai-extras==1.0\ninspect-swarm==0.4.1\n")


def test_create_registers_each_instance_pins_its_launch_and_passes_its_audit(registry, monkeypatch, capsys):
    fake = FakeVast()
    monkeypatch.setattr(vast_api, "http", fake)
    assert fleet.main(["create", *TWO_BOXES]) == 0
    boxes = json.loads(registry.read_text())["boxes"]
    launch = boxes[0]["launch"]
    assert fleet_layout.LAUNCH.match(launch) and {b["launch"] for b in boxes} == {launch}
    assert [(b["id"], b["label"], b["offer"], b["box"]) for b in boxes] == [
        (901, f"spar-screen-disregulator-existence-2-1-{launch}", 57970273, "disregulator-existence-2-1"),
        (902, f"spar-screen-disregulator-existence-2-2-{launch}", 57143771, "disregulator-existence-2-2")]
    for entry, box in zip(boxes, ASSIGNMENT.boxes):
        assert {key: entry[key] for key in ("study", "plan", "gates", "targets", "max_agents", "runs", "cap_usd",
                                            "tasks", "floor", "commit")} == {
            "study": STUDY, "plan": "studies/plans/disregulator_existence_2.yaml", "gates": None,
            "targets": box.targets, "max_agents": 44, "runs": 5, "cap_usd": 45.0, "tasks": ["swe"], "floor": [32, 64],
            "commit": PIN["commit"]}
    pins = fleet_registry.load_launch(launch)
    assert pins["commit"] == PIN["commit"] and Path(pins["requirements"]).read_bytes() == REQUIREMENTS
    assert "never-stored" not in registry.read_text() and "never-stored" not in capsys.readouterr().out


def test_create_refuses_more_boxes_than_groups_of_targets(registry, monkeypatch, capsys):
    fake = FakeVast()
    monkeypatch.setattr(vast_api, "http", fake)
    assert fleet.main(["create", "--plan", str(PLAN), "--boxes", "11"]) == 2
    assert "10 groups of targets for 11 boxes" in capsys.readouterr().out
    assert fake.calls == []


def test_create_refuses_without_the_cloud_box_guard_and_a_dry_run_needs_none(registry, monkeypatch, capsys):
    """No box is rented while no guard daemon watches it (2026-11-01: 19 idle boxes, $126). A dry run sends
    nothing, so it needs no guard."""
    fake = FakeVast()
    monkeypatch.setattr(vast_api, "http", fake)
    monkeypatch.setattr(fleet_create, "guard_alive", lambda: False)
    assert fleet.main(["create", *TWO_BOXES]) == 1
    assert "no cloud box guard daemon" in capsys.readouterr().out
    assert fake.calls == []
    assert fleet.main(["create", *TWO_BOXES, "--dry-run"]) == 0


def test_create_refuses_only_names_outside_the_assignment(registry, monkeypatch, capsys):
    fake = FakeVast()
    monkeypatch.setattr(vast_api, "http", fake)
    assert fleet.main(["create", *TWO_BOXES, "--only", "regulator-scale-1-1"]) == 2
    assert "not boxes of the assignment: regulator-scale-1-1" in capsys.readouterr().out
    assert fake.calls == []


def test_create_refuses_a_label_destroy_would_not_act_on(registry, monkeypatch, capsys):
    fake = FakeVast()
    monkeypatch.setattr(vast_api, "http", fake)
    assert fleet.main(["create", *ONE_BOX, "--label-prefix", "retry-spar-screen"]) == 2
    assert "--label-prefix must start with spar-screen" in capsys.readouterr().out
    assert fake.calls == []


def test_create_refuses_a_box_the_account_holds_from_another_launch(registry, monkeypatch, capsys):
    fake = FakeVast(instances=[{"id": 5, "label": f"spar-screen-{BOX.name}-l999999"}])
    monkeypatch.setattr(vast_api, "http", fake)
    assert fleet.main(["create", *ONE_BOX]) == 1
    assert "holds these boxes already: 5" in capsys.readouterr().out
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
    monkeypatch.setattr(vast_api, "http", transport)
    assert fleet.main(["create", *TWO_BOXES]) == 1
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
    monkeypatch.setattr(vast_api, "http", transport)
    with pytest.raises(KeyboardInterrupt):
        fleet.main(["create", *ONE_BOX])
    assert [b["id"] for b in json.loads(registry.read_text())["boxes"]] == [901]


def test_the_audit_adopts_only_its_own_launchs_labels(registry, capsys):
    rental = fleet_create.Rental(LAUNCH, ASSIGNMENT, {LABEL: BOX}, PIN["commit"], 0.0, 1.0, 15.0, False)
    fake = FakeVast(instances=[{"id": 7, "label": LABEL, "dph_total": 0.4}])
    assert fleet_create.audit(vast_api.Vast(writes=False, transport=fake), rental) == 0
    assert [(b["id"], b["launch"], b["dph"], b["targets"]) for b in json.loads(registry.read_text())["boxes"]] == [
        (7, LAUNCH, 0.4, BOX.targets)]
    assert fleet_create.audit(vast_api.Vast(writes=False, transport=fake), rental) == 0   # registered already: no error
    other_launch = FakeVast(instances=[{"id": 8, "label": f"spar-screen-{BOX.name}-l999999"}])
    assert fleet_create.audit(vast_api.Vast(writes=False, transport=other_launch), rental) == 1
    assert "UNREGISTERED 8" in capsys.readouterr().out
    assert [b["id"] for b in json.loads(registry.read_text())["boxes"]] == [7]


def test_the_registry_refuses_an_id_twice(registry):
    entry = fleet_registry.box_entry(3, LABEL, ASSIGNMENT, BOX, SAVED_SEARCH[3], LAUNCH, PIN["commit"])
    assert fleet_registry.register(entry)
    with pytest.raises(ValueError):
        fleet_registry.register(entry)
    assert fleet_registry.register(entry, exist_ok=True) is False
    assert fleet_registry.registered(fleet_registry.load_registry(), 3)["ram_gb"] == 128.7


def test_every_page_of_the_instances_is_read():
    pages = iter([{"instances": [{"id": 1}], "next_token": "t", "total_instances": 2},
                  {"instances": [{"id": 2}], "next_token": None, "total_instances": 2}])
    rows = vast_api.list_instances(vast_api.Vast(writes=False, transport=lambda call: next(pages)))
    assert [r["id"] for r in rows] == [1, 2]
    short = iter([{"instances": [{"id": 1}], "next_token": None, "total_instances": 2}])
    with pytest.raises(RuntimeError):
        vast_api.list_instances(vast_api.Vast(writes=False, transport=lambda call: next(short)))


def test_ssh_goes_direct_when_the_box_maps_its_port(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet_registry, "BOXES", tmp_path)
    record = {"public_ipaddr": "213.97.25.93", "ports": {"22/tcp": [{"HostPort": "22444"}]},
              "ssh_host": "ssh1.vast.ai", "ssh_port": 35410}
    ssh = vast_api.ssh_endpoint(record)
    assert ssh == {"direct": {"host": "213.97.25.93", "port": 22444}, "proxy": {"host": "ssh1.vast.ai", "port": 35410}}
    config = fleet_status.write_ssh_config(42, ssh).read_text()
    assert "HostName 213.97.25.93\n  Port 22444\n" in config and "BatchMode yes" in config
    assert vast_api.ssh_endpoint({"ssh_host": "ssh1.vast.ai", "ssh_port": 35410})["direct"] is None


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
    monkeypatch.setattr(vast_api, "KEY_FILE", key)
    monkeypatch.setattr(vast_api, "API", f"http://127.0.0.1:{origin.server_port}")
    monkeypatch.setenv("no_proxy", "*")
    if old_way:
        monkeypatch.setattr(vast_api, "build_request", lambda call, k: urllib.request.Request(
            call.url, method=call.method, headers={"Authorization": f"Bearer {k}"}))
    try:
        assert vast_api.http(vast_api.Call("GET", "/api/v0/users/current")) == {}
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
    return fleet_destroy.destroy_refusals(7, entry, {"label": live_label}, marker, now)


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
    assert "not a box of this fleet" in gate(fresh_marker(), live_label="spar-screen-regulator-scale-1-1")[0]
    assert "not in the registry" in fleet_destroy.destroy_refusals(7, None, {"label": "x"}, fresh_marker(), 1000.0)[0]


def test_destroy_without_a_marker_sends_no_delete(registry, tmp_path, monkeypatch, capsys):
    fake = FakeVast(instances=[{"id": 7, "label": LABEL}])
    monkeypatch.setattr(vast_api, "http", fake)
    add_box()
    assert fleet.main(["destroy", "7"]) == 1
    assert "no capture marker" in capsys.readouterr().out
    assert not any(call.method == "DELETE" for call in fake.calls)


@pytest.fixture
def passing_capture(monkeypatch):
    """A capture that passes and a snapshot that holds, without a box."""
    marker = fresh_marker(epoch=time.time(), time="now", captured=12, excluded={}, docker_accepted=None)
    monkeypatch.setattr(box_capture, "read_marker", lambda instance_id: marker)
    monkeypatch.setattr(box_capture, "check", lambda instance_id, pull, accept_docker=None: marker)
    monkeypatch.setattr(box_capture, "snapshot", lambda instance_id: {"path": "snapshot.tar.gz", "files": 12})
    monkeypatch.setattr(fleet_destroy, "DESTROY_WAIT_S", 0)
    return marker


def test_a_destroy_vast_ai_does_not_carry_out_keeps_the_box_in_sight(registry, monkeypatch, capsys,
                                                                      passing_capture):
    fake = FakeVast(instances=[{"id": 7, "label": LABEL, "start_date": time.time() - 3600, "dph_total": 0.5}],
                    deletes=False)
    monkeypatch.setattr(vast_api, "http", fake)
    add_box()
    assert fleet.main(["destroy", "7"]) == 1
    assert "still lists it" in capsys.readouterr().out
    box = fleet_registry.registered(fleet_registry.load_registry(), 7)
    assert box["destroyed"] is None and box["destroy_sent"]
    assert json.loads((fleet_registry.BOXES / "destroyed.jsonl").read_text())["gone_from_account"] is False
    assert fleet.main(["ids"]) == 0 and capsys.readouterr().out.split() == ["7"]
    assert fleet.main(["status"]) == 1 and "destroy sent at" in capsys.readouterr().out
    fake.instances.clear()                                                    # vast.ai carries it out later
    assert fleet.main(["status"]) == 0 and "marked destroyed" in capsys.readouterr().out
    assert fleet.main(["ids"]) == 0 and capsys.readouterr().out == ""


def test_a_destroy_marks_the_box_destroyed_once_it_is_gone(registry, monkeypatch, capsys, passing_capture):
    fake = FakeVast(instances=[{"id": 7, "label": LABEL, "start_date": time.time() - 3600, "dph_total": 0.5}])
    monkeypatch.setattr(vast_api, "http", fake)
    add_box()
    assert fleet.main(["destroy", "7"]) == 0
    out = capsys.readouterr().out
    assert "never swept, gone with the box: Docker's images" in out
    assert fleet_registry.registered(fleet_registry.load_registry(), 7)["destroyed"]
    log = json.loads((fleet_registry.BOXES / "destroyed.jsonl").read_text())
    assert log["gone_from_account"] and log["snapshot"]["files"] == 12 and log["files_lost"] == 0


def test_status_flags_a_destroyed_box_the_account_still_lists(registry, monkeypatch, capsys):
    monkeypatch.setattr(vast_api, "http", FakeVast(instances=[{"id": 7, "label": LABEL, "dph_total": 0.5}]))
    add_box(destroyed="2026-10-30T00:00:00Z")
    assert fleet.main(["status"]) == 1
    assert "marked destroyed at 2026-10-30T00:00:00Z, still on the account" in capsys.readouterr().out


def test_status_flags_a_box_vast_ai_cannot_start_and_create_avoids_its_machine(registry, monkeypatch, capsys):
    error = "Error: GPU error, unable to start instance."
    fake = FakeVast(instances=[{"id": 7, "label": LABEL, "dph_total": 0.5, "status_msg": error, "cur_state": "stopped",
                                "storage_total_cost": 0.056}])
    monkeypatch.setattr(vast_api, "http", fake)
    add_box()                                                                   # on 57970273, the cheapest offer
    assert fleet.main(["status"]) == 1
    out = capsys.readouterr().out
    assert f"!! 7 {LABEL}: vast.ai cannot start it ({error})" in out and "bill $0.056/h" in out
    assert json.loads(registry.read_text())["boxes"][0]["start_error"] == error
    assert fleet.main(["create", *ONE_BOX, "--label-prefix", "spar-screen-retry"]) == 0
    assert json.loads(registry.read_text())["boxes"][-1]["offer"] == 57143771


def test_accept_loss_takes_a_reason_and_logs_what_is_lost(registry, monkeypatch, capsys):
    fake = FakeVast(instances=[{"id": 7, "label": LABEL, "start_date": time.time() - 3600, "dph_total": 0.5}])
    monkeypatch.setattr(vast_api, "http", fake)
    monkeypatch.setattr(fleet_destroy, "DESTROY_WAIT_S", 0)
    add_box()
    assert fleet.main(["destroy", "7", "--accept-loss", "gone"]) == 2
    assert not any(call.method == "DELETE" for call in fake.calls)

    def unreachable(instance_id, pull):
        raise FileNotFoundError("no ssh config")
    monkeypatch.setattr(box_capture, "run_check", unreachable)
    assert fleet.main(["destroy", "7", "--accept-loss", "the host lost the disk on 10-31"]) == 0
    log = json.loads((fleet_registry.BOXES / "destroyed.jsonl").read_text())
    assert log["accepted_loss"] == "the host lost the disk on 10-31" and log["lost"]["reachable"] is False


def test_entry_prints_a_boxs_fields_and_its_launchs_pins(registry, capsys):
    add_box()
    assert fleet.main(["entry", "7", "tasks", "commit", "requirements_sha256", "min_ram_gb", "box", "study", "key",
                       "max_agents", "cap_usd"]) == 0
    assert capsys.readouterr().out.split() == ["swe", PIN["commit"], PIN["requirements_sha256"], "57", BOX.name, STUDY,
                                               "OPENROUTER_API_KEY", "44", "45.0"]
    (fleet_registry.launch_dir(LAUNCH) / "requirements.txt").write_bytes(b"inspect-ai==9\n")     # changed later
    assert fleet.main(["entry", "7", "tasks"]) == 1
    assert "not the package list launch" in capsys.readouterr().err


def test_repin_moves_a_box_while_the_package_list_stays(registry, monkeypatch, capsys):
    add_box()
    moved = "c" * 40
    monkeypatch.setattr(fleet_registry, "fleet_pin", lambda commit: PIN | {"commit": commit})
    assert fleet.main(["repin", "7", "--commit", moved]) == 0
    assert f"{PIN['commit']} to {moved}" in capsys.readouterr().out
    assert fleet.main(["entry", "7", "commit"]) == 0 and capsys.readouterr().out.split() == [moved]
    monkeypatch.setattr(fleet_registry, "fleet_pin", lambda commit: PIN | {"requirements_sha256": "0" * 64})
    assert fleet.main(["repin", "7", "--commit", "d" * 40]) == 1
    assert "a new list takes a new launch" in capsys.readouterr().err
    assert fleet.main(["entry", "7", "commit"]) == 0 and capsys.readouterr().out.split() == [moved]
    assert fleet.main(["repin", "8", "--commit", moved]) == 1


def test_run_args_start_the_launcher_on_the_box_s_targets_from_the_registry(registry, capsys):
    add_box()
    assert fleet.main(["run-args", "7"]) == 0
    assert (out := capsys.readouterr().out.split("\n")[:-1]) == [
        "scripts.study.run_study", "studies/plans/disregulator_existence_2.yaml", "--yes", "--only", *BOX.targets,
        "--limit", "max_agents=44"]
    parsed = run_study.parse_arguments(out[1:])                     # the launcher's own options read them as meant
    assert (parsed.yes, parsed.only, parsed.limit, parsed.mock) == (True, BOX.targets, ["max_agents=44"], False)
    assert fleet.main(["run-args", "7", "--mock"]) == 0
    assert run_study.parse_arguments(capsys.readouterr().out.split("\n")[1:-1]).mock
    assert fleet.main(["run-args", "8"]) == 1


def test_run_args_pass_the_gates_and_every_target_of_the_box_s_chains(registry, capsys):
    plan, gates = (fleet_layout.ROOT / "studies" / folder / "regulator_scale_1.yaml" for folder in ("plans", "gated"))
    assignment = fleet_plan.assign(plan, gates, boxes=4)
    box = assignment.boxes[0]
    fleet_registry.write_launch(LAUNCH, dict(PIN))
    fleet_registry.register(fleet_registry.box_entry(7, LABEL, assignment, box, SAVED_SEARCH[3], LAUNCH,
                                                     PIN["commit"]))
    assert fleet.main(["run-args", "7"]) == 0
    out = capsys.readouterr().out.split("\n")[:-1]
    assert out[:5] == ["scripts.study.run_study", "studies/plans/regulator_scale_1.yaml", "--yes", "--gates",
                       "studies/gated/regulator_scale_1.yaml"]
    assert out[5:] == ["--only", *box.targets, "--limit", "max_agents=44"]
    assert "ian_flipt_deepseek_n5_reg20_weak" in box.targets and "ian_flipt_deepseek_n5_reg20_top" in box.targets
    parsed = run_study.parse_arguments(out[1:])
    assert (str(parsed.gates), parsed.only) == ("studies/gated/regulator_scale_1.yaml", box.targets)
    assert fleet.main(["run-args", "7", "--mock"]) == 0                  # the launcher takes no --gates with --mock
    mocked = run_study.parse_arguments(capsys.readouterr().out.split("\n")[1:-1])
    in_plan = run_study.load_plan(plan)["targets"]
    assert mocked.mock and mocked.gates is None and mocked.only == [t for t in box.targets if t in in_plan]


def test_run_args_refuse_a_run_made_elsewhere_and_a_model_on_another_key(registry, capsys, monkeypatch):
    add_box()
    run_json = run_study.RUNS / STUDY / BOX.runs[0].name / "processed/run.json"
    write_text(run_json, json.dumps({"eval_log": f"/Users/someone/logs/{STUDY}/x/x.eval"}))
    assert fleet.main(["run-args", "7"]) == 1
    assert f"{STUDY}/{BOX.runs[0].name}" in capsys.readouterr().err
    write_text(run_json, json.dumps({"eval_log": f"/root/work/logs/{STUDY}/x/x.eval"}))
    assert fleet.main(["run-args", "7"]) == 0                                # the box's own, pulled by the watch
    capsys.readouterr()
    monkeypatch.setattr(fleet_plan, "key_problems", lambda box: ["openai/x needs OPENAI_API_KEY"])
    assert fleet.main(["run-args", "7"]) == 1 and "OPENAI_API_KEY" in capsys.readouterr().err
    assert fleet.main(["run-args", "7", "--mock"]) == 0                      # the mock model needs no key


def test_run_args_refuse_a_plan_that_no_longer_holds_the_box_s_targets(registry, capsys):
    add_box(targets=["ian_gflipt_nobody_baseline"])
    assert fleet.main(["run-args", "7"]) == 1
    assert "no longer holds the box's targets" in capsys.readouterr().err

def test_labels_and_the_fleet_prefix():
    assert fleet_layout.box_label("spar-screen", BOX.name, LAUNCH) == LABEL
    with pytest.raises(ValueError):
        fleet_layout.box_label("spar-screen", BOX.name, "x")
    assert fleet_layout.label_base(LABEL) == f"spar-screen-{BOX.name}"
    assert fleet_layout.label_base(f"spar-screen-{BOX.name}") == f"spar-screen-{BOX.name}"
    assert fleet_layout.is_fleet_label(f"spar-screen-retry-{BOX.name}")
    assert not fleet_layout.is_fleet_label("spar-screens") and not fleet_layout.is_fleet_label(None)


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
        work / "runs" / "study_x" / "r1" / "processed/run.json": '{"a": 1}\n',
        work / "logs" / "r1" / "x.eval": "eval bytes",
        work / "runs" / "study_x" / "r1" / "analysis/figures" / "fig.png": "png",
        work / "src" / "__pycache__" / "x.pyc": "pyc",
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
    for rel in ("runs/study_x/r1/processed/run.json", "logs/r1/x.eval", "runs/study_x/r1/analysis/figures/fig.png"):
        (local / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(work / rel, local / rel)
    monkeypatch.setattr(box_capture, "REMOTE_REPO", str(work))
    monkeypatch.setattr(box_capture, "HOME", str(home))
    monkeypatch.setattr(box_capture, "TMP", str(tmp))
    monkeypatch.setattr(box_capture, "ROOT", local)
    monkeypatch.setattr(box_capture, "BOXES", local / "logs" / "boxes")
    monkeypatch.setattr(box_capture, "REGISTRY", local / "runs" / "boxes.json")
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
    assert listed == ["root/.bashrc", "root/screen.log", "root/work/logs/r1/x.eval",
                      "root/work/runs/study_x/r1/analysis/figures/fig.png",
                      "root/work/runs/study_x/r1/processed/run.json",
                      "root/work/src/edited.py", "root/work/src/new.py",
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
    write_text(box["local"] / "runs" / "study_x" / "r1" / "processed/run.json",
               '{"a": 2}\n')         # same size, other bytes
    report = compare(box, sweep(box))
    assert sorted(path.split("/r1/")[1] for path, _ in report.differ) == ["processed/run.json", "x.eval"] \
        and not report.ok


@pytest.mark.parametrize("change, words", [
    (lambda lines: [line for line in lines if "done" not in line], "cut short"),
    (lambda lines: [{"done": True, "roots": []}], "found no files"),
    (lambda lines: [{"producing": 12, "command": "python -m examples.protein_design.binder_solo"}, *lines],
     "still producing"),
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
    (box["work"] / "runs" / "study_x" / "r1" / "data").symlink_to(elsewhere)
    (box["work"] / "runs" / "study_x" / "r1" / "inside").symlink_to(box["work"] / "src")
    lines = sweep(box)
    assert [line["outside_link"] for line in lines if "outside_link" in line] == [
        str(box["work"] / "runs" / "study_x" / "r1" / "data")]
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
    proc = fake_proc(box, flags, box["work"] / "runs" / "study_x" / "r1" / "processed/run.json")
    producing = [line for line in sweep(box, proc=proc) if "producing" in line]
    assert len(producing) == (2 if counts else 0)                  # before and after the walk
    if counts:
        assert producing[0]["writing"] == [str(box["work"] / "runs" / "study_x" / "r1" / "processed/run.json")]


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
    assert fleet_destroy.destroy_refusals(7, entry, {"label": LABEL}, marker, time.time()) == []
    record = box_capture.snapshot(7)
    snapshot = Path(record["path"])
    assert record["files"] == 8 and not os.access(snapshot, os.W_OK)
    with tarfile.open(snapshot) as tar:
        member = str(box["work"] / "runs" / "study_x" / "r1" / "processed/run.json").lstrip("/")
        assert tar.extractfile(member).read() == b'{"a": 1}\n'
    write_text(box["local"] / "runs" / "study_x" / "r1" / "processed/run.json",
               '{"a": 9}\n')     # changed after the check
    with pytest.raises(RuntimeError, match="does not hold the manifest"):
        box_capture.snapshot(7)


def test_a_newer_local_copy_stays_and_the_boxs_bytes_go_beside_it(box, stand_in_ssh, capsys):
    assert box_capture.check(7, pull=True)
    local = box["local"] / "runs" / "study_x" / "r1" / "processed/run.json"
    local.write_text('{"a": 2, "rejudged": true}\n')
    os.utime(local, (time.time() + 600, time.time() + 600))                  # a local rewrite, newer than the box's
    (box["local"] / "runs" / "study_x" / "r1" / "stale.json").write_text("{}\n")
    marker = box_capture.check(7, pull=True)
    out = capsys.readouterr().out
    assert marker is not None and len(marker["conflicts"]) == 1 and marker["local_only"] == 1
    assert local.read_text() == '{"a": 2, "rejudged": true}\n'
    assert (box["folder"] / "conflicts" / "runs" / "study_x" / "r1" / "processed/run.json").read_text() == '{"a": 1}\n'
    assert "!! CONFLICT" in out and "LOCAL ONLY runs/study_x/r1/stale.json" in out


def a_plan_box(box) -> None:
    """Box 7 runs study_x's plan: its launcher's output and the plan its launcher rewrote, and one run of study_x."""
    box["local"].joinpath("runs").mkdir(parents=True, exist_ok=True)
    (box["local"] / "runs" / "boxes.json").write_text(json.dumps({"boxes": [
        {"id": 7, "study": "study_x", "plan": "studies/plans/study_x.yaml", "destroyed": None}]}))
    write_text(box["home"] / "launcher.log", "LAUNCH r2\n")
    write_text(box["work"] / "studies" / "plans" / "study_x.yaml", "study: study_x\n")
    write_text(box["work"] / "logs" / "study_x" / "r2" / "a.eval", "the box's eval")
    write_text(box["work"] / "runs" / "study_x" / "r2" / "processed/run.json",
               json.dumps({"eval_log": "/root/work/logs/study_x/r2/a.eval"}))


def test_the_launcher_s_output_and_plan_copy_go_under_the_study_s_box_folder(box, stand_in_ssh):
    a_plan_box(box)
    marker = box_capture.check(7, pull=True)
    place = box["local"] / "logs" / "study_x" / "boxes" / "7"
    assert marker is not None and marker["lost"] == 0
    assert (place / "launcher.log").read_text() == "LAUNCH r2\n"
    assert (place / "study_x.yaml").read_text() == "study: study_x\n"
    assert (box["local"] / "logs" / "study_x" / "r2" / "a.eval").read_text() == "the box's eval"
    assert not (box["folder"] / "files" / str(box["home"] / "launcher.log").lstrip("/")).exists()


def test_a_run_of_a_name_held_here_from_another_run_is_refused_before_any_pull(box, stand_in_ssh, capsys):
    a_plan_box(box)
    write_text(box["local"] / "logs" / "study_x" / "r2" / "b.eval", "the Mac's own eval")
    assert box_capture.check(7, pull=True) is None
    out = capsys.readouterr().out
    assert "logs/study_x/r2/b.eval here is not the box's: this machine holds another run of r2" in out
    assert "nothing pulled" in out and not (box["local"] / "logs" / "study_x" / "r2" / "a.eval").exists()
    (box["local"] / "logs" / "study_x" / "r2" / "b.eval").unlink()
    write_text(box["local"] / "runs" / "study_x" / "r2" / "processed/run.json",
               json.dumps({"eval_log": "/Users/someone/logs/study_x/r2/c.eval"}))
    assert box_capture.check(7, pull=True) is None
    assert "names the eval log c.eval, which the box does not hold" in capsys.readouterr().out


def test_an_eval_log_the_box_holds_with_other_bytes_is_another_run(box):
    lines = [{"file": f"{box_capture.REMOTE_REPO}/logs/study_x/r2/a.eval", "sha256": "0" * 64},
             {"file": f"{box_capture.REMOTE_REPO}/logs/r1/x.eval", "sha256": "1" * 64}]
    assert box_capture.box_evals(lines) == {("study_x", "r2"): {"a.eval": "0" * 64}}
    write_text(box["local"] / "logs" / "study_x" / "r2" / "a.eval", "other bytes")
    assert box_capture.foreign_runs(lines) == [
        "logs/study_x/r2/a.eval here is not the box's: this machine holds another run of r2"]


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


def test_local_only_lists_what_this_machine_added_to_a_run_of_a_study(tmp_path, monkeypatch):
    monkeypatch.setattr(box_capture, "ROOT", tmp_path)
    run, other = tmp_path / "runs/study_x/game_solo", tmp_path / "runs/study_x/game_minimal"
    for path in (run / "processed/run.json", run / "analysis/judge.jsonl", other / "processed/run.json",
                 run / "analysis/figures/a.png"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    lines = [{"file": f"{box_capture.REMOTE_REPO}/{rel}"}
             for rel in ("runs/study_x/game_solo/processed/run.json", "runs/study_x/game_solo/analysis/figures/b.png")]
    assert box_capture.local_only(lines) == ["runs/study_x/game_solo/analysis/figures/a.png",
                                             "runs/study_x/game_solo/analysis/judge.jsonl"]


def test_local_only_reads_a_runs_figures_and_an_attempts_own_folder(tmp_path, monkeypatch):
    """A run's figures sit in its own folder, and attempts/<name>.<n> is the attempt's own folder: the files another run
    of the study keeps here are not this box's run's."""
    monkeypatch.setattr(box_capture, "ROOT", tmp_path)
    local = ("runs/study_x/game_solo/processed/run.json", "runs/study_x/game_solo/analysis/figures/a.png",
             "runs/study_x/game_minimal/analysis/figures/c.png", "runs/study_x/attempts/game_solo.1/processed/run.json",
             "runs/study_x/attempts/game_solo.1/analysis/judge.jsonl",
             "runs/study_x/attempts/game_minimal.1/processed/run.json")
    for rel in local:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x")
    lines = [{"file": f"{box_capture.REMOTE_REPO}/{rel}"}
             for rel in ("runs/study_x/game_solo/processed/run.json", "runs/study_x/game_solo/analysis/figures/b.png",
                         "runs/study_x/attempts/game_solo.1/processed/run.json")]
    assert box_capture.local_only(lines) == ["runs/study_x/attempts/game_solo.1/analysis/judge.jsonl",
                                             "runs/study_x/game_solo/analysis/figures/a.png"]


# ---- the watch ----

@pytest.mark.parametrize("every", ["abc", "0", ""])
def test_the_watch_refuses_an_interval_that_is_no_whole_minute(every):
    result = subprocess.run(["bash", str(Path(box_capture.SWEEP).with_name("watch.sh")), "--once", "--every", every],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 2 and "whole minutes" in result.stdout


DOCKER = {"running": [], "containers": ["c1", "c2"], "volumes": ["v1"]}


@pytest.mark.parametrize("later", ["41 minutes ago", "About an hour ago", "2 hours ago", "Less than a second ago"])
def test_docker_drift_ignores_a_stopped_container_s_age_and_nothing_else(later):
    state = {"running": [], "containers": ["inspect-a-1 Exited (143) 40 minutes ago"], "volumes": []}
    assert fleet_destroy.docker_drift({"state": state}, {"state": {
        **state, "containers": [f"inspect-a-1 Exited (143) {later}"]}}) is None
    for other in (f"inspect-b-1 Exited (143) {later}", f"inspect-a-1 Exited (0) {later}"):
        assert "containers differ" in fleet_destroy.docker_drift({"state": state}, {"state": {
            **state, "containers": [other]}})


def test_docker_drift_names_a_running_container_and_any_other_set():
    accepted = {"reason": "saved", "state": DOCKER}
    assert fleet_destroy.docker_drift(accepted, {"state": dict(DOCKER)}) is None
    assert "run now" in fleet_destroy.docker_drift(accepted, {"state": {**DOCKER, "running": ["c1"]}})
    assert "containers differ" in fleet_destroy.docker_drift(accepted, {"state": {**DOCKER, "containers": ["c1"]}})
    assert "volumes differ" in fleet_destroy.docker_drift(accepted, {"state": {**DOCKER, "volumes": []}})


@pytest.mark.parametrize("now, code, words", [
    (dict(DOCKER), 0, "go with the box: saved"),
    ({**DOCKER, "running": ["c1"]}, 1, "refused: 1 containers run now"),
])
def test_a_destroy_carries_the_accepted_docker_reason_into_its_second_check(registry, monkeypatch, capsys,
                                                                            now, code, words):
    marker = fresh_marker(epoch=time.time(), time="now", captured=12, excluded={},
                          docker_accepted={"reason": "saved", "state": DOCKER})
    seen = []

    def check(instance_id, pull, accept_docker=None):
        seen.append(accept_docker)
        return dict(marker, docker_accepted={"reason": accept_docker, "state": now})

    monkeypatch.setattr(box_capture, "read_marker", lambda instance_id: marker)
    monkeypatch.setattr(box_capture, "check", check)
    monkeypatch.setattr(box_capture, "snapshot", lambda instance_id: {"path": "snapshot.tar.gz", "files": 12})
    monkeypatch.setattr(fleet_destroy, "DESTROY_WAIT_S", 0)
    fake = FakeVast(instances=[{"id": 7, "label": LABEL, "start_date": time.time() - 3600, "dph_total": 0.5}])
    monkeypatch.setattr(vast_api, "http", fake)
    add_box()
    assert fleet.main(["destroy", "7"]) == code
    assert seen == ["saved"] and words in capsys.readouterr().out
    assert any(call.method == "DELETE" for call in fake.calls) == (code == 0)
