"""The vast.ai fleet: find offers, rent one box per (task, model), list the boxes, destroy one once its capture holds.

    uv run python -m scripts.box.fleet offers [--top 20] [--max-dph 1.0] [--saved search.json]
    uv run python -m scripts.box.fleet create --count 15 [--tasks ...] [--models ...] [--label-prefix spar-screen]
                                              [--max-dph 1.0] [--max-fleet-dph 15] [--commit SHA] [--dry-run]
    uv run python -m scripts.box.fleet status
    uv run python -m scripts.box.fleet ids [--dph] [<id> ...]
    uv run python -m scripts.box.fleet entry <id> <field> ...
    uv run python -m scripts.box.fleet repin <id> ... --commit SHA
    uv run python -m scripts.box.fleet run-args <id> [--suffix S] [--samples N] [--kinds KIND ...] [--mock]
    uv run python -m scripts.box.fleet destroy <id> [--dry-run] [--accept-loss REASON]

offers searches the on-demand offers (POST /api/v0/bundles/, the request `vastai search offers` sends) and keeps the
ones that can hold a box, cheapest first: x86_64, a host that runs VMs (the box is the kvm image as a VM, with Docker
inside), at least 16 cores and 32 GB of RAM in the share the offer rents (create holds each box to its task's floor,
fleet_layout.floor_of), 200 GB of disk, reliability 0.98, an open port for direct ssh, and a host outside the
countries the model providers do not serve. vast.ai gives an instance the offer's share of the machine's cores
(cpu_cores_effective); the RAM share is assumed to follow the cores (cpu_ram is the machine's), and box_setup.sh
checks the RAM each box really has.

create refuses a task without every cell or with a cell stem another task has, a count that does not match the boxes
asked for, and a (task, model) the account holds a box of already. It pins the launch before the first rent: HEAD (or
--commit, when others commit to this checkout), which origin/main must hold, the .venv's package list, the Python and uv
versions, in logs/boxes/launch-<launch>/, so every box of the launch runs the same code and packages whatever this
checkout does later. It rents one offer per box in the same step as the search, since offer ids expire within minutes:
PUT /api/v0/asks/<offer>/ with the kvm image as a VM, 200 GB and direct ssh, labelled
spar-screen-<task>-<model>-<launch>. It takes no offer above --max-dph and stops before the fleet's $/h (the registered
boxes not destroyed, and this launch) would pass --max-fleet-dph. Each instance goes into the registry runs/boxes.json
the moment vast.ai returns its id. A rejected offer moves on to the next one; any other outcome (an error, a reply it
cannot read, an interrupt) stops the launch, since the instance may exist. However the launch ends, once a rent was sent
an audit of the account runs: an instance with this launch's label that the registry lacks is registered then, and any
other spar-screen instance outside the registry fails the launch and stays unregistered. --dry-run does the search and
prints every request it would send, and sends none: its client refuses anything but a read.

status lists the account's instances (GET /api/v1/instances/, every page) joined with the registry, records each box's
ssh endpoint from that listing (GET /api/v0/instances/<id>/ only for a row without one) and writes
logs/boxes/<id>/ssh_config, which box_ssh.sh, box_scp.sh and rsync use. It fails on a registered box the account lacks,
a box marked destroyed or sent a destroy that the account still lists, a box vast.ai cannot start, and a spar-screen
instance outside the registry, and prints what the registered boxes bill (a stopped box its disk alone). A box that
cannot start stays on the account, billing its disk, and its error goes into the registry, so create never rents that
machine again; its replacement takes --label-prefix spar-screen-retry. ids prints the registered boxes not yet destroyed
(with --dph, each one's $/h and the total). entry prints fields of a registered box for the shell scripts: its registry
record and its launch's pins. run-args prints box_runner.py run's arguments for a box: the task, the model and the
commit from the registry, and the registry's suffix or one that starts with it; it refuses a run name this machine holds
from a run made elsewhere, which the watch would overwrite. --mock asks for the mock model instead of the box's, with a
suffix that says so (<suffix>mock by default). repin moves boxes to a commit origin/main holds, in the registry: the
launch's package list must be the .venv's, and box_setup.sh run again checks the commit out and rebuilds what changed.

destroy refuses unless the id is in the registry, vast.ai labels it as the registry does (spar-screen-*),
capture_check.sh passed for it within the last 10 minutes, and a capture check run again now passes. It prints what
the capture leaves behind, makes a read-only snapshot of the captured files and reads it back against the manifest,
records the destroy as sent, destroys, and marks the box destroyed only once the account no longer lists it. It
appends the entry for VERIFICATION_LOG.md (id, files swept, captured and lost, the snapshot, cost) to
logs/boxes/destroyed.jsonl. --accept-loss REASON is for a box that can never pass the capture (unreachable, its disk
gone): it keeps the registry and label gates, runs the check once to record what is lost when the box answers, and
logs the reason with the destroy.

The API key is read from ~/.config/vastai/vast_api_key and goes only into the Authorization header, which follows a
redirect only to the API's own scheme and host. Nothing prints it, nor the instance key a create returns, nor the
account record beyond its credit.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from scripts.box import box_capture
from scripts.box.fleet_layout import (
    BOXES,
    EVERY_KIND,
    LABEL_PREFIX,
    LIGHT_FLOOR,
    MODELS,
    REGISTRY,
    REMOTE_REPO,
    ROOT,
    TASKS,
    box_label,
    boxes,
    cells,
    floor_of,
    is_fleet_label,
    kinds_of,
    label_base,
    layout_problems,
    new_launch,
    run_name,
)
from src.configuration import settings

API = "https://console.vast.ai"
KEY_FILE = Path.home() / ".config" / "vastai" / "vast_api_key"
SSH_KEY = "~/.ssh/id_ed25519"
TIMEOUT_S = 60
READ_BACKOFF_S = (2, 5, 10, 20, 30)
"""The waits before a read vast.ai refused with 429 goes again: 15 box_setup.sh calling status at once hit the limit on
2026-10-30, and a status that dies part way writes no ssh_config for the boxes after that point."""
MOCK_MODEL = "mockllm/model"
"""What a rehearsal runs: Inspect's mock model, no key and no spend."""

IMAGE = "docker.io/vastai/kvm:ubuntu_terminal"
DISK_GB = 200
MIN_CORES, MIN_RAM_GB = LIGHT_FLOOR
"""The least a box of any task needs; a task's own floor is fleet_layout.floor_of."""
MIN_RELIABILITY = 0.98
MAX_DPH = 1.00
"""The dearest offer, in $/h with the disk, that offers and create take unless --max-dph says otherwise."""
MAX_FLEET_DPH = 15.00
"""What the fleet may bill per hour in all (its boxes not destroyed), unless --max-fleet-dph says otherwise."""
BLOCKED_COUNTRIES = {"CN", "HK", "MO", "RU", "BY", "IR", "KP", "SY", "CU"}
"""Where OpenAI or Anthropic do not serve their API, or where the box could not reach it."""
MAX_REJECTIONS = 10
"""Offers vast.ai may refuse in one launch (taken since the search) before the launch stops."""
CAPTURE_FRESH_S = 600
DESTROY_WAIT_S = 180
SEARCH_PATH = "/api/v0/bundles/"
NOT_SWEPT = ("Docker's images, and its containers and volumes unless the capture saved them (/var/lib/docker)",
             "/var", "/home", "/opt", "/etc", "/usr", "/srv", "/mnt", "every other path outside /root/work, /root "
             "and /tmp")
"""What the capture never sweeps, printed before every destroy."""
ENTRY_FIELDS = ("task", "model", "suffix", "label", "launch", "commit", "requirements", "requirements_sha256",
                "python_version", "uv_version", "cores", "ram_gb", "dph", "min_cores", "min_ram_gb", "min_disk_gb")


def minimums(task: str) -> dict[str, int]:
    """What box_setup.sh requires a box of the task to have (nproc, MemTotal, the root filesystem; GB of 10^9 bytes):
    the offer's floor, less what a VM's kernel and filesystem take. A VM on a 32-core share has 30 cores (instance
    58388038)."""
    cores, ram_gb = floor_of(task)
    return {"min_cores": cores - 2, "min_ram_gb": int(0.9 * ram_gb), "min_disk_gb": int(0.85 * DISK_GB)}


@dataclass(frozen=True)
class Call:
    """One request to the vast.ai API."""

    method: str
    path: str
    query: dict = field(default_factory=dict)
    body: dict | None = None

    @property
    def url(self) -> str:
        return API + self.path + ("?" + urlencode(self.query) if self.query else "")

    @property
    def reads(self) -> bool:
        """A GET, or the offer search: a POST that changes nothing."""
        return self.method == "GET" or (self.method == "POST" and self.path == SEARCH_PATH)

    def show(self) -> str:
        return f"{self.method} {self.url}" + ("" if self.body is None else " " + json.dumps(self.body, sort_keys=True))


def build_request(call: Call, key: str) -> urllib.request.Request:
    """The request for a call, the key in an unredirected header: urllib copies a request's other headers to a
    redirect's target, never these."""
    request = urllib.request.Request(call.url, method=call.method,
                                     data=None if call.body is None else json.dumps(call.body).encode(),
                                     headers={"Accept": "application/json", "Content-Type": "application/json"})
    request.add_unredirected_header("Authorization", f"Bearer {key}")
    return request


def origin(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return parts.scheme, parts.netloc


class KeyStaysHome(urllib.request.HTTPRedirectHandler):
    """Follows a redirect as urllib does, and carries the key along only to the API's own scheme and host (vast.ai
    sends /api/v0/users/current to /api/v0/users/current/); anywhere else the key stays behind."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        key = req.unredirected_hdrs.get("Authorization")
        if new is not None and key and origin(newurl) == origin(API) == origin(req.full_url):
            new.add_unredirected_header("Authorization", key)
        return new


def http(call: Call) -> dict:
    """Send one call with the account's key; the reply's JSON."""
    request = build_request(call, KEY_FILE.read_text().strip())
    with urllib.request.build_opener(KeyStaysHome).open(request, timeout=TIMEOUT_S) as reply:
        return json.loads(reply.read() or b"{}")


class Vast:
    """The API, through a transport (http, or a fake in the tests). A client built without writes refuses every call
    but a read, so a dry run cannot rent or destroy anything."""

    def __init__(self, writes: bool, transport: Callable[[Call], dict] | None = None):
        self.writes = writes
        self.transport = transport or http

    def send(self, call: Call) -> dict:
        if not (self.writes or call.reads):
            raise PermissionError(f"refused {call.method} {call.path}: this client only reads")
        for wait in (*READ_BACKOFF_S, None):
            try:
                return self.transport(call)
            except urllib.error.HTTPError as error:
                if error.code != 429 or not call.reads or wait is None:
                    raise
                time.sleep(wait)


# ---- offers ----

def search_call(limit: int = 1000) -> Call:
    """The search `vastai search offers` sends, narrowed on the server to what the box needs (the RAM share and the
    country are checked here), on-demand prices with the box's disk included, cheapest first."""
    query = {"verified": {"eq": True}, "external": {"eq": False}, "rentable": {"eq": True},
             "cpu_arch": {"eq": "amd64"}, "vms_enabled": {"eq": True}, "cpu_cores_effective": {"gte": MIN_CORES},
             "cpu_ram": {"gte": MIN_RAM_GB * 1000}, "disk_space": {"gte": DISK_GB},
             "reliability": {"gte": MIN_RELIABILITY}, "direct_port_count": {"gte": 1}}
    return Call("POST", SEARCH_PATH, body={**query, "order": [["dph_total", "asc"]], "type": "on-demand",
                                           "allocated_storage": DISK_GB, "limit": limit})


def ram_share_gb(offer: dict) -> float:
    """The RAM an instance of the offer gets, assumed: the machine's RAM (cpu_ram, MB) times the offer's share of its
    cores."""
    cores = offer.get("cpu_cores") or 0
    share = (offer.get("cpu_cores_effective") or 0) / cores if cores else 0.0
    return (offer.get("cpu_ram") or 0) / 1000 * share


def country(offer: dict) -> str | None:
    """The two-letter code that ends geolocation ("Quebec, CA" -> "CA")."""
    location = offer.get("geolocation") or ""
    code = location.rsplit(",", 1)[-1].strip()
    return code if len(code) == 2 else None


def rejections(offer: dict, max_dph: float = MAX_DPH, floor: tuple[int, int] = LIGHT_FLOOR) -> list[str]:
    """Why an offer cannot hold a box of that floor (cores, GB of RAM); empty when it can."""
    min_cores, min_ram_gb = floor
    checks = [
        (offer.get("cpu_arch") == "amd64", "not x86_64"),
        (offer.get("vms_enabled") is True, "no VM support"),
        (not offer.get("is_vm_deverified"), "VM support deverified"),
        ((offer.get("cpu_cores_effective") or 0) >= min_cores, f"fewer than {min_cores} cores"),
        (ram_share_gb(offer) >= min_ram_gb, f"less than {min_ram_gb} GB of RAM in its share"),
        ((offer.get("disk_space") or 0) >= DISK_GB, f"less than {DISK_GB} GB of disk"),
        ((offer.get("reliability") or 0) >= MIN_RELIABILITY, f"reliability below {MIN_RELIABILITY}"),
        ((offer.get("direct_port_count") or 0) >= 1, "no open port for direct ssh"),
        (offer.get("rentable") is not False and not offer.get("rented"), "not rentable"),
        (country(offer) is not None, "no country"),
        (country(offer) not in BLOCKED_COUNTRIES, "a country the model providers do not serve"),
        (offer.get("dph_total") is not None, "no price"),
        ((offer.get("dph_total") or 0) <= max_dph, f"dearer than ${max_dph:.2f}/h"),
    ]
    return [reason for ok, reason in checks if not ok]


def candidates(offers: list[dict], max_dph: float = MAX_DPH) -> list[dict]:
    """The offers that can hold a box, cheapest first, then the most reliable, then by id."""
    return sorted((o for o in offers if not rejections(o, max_dph)),
                  key=lambda o: (o["dph_total"], -o["reliability"], o["id"]))


def offer_row(offer: dict) -> str:
    return (f"{offer['id']:>9} {offer.get('machine_id', ''):>7} {offer['dph_total']:7.3f} "
            f"{offer.get('cpu_cores_effective', 0):5.0f}/{offer.get('cpu_cores', 0):<4} "
            f"{ram_share_gb(offer):6.0f}/{(offer.get('cpu_ram') or 0) / 1000:<5.0f} {offer.get('disk_space', 0):6.0f} "
            f"{offer.get('reliability', 0):6.4f} {'yes' if offer.get('vms_enabled') else 'no':>3} "
            f"{offer.get('inet_down', 0):7.0f}  {offer.get('geolocation') or '-'}")


OFFER_HEADER = (f"{'offer':>9} {'machine':>7} {'$/h':>7} {'cores':>10} {'RAM GB':>12} {'disk':>6} {'reliab':>6} "
                f"{'vm':>3} {'down':>7}  location   (cores and RAM: the offer's share/the machine's; Docker runs "
                f"inside the VM)")


# ---- the registry and the launches ----

@contextlib.contextmanager
def registry_lock() -> Iterator[None]:
    """The registry's lock, held by every read-modify-write of it."""
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    with open(REGISTRY.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def load_registry() -> dict:
    return json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {"boxes": []}


def save_registry(registry: dict) -> None:
    """Write through a temporary file, so a crash never leaves half a registry."""
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    temporary = REGISTRY.with_suffix(".tmp")
    temporary.write_text(json.dumps(registry, indent=1) + "\n")
    os.replace(temporary, REGISTRY)


def register(entry: dict, exist_ok: bool = False) -> bool:
    """Add one instance to the registry at once, under the lock; False when it is there already and exist_ok."""
    with registry_lock():
        registry = load_registry()
        if any(box["id"] == entry["id"] for box in registry["boxes"]):
            if exist_ok:
                return False
            raise ValueError(f"instance {entry['id']} is registered already")
        registry["boxes"].append(entry)
        save_registry(registry)
        return True


def update_box(instance_id: int, **fields) -> None:
    """Set fields of one registered box, under the lock."""
    with registry_lock():
        registry = load_registry()
        registered(registry, instance_id).update(fields)
        save_registry(registry)


def registered(registry: dict, instance_id: int) -> dict | None:
    return next((box for box in registry["boxes"] if box["id"] == instance_id), None)


def box_entry(instance_id: int, label: str, task: str, model: str, offer: dict | None, launch: str) -> dict:
    """The registry's record of one instance. Only what identifies and prices the box: never the reply's keys."""
    offer = offer or {}
    return {"id": instance_id, "label": label, "task": task, "model": model, "suffix": MODELS[model],
            "launch": launch, "offer": offer.get("id"), "machine_id": offer.get("machine_id"),
            "dph": offer.get("dph_total"), "cores": offer.get("cpu_cores_effective"),
            "ram_gb": round(ram_share_gb(offer), 1) if offer else None, "disk_gb": DISK_GB,
            "reliability": offer.get("reliability"), "geolocation": offer.get("geolocation"),
            "created": now_iso(), "ssh": None, "destroy_sent": None, "destroyed": None}


def open_dph(registry: dict) -> float:
    """What the registered boxes not destroyed bill per hour, by their offers."""
    return sum(box["dph"] or 0 for box in registry["boxes"] if not box["destroyed"])


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def launch_dir(launch: str) -> Path:
    return BOXES / f"launch-{launch}"


def fleet_pin(commit: str = "HEAD") -> dict:
    """What every box of a launch runs: the commit, which origin/main must hold since the boxes clone it from GitHub,
    the .venv's package list (uv pip freeze, editable installs left out), and the Python and uv versions."""
    def run(*command: str) -> subprocess.CompletedProcess:
        return subprocess.run(command, cwd=ROOT, capture_output=True)

    if run("git", "fetch", "--quiet", "origin").returncode:
        raise RuntimeError("git fetch origin failed")
    commit = run("git", "rev-parse", commit).stdout.decode().strip()
    if run("git", "merge-base", "--is-ancestor", commit, "origin/main").returncode:
        raise RuntimeError(f"{commit} is not on origin/main: push it first, since every box clones it")
    freeze = run("uv", "pip", "freeze", "--python", ".venv", "--exclude-editable")
    if freeze.returncode or not re.search(rb"^inspect-ai==", freeze.stdout, re.M):
        raise RuntimeError(f"the .venv's package list could not be read or lacks inspect-ai: {freeze.stderr[-300:]}")
    python = run(str(ROOT / ".venv" / "bin" / "python"), "-c", "import platform; print(platform.python_version())")
    uv = run("uv", "--version").stdout.decode().split()
    if python.returncode or len(uv) < 2:
        raise RuntimeError("the .venv's Python or uv's version could not be read")
    return {"commit": commit, "requirements": freeze.stdout, "requirements_sha256": sha256_bytes(freeze.stdout),
            "python_version": python.stdout.decode().strip(), "uv_version": uv[1]}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_launch(launch: str, pin: dict) -> Path:
    folder = launch_dir(launch)
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "requirements.txt").write_bytes(pin["requirements"])
    pins = {key: value for key, value in pin.items() if key != "requirements"} | {"launch": launch,
                                                                                  "created": now_iso()}
    (folder / "launch.json").write_text(json.dumps(pins, indent=1) + "\n")
    return folder


def load_launch(launch: str) -> dict:
    """A launch's pins, with the path of its package list, which must still hash to what the launch recorded."""
    folder = launch_dir(launch)
    pins = json.loads((folder / "launch.json").read_text())
    requirements = folder / "requirements.txt"
    if sha256_bytes(requirements.read_bytes()) != pins["requirements_sha256"]:
        raise RuntimeError(f"{requirements} is not the package list launch {launch} pinned")
    return pins | {"requirements": str(requirements)}


# ---- the account ----

def list_instances(vast: Vast) -> list[dict]:
    """Every instance of the account: every page of GET /api/v1/instances/, checked against the total it reports."""
    rows, token = [], None
    while True:
        query = {"select_filters": "{}", "order_by": json.dumps([{"col": "id", "dir": "asc"}]), "limit": 25}
        reply = vast.send(Call("GET", "/api/v1/instances/", {**query, **({"after_token": token} if token else {})}))
        rows += reply.get("instances") or []
        token = reply.get("next_token")
        if not token:
            break
    if reply.get("total_instances") is not None and reply["total_instances"] != len(rows):
        raise RuntimeError(f"the account reports {reply['total_instances']} instances, the pages held {len(rows)}")
    return rows


def instance(vast: Vast, instance_id: int) -> dict | None:
    """One instance's full record (GET /api/v0/instances/<id>/), None when the account has no such instance."""
    try:
        reply = vast.send(Call("GET", f"/api/v0/instances/{instance_id}/", {"owner": "me"}))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise
    return reply.get("instances") or None


def credit(vast: Vast) -> float | None:
    """The account's credit; the rest of the account record is never kept."""
    return vast.send(Call("GET", "/api/v0/users/current/")).get("credit")


def start_error(row: dict) -> str | None:
    """Why vast.ai cannot start an instance, from its status message; None when it does not say so. A failed start
    says "Error: ...", and a start tried again after it "retries exceeded"."""
    message = (row.get("status_msg") or "").strip()
    return message if message.startswith("Error") or "retries exceeded" in message else None


def ssh_endpoint(record: dict) -> dict | None:
    """Direct ssh (the public address and the port mapped to 22) and vast.ai's proxy, from an instance record."""
    mapped = (record.get("ports") or {}).get("22/tcp") or []
    direct = {"host": record.get("public_ipaddr"), "port": int(mapped[0]["HostPort"])} if mapped else None
    proxy = {"host": record["ssh_host"], "port": int(record["ssh_port"])} if record.get("ssh_host") else None
    return {"direct": direct, "proxy": proxy} if direct or proxy else None


def write_ssh_config(instance_id: int, ssh: dict) -> Path:
    """logs/boxes/<id>/ssh_config: host `box` at the direct endpoint (the proxy when there is none), root with the
    account's key, its own known_hosts, no prompts. ssh -F <it> box, scp and rsync -e 'ssh -F <it>' then need no
    options on their command lines."""
    folder = BOXES / str(instance_id)
    folder.mkdir(parents=True, exist_ok=True)
    target = ssh["direct"] or ssh["proxy"]
    config = folder / "ssh_config"
    text = "\n".join([
        "Host box", f"  HostName {target['host']}", f"  Port {target['port']}", "  User root",
        f"  IdentityFile {SSH_KEY}", "  IdentitiesOnly yes", f'  UserKnownHostsFile "{folder / "known_hosts"}"',
        "  StrictHostKeyChecking accept-new", "  ServerAliveInterval 30", "  ServerAliveCountMax 6",
        "  ConnectTimeout 20", "  BatchMode yes", "  LogLevel ERROR", ""])
    if not config.exists() or config.read_text() != text:   # replaced whole: an ssh reading it never sees half a file
        temporary = folder / f"ssh_config.{os.getpid()}"
        temporary.write_text(text)
        os.replace(temporary, config)
    return config


# ---- create ----

def create_call(offer_id: int, label: str) -> Call:
    """The rent request: the kvm image as a VM, its disk, direct ssh and the box's label."""
    return Call("PUT", f"/api/v0/asks/{offer_id}/",
                body={"image": IMAGE, "disk": DISK_GB, "runtype": "ssh_direct", "vm": True, "label": label})


def try_create(vast: Vast, call: Call) -> tuple[str, int | None, str]:
    """("created", id, ""), ("rejected", None, why) when vast.ai refused the offer and made nothing, or ("unknown",
    None, why) when the instance may exist: any other error, or a reply without an id."""
    try:
        reply = vast.send(call)
    except urllib.error.HTTPError as error:
        try:
            text = error.read().decode(errors="replace")[:300]
        except Exception as unread:  # noqa: BLE001 - the status alone decides
            text = f"(the body could not be read: {type(unread).__name__})"
        if 400 <= error.code < 500 and error.code not in (401, 403, 408, 429):
            return "rejected", None, f"HTTP {error.code}: {text}"
        return "unknown", None, f"HTTP {error.code}: {text}"
    except Exception as error:  # noqa: BLE001 - a broken read, a reply that is not JSON: the instance may exist
        return "unknown", None, f"{type(error).__name__}: {error}"
    if not isinstance(reply, dict):
        return "unknown", None, f"a reply that is not an object ({type(reply).__name__})"
    if reply.get("success") and reply.get("new_contract"):
        return "created", int(reply["new_contract"]), ""
    if reply.get("success") is False:
        return "rejected", None, str(reply.get("msg") or reply.get("error"))[:300]
    return "unknown", None, f"a reply without an instance id (keys {sorted(reply)})"


def audit(vast: Vast, asked: dict[str, tuple[str, str]]) -> int:
    """After a launch: register any instance with one of this launch's labels that the registry lacks, then fail on
    any other spar-screen instance outside the registry, which stays unregistered (another launch's, or a stranger)."""
    live = list_instances(vast)
    known = {box["id"] for box in load_registry()["boxes"]}
    failures = 0
    for row in live:
        if row["id"] in known or not is_fleet_label(row.get("label")):
            continue
        if row["label"] in asked:
            task, model = asked[row["label"]]
            launch = row["label"].rsplit("-", 1)[1]
            entry = box_entry(row["id"], row["label"], task, model, None, launch)
            if register(entry | {"dph": row.get("dph_total"), "note": "registered by the audit"}, exist_ok=True):
                print(f"   registered {row['id']} {row['label']}: the launch asked for it and did not hear back")
        else:
            print(f"!! UNREGISTERED {row['id']} {row['label']}: a spar-screen instance outside the registry, not of "
                  f"this launch")
            failures += 1
    mine = [box for box in load_registry()["boxes"] if box["label"] in asked and not box["destroyed"]]
    print(f"== audit: {len(live)} instances on the account, {len(mine)} of this launch registered")
    for box in mine:
        print(f"   {box['id']} {box['label']} offer {box['offer']} ${box['dph'] or 0:.3f}/h")
    return 1 if failures else 0


def rent(vast: Vast, asked: dict[str, tuple[str, str]], offers: list[dict], args: argparse.Namespace, launch: str,
         held: float, sent: list[int]) -> int:
    """One offer per label, the cheapest that meets its task's floor, never past the fleet's cap; sent[0] counts the
    rents sent."""
    remaining, rejected, spent, placed = list(offers), 0, 0.0, []
    for label, (task, model) in asked.items():
        over_cap = False
        for offer in [o for o in remaining if not rejections(o, args.max_dph, floor_of(task))]:
            if held + spent + offer["dph_total"] > args.max_fleet_dph:
                over_cap = True
                print(f"== stopped: offer {offer['id']} at ${offer['dph_total']:.3f}/h takes the fleet to "
                      f"${held + spent + offer['dph_total']:.3f}/h, past its cap ${args.max_fleet_dph:.2f}/h")
                break
            remaining.remove(offer)
            call = create_call(offer["id"], label)
            if args.dry_run:
                print(f"   would send: {call.show()}\n      offer {offer_row(offer)}")
                placed.append(label)
                spent += offer["dph_total"]
                break
            sent[0] += 1
            outcome, instance_id, why = try_create(vast, call)
            if outcome == "created":
                register(box_entry(instance_id, label, task, model, offer, launch))
                print(f"   created {instance_id} {label} on offer {offer['id']} ${offer['dph_total']:.3f}/h")
                placed.append(label)
                spent += offer["dph_total"]
                break
            print(f"   {outcome}: offer {offer['id']} for {label}: {why}")
            if outcome == "unknown":
                return 1
            rejected += 1
            if rejected > MAX_REJECTIONS:
                print(f"== stopped: {rejected} offers refused")
                return 1
        if label not in placed and (over_cap or not args.dry_run):
            break
    unfilled = [label for label in asked if label not in placed]
    if unfilled:
        print(f"== no offer left for: {', '.join(unfilled)}")
    if args.dry_run:
        print(f"== dry run: {len(placed)} of {len(asked)} requests printed, ${spent:.3f}/h, none sent")
    return 1 if unfilled else 0


def cmd_create(args: argparse.Namespace) -> int:
    pairs = sorted(boxes(args.tasks, args.models), key=lambda pair: floor_of(pair[0]), reverse=True)    # heavy first
    if len(pairs) != args.count:
        print(f"--count {args.count} but the tasks and models make {len(pairs)} boxes: say the count you mean")
        return 2
    if not args.label_prefix.startswith(LABEL_PREFIX):
        print(f"--label-prefix must start with {LABEL_PREFIX}: destroy acts on nothing else")
        return 2
    refused = False
    for problem in layout_problems(args.tasks):
        print(f"!! {problem}")
        refused = True
    if refused and not args.dry_run:
        print("refused: a box rented for these tasks could not run them")
        return 1
    vast = Vast(writes=not args.dry_run)
    launch = new_launch()
    asked = {box_label(args.label_prefix, task, model, launch): (task, model) for task, model in pairs}
    bases = {label_base(label) for label in asked}
    clash = [row for row in list_instances(vast) if label_base(row.get("label") or "") in bases]
    if clash:
        print("refused: the account holds boxes of these (task, model) already: "
              + ", ".join(f"{row['id']} {row['label']}" for row in clash))
        return 1
    try:
        pin = fleet_pin(args.commit)
    except (RuntimeError, OSError) as error:
        print(f"refused: {error}")
        return 1
    print(f"== launch {launch}: commit {pin['commit']}, package list sha256 {pin['requirements_sha256'][:16]}, "
          f"Python {pin['python_version']}, uv {pin['uv_version']}")
    print(f"== credit ${credit(vast) or 0:.2f}; {args.count} boxes; search: {search_call().show()}")
    registry = load_registry()
    dead = {box["machine_id"] for box in registry["boxes"] if box.get("start_error")}
    offers = [offer for offer in candidates(vast.send(search_call())["offers"], args.max_dph)
              if offer.get("machine_id") not in dead]
    held = open_dph(registry)
    planned = sum(offer["dph_total"] for offer in offers[:args.count])
    print(f"== {len(offers)} offers can hold a box at ${args.max_dph:.2f}/h or less; the cheapest "
          f"{min(args.count, len(offers))} bill ${planned:.3f}/h, the fleet with them ${held + planned:.3f}/h "
          f"(cap ${args.max_fleet_dph:.2f}/h)")
    if len(offers) < args.count:
        print(f"{'refused' if not args.dry_run else '!!'}: {len(offers)} offers for {args.count} boxes")
        refused = True
    if held + planned > args.max_fleet_dph:
        print(f"{'refused' if not args.dry_run else '!!'}: the fleet would bill ${held + planned:.3f}/h, past its cap "
              f"${args.max_fleet_dph:.2f}/h")
        refused = True
    if refused and not args.dry_run:
        return 1
    if not args.dry_run:
        print(f"== pinned in {write_launch(launch, pin)}")
    sent, audit_code = [0], 0
    try:
        code = rent(vast, asked, offers, args, launch, held, sent)
    finally:
        if sent[0]:
            audit_code = audit(vast, asked)
    if args.dry_run:
        return 1 if code or refused else 0
    return code or audit_code


# ---- status ----

def cmd_status(args: argparse.Namespace) -> int:
    vast = Vast(writes=False)
    live = {row["id"]: row for row in list_instances(vast)}
    failures, billing = 0, 0.0
    with registry_lock():
        registry = load_registry()
        print(f"== {len(live)} instances on the account; "
              f"{sum(not box['destroyed'] for box in registry['boxes'])} boxes in the registry not destroyed")
        for box in registry["boxes"]:
            row = live.get(box["id"])
            if box["destroyed"]:
                if row is not None:
                    print(f"!! {box['id']} {box['label']}: marked destroyed at {box['destroyed']}, still on the "
                          f"account ({row.get('actual_status')}, ${row.get('dph_total') or 0:.3f}/h)")
                    failures += 1
                continue
            if row is None:
                if box.get("destroy_sent"):
                    box["destroyed"] = now_iso()
                    print(f"   {box['id']} {box['label']}: gone from the account since the destroy sent at "
                          f"{box['destroy_sent']}; marked destroyed")
                else:
                    print(f"!! {box['id']} {box['label']}: registered, not on the account")
                    failures += 1
                continue
            if box.get("destroy_sent"):
                print(f"!! {box['id']} {box['label']}: destroy sent at {box['destroy_sent']}, still on the account")
                failures += 1
            why = start_error(row)
            if why:
                box["start_error"] = why
                print(f"!! {box['id']} {box['label']}: vast.ai cannot start it ({why}); rent another with "
                      f"--label-prefix {LABEL_PREFIX}-retry, and this one bills its disk until it goes")
                failures += 1
            rate = (row.get("storage_total_cost") if row.get("cur_state") == "stopped" else row.get("dph_total")) or 0
            billing += rate
            box["ssh"] = ssh_endpoint(row) or ssh_endpoint(instance(vast, box["id"]) or {}) or box["ssh"]
            if box["ssh"]:
                write_ssh_config(box["id"], box["ssh"])
            target = box["ssh"] and (box["ssh"]["direct"] or box["ssh"]["proxy"])
            age_h = (time.time() - row["start_date"]) / 3600 if row.get("start_date") else 0
            where = f"{target['host']}:{target['port']}" if target else "no ssh yet"
            print(f"   {box['id']:>9} {box['label']:60s} {row.get('actual_status') or '-':8s} "
                  f"${rate:.3f}/h {age_h:5.1f} h  {where}")
        save_registry(registry)
    known = {box["id"] for box in registry["boxes"]}
    for row in live.values():
        if row["id"] in known:
            continue
        if is_fleet_label(row.get("label")):
            print(f"!! UNREGISTERED {row['id']} {row['label']}")
            failures += 1
        else:
            print(f"   {row['id']:>9} {row.get('label') or '(no label)':60s} another workstream's; never touched")
    print(f"== the registered boxes on the account bill ${billing:.3f}/h")
    return 1 if failures else 0


def cmd_ids(args: argparse.Namespace) -> int:
    open_boxes = [box for box in load_registry()["boxes"] if not box["destroyed"]]
    if args.ids:
        unknown = sorted(set(args.ids) - {box["id"] for box in open_boxes})
        if unknown:
            print(f"refused: not registered boxes that are not destroyed: {unknown}", file=sys.stderr)
            return 1
        open_boxes = [box for box in open_boxes if box["id"] in args.ids]
    for box in open_boxes:
        print(f"{box['id']} {box['dph'] or 0:.3f}" if args.dph else box["id"])
    if args.dph:
        print(f"total {sum(box['dph'] or 0 for box in open_boxes):.3f}")
    return 0


def open_entry(instance_id: int) -> dict:
    """A registered box that is not destroyed, with its launch's pins."""
    entry = registered(load_registry(), instance_id)
    if entry is None or entry["destroyed"]:
        raise RuntimeError(f"{instance_id} is not a registered box that is not destroyed")
    pins = load_launch(entry["launch"])
    return entry | pins | minimums(entry["task"]) | {"commit": entry.get("commit", pins["commit"])}


def cmd_entry(args: argparse.Namespace) -> int:
    try:
        values = open_entry(args.id)
    except (RuntimeError, OSError, KeyError, ValueError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    for name in args.fields:
        print("" if values.get(name) is None else values[name])
    return 0


def cmd_repin(args: argparse.Namespace) -> int:
    try:
        pin = fleet_pin(args.commit)
        entries = [open_entry(instance_id) for instance_id in args.ids]
    except (RuntimeError, OSError, KeyError, ValueError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    other = [entry["id"] for entry in entries if entry["requirements_sha256"] != pin["requirements_sha256"]]
    if other:
        print(f"refused: the .venv's package list is not the one {other} pinned; a new list takes a new launch",
              file=sys.stderr)
        return 1
    for entry in entries:
        update_box(entry["id"], commit=pin["commit"])
        print(f"{entry['id']}: {entry['commit']} to {pin['commit']}; run box_setup.sh {entry['id']} before box_run.sh")
    return 0


def made_elsewhere(name: str) -> str | None:
    """What this machine holds of a run of this name that no box made: a run.json whose eval log is not under the
    box's clone, or a screen log whose command is not the box's."""
    run = settings.RUNS / name / "run.json"
    if run.exists():
        eval_log = json.loads(run.read_text()).get("eval_log") or ""
        if not eval_log.startswith(REMOTE_REPO + "/"):
            return f"{run.parent} (its eval log {eval_log or 'unknown'})"
    log = settings.LOGS / "screen" / f"{name}.log"
    if log.exists():
        with open(log) as handle:
            if f"{REMOTE_REPO}/" not in handle.readline():
                return str(log)
    return None


def cmd_run_args(args: argparse.Namespace) -> int:
    try:
        values = open_entry(args.id)
        suffix = args.suffix or (values["suffix"] + "mock" if args.mock else values["suffix"])
        if not re.fullmatch(r"[a-z0-9]+", suffix) or not suffix.startswith(values["suffix"]):
            raise RuntimeError(f"the suffix {suffix!r} must be lowercase letters and digits starting with "
                               f"{values['suffix']!r}, the model's, so the run names say which model ran them")
        if args.mock and "mock" not in suffix:
            raise RuntimeError(f"the suffix {suffix!r} must say mock: a mock run must not stand in for the screen's")
        if args.samples < 1:
            raise RuntimeError("--samples must be 1 or more")
        kinds = args.kinds or list(kinds_of(values["task"]))
        task_cells = cells(values["task"], tuple(kinds))
    except (RuntimeError, OSError, KeyError, ValueError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    names = [run_name(cell, suffix, k) for k in range(1, args.samples + 1) for cell in task_cells]
    clashes = list(filter(None, map(made_elsewhere, names)))
    if clashes:
        print("refused: this machine holds runs of these names made elsewhere, which the watch would overwrite: "
              + ", ".join(clashes), file=sys.stderr)
        return 1
    model = MOCK_MODEL if args.mock else values["model"]
    print("\n".join(["--task", values["task"], "--model", model, "--suffix", suffix, "--commit",
                     values["commit"], "--samples", str(args.samples), "--kinds", *kinds]))
    return 0


# ---- destroy ----

def identity_refusals(instance_id: int, entry: dict | None, live: dict | None) -> list[str]:
    """Why this is not a box of this fleet to destroy now; empty when it is."""
    if entry is None:
        return [f"{instance_id} is not in the registry: fleet.py destroys only the boxes it created"]
    if entry["destroyed"]:
        return [f"{instance_id} was destroyed at {entry['destroyed']}"]
    if live is None:
        return [f"the account lists no instance {instance_id}"]
    if not is_fleet_label(live.get("label")) or live.get("label") != entry["label"]:
        return [f"vast.ai labels {instance_id} {live.get('label')!r}, the registry {entry['label']!r}: not a box of "
                f"this fleet"]
    return []


def destroy_refusals(instance_id: int, entry: dict | None, live: dict | None, marker: dict | None,
                     now: float) -> list[str]:
    """Why the box may not be destroyed now; empty when every gate holds."""
    refusals = identity_refusals(instance_id, entry, live)
    if entry is None or entry["destroyed"]:
        return refusals
    if marker is None:
        refusals.append("no capture marker: run scripts/box/capture_check.sh first")
    elif marker.get("id") != instance_id:
        refusals.append(f"the capture marker is for {marker.get('id')}")
    elif now - marker.get("epoch", 0) > CAPTURE_FRESH_S:
        refusals.append(f"the capture passed {(now - marker['epoch']) / 60:.0f} minutes ago, more than "
                        f"{CAPTURE_FRESH_S // 60}: run capture_check.sh again")
    elif marker.get("lost") != 0 or not marker.get("swept"):
        refusals.append(f"the capture marker records {marker.get('lost')} files lost of {marker.get('swept')}")
    return refusals


def wait_gone(vast: Vast, instance_id: int) -> bool:
    """Whether the account stops listing the instance within DESTROY_WAIT_S."""
    deadline = time.time() + DESTROY_WAIT_S
    while True:
        if all(row["id"] != instance_id for row in list_instances(vast)):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(10)


def what_is_lost(instance_id: int) -> dict:
    """For --accept-loss: the capture check run once, what it found missing or different, or why it could not run."""
    try:
        report, _ = box_capture.run_check(instance_id, pull=False)
    except (FileNotFoundError, ValueError, OSError, subprocess.SubprocessError) as error:
        return {"reachable": False, "error": f"{type(error).__name__}: {error}"[:500]}
    return {"reachable": True, "summary": report.summary(), "swept": report.swept, "captured": report.captured,
            "lost": report.lost, "missing": [path for path, _ in report.missing][:500],
            "differ": [path for path, _ in report.differ][:500], "problems": report.problems[:50]}


def cmd_destroy(args: argparse.Namespace) -> int:
    vast = Vast(writes=not args.dry_run)
    entry = registered(load_registry(), args.id)
    live = instance(vast, args.id) if entry else None
    marker, snap, lost = None, None, None
    if args.accept_loss is not None:
        if len(args.accept_loss.strip()) < 10:
            print("refused: --accept-loss takes the reason in words")
            return 2
        refusals = identity_refusals(args.id, entry, live)
        if refusals:
            print("refused:\n  " + "\n  ".join(refusals))
            return 1
        lost = what_is_lost(args.id)
        print(f"== {args.id} {entry['label']}: destroyed with its losses accepted ({args.accept_loss}); the check "
              f"found: {json.dumps(lost)[:2000]}")
    else:
        refusals = destroy_refusals(args.id, entry, live, box_capture.read_marker(args.id), time.time())
        if refusals:
            print("refused:\n  " + "\n  ".join(refusals))
            return 1
        print(f"== {args.id} {entry['label']}: the capture of {box_capture.read_marker(args.id)['time']} passed; "
              f"checking again now")
        marker = box_capture.check(args.id, pull=False)
        if marker is None:
            print("refused: the capture check run now does not pass")
            return 1
        try:
            snap = box_capture.snapshot(args.id)
        except (RuntimeError, OSError) as error:
            print(f"refused: the snapshot failed: {error}")
            return 1
        print("== not captured, by design: " + "; ".join(f"{reason} ({n['files']} files)"
                                                         for reason, n in marker["excluded"].items()))
        if marker.get("docker_accepted"):
            print(f"== Docker's containers and volumes go with the box: {marker['docker_accepted']['reason']}")
    print("== never swept, gone with the box: " + "; ".join(NOT_SWEPT))
    call = Call("DELETE", f"/api/v0/instances/{args.id}/")
    if args.dry_run:
        print(f"== dry run: would send {call.show()}")
        return 0
    before = credit(vast)
    hours = (time.time() - live["start_date"]) / 3600 if live.get("start_date") else None
    update_box(args.id, destroy_sent=now_iso())
    vast.send(call)
    gone = wait_gone(vast, args.id)
    if gone:
        update_box(args.id, destroyed=now_iso())
    entry_log = {"id": args.id, "label": entry["label"], "destroy_sent": registered(load_registry(), args.id)[
                     "destroy_sent"], "gone_from_account": gone, "accepted_loss": args.accept_loss, "lost": lost,
                 "files_swept": marker["swept"] if marker else None,
                 "files_captured": marker["captured"] if marker else None,
                 "files_lost": marker["lost"] if marker else (lost or {}).get("lost"),
                 "excluded": marker["excluded"] if marker else None,
                 "docker_accepted": marker.get("docker_accepted") if marker else None, "snapshot": snap,
                 "dph": live.get("dph_total"), "hours": hours,
                 "cost_usd_estimate": round(live["dph_total"] * hours, 2) if hours and live.get("dph_total") else None,
                 "credit_before": before, "credit_after": credit(vast)}
    BOXES.mkdir(parents=True, exist_ok=True)
    with open(BOXES / "destroyed.jsonl", "a") as log:
        log.write(json.dumps(entry_log) + "\n")
    print(json.dumps(entry_log, indent=1))
    if not gone:
        print(f"!! vast.ai took the destroy of {args.id} and still lists it after {DESTROY_WAIT_S} s: it stays in the "
              f"registry as not destroyed, so status, ids and watch.sh keep showing it; destroy it again")
    return 0 if gone else 1


# ---- offers ----

def cmd_offers(args: argparse.Namespace) -> int:
    offers = json.loads(Path(args.saved).read_text()) if args.saved else \
        Vast(writes=False).send(search_call())["offers"]
    offers = offers["offers"] if isinstance(offers, dict) else offers
    kept = candidates(offers, args.max_dph)
    reasons = Counter(reason for offer in offers for reason in rejections(offer, args.max_dph))
    print(f"== {len(offers)} offers searched, {len(kept)} can hold a box; "
          f"{len({o.get('machine_id') for o in kept})} machines")
    for reason, n in reasons.most_common():
        print(f"   {n:4d} rejected: {reason}")
    print(OFFER_HEADER)
    for offer in kept[:args.top]:
        print(offer_row(offer))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    offers = commands.add_parser("offers", help="the offers that can hold a box, cheapest first (reads only)")
    offers.add_argument("--top", type=int, default=20)
    offers.add_argument("--max-dph", type=float, default=MAX_DPH, help="the dearest offer, $/h with the disk")
    offers.add_argument("--saved", help="rank a saved search (vastai search offers ... --raw) instead of searching")
    create = commands.add_parser("create", help="rent one box per (task, model) and register each")
    create.add_argument("--count", type=int, required=True, help="the number of boxes, which must match")
    create.add_argument("--tasks", nargs="+", default=list(TASKS))
    create.add_argument("--models", nargs="+", default=list(MODELS))
    create.add_argument("--label-prefix", default=LABEL_PREFIX)
    create.add_argument("--max-dph", type=float, default=MAX_DPH, help="the dearest offer, $/h with the disk")
    create.add_argument("--max-fleet-dph", type=float, default=MAX_FLEET_DPH,
                        help="what the fleet may bill per hour in all, its registered boxes included")
    create.add_argument("--commit", default="HEAD", help="what the boxes run, a commit origin/main holds")
    create.add_argument("--dry-run", action="store_true", help="search and print every request; send none")
    commands.add_parser("status", help="the account's instances with the registry; writes each box's ssh_config")
    ids = commands.add_parser("ids", help="the registered boxes not yet destroyed")
    ids.add_argument("ids", type=int, nargs="*", help="only these, each of which must be one")
    ids.add_argument("--dph", action="store_true", help="each box's $/h, and the total last")
    entry = commands.add_parser("entry", help="fields of a registered box and its launch, one per line")
    entry.add_argument("id", type=int)
    entry.add_argument("fields", nargs="+", choices=ENTRY_FIELDS)
    repin = commands.add_parser("repin", help="move boxes to a commit origin/main holds; box_setup.sh checks it out")
    repin.add_argument("ids", type=int, nargs="+")
    repin.add_argument("--commit", required=True, help="what the boxes run from now on")
    run_args = commands.add_parser("run-args", help="box_runner.py run's arguments for a box, one per line")
    run_args.add_argument("id", type=int)
    run_args.add_argument("--suffix", help="the run names' suffix: the registry's, or one that starts with it")
    run_args.add_argument("--samples", type=int, default=3)
    run_args.add_argument("--kinds", nargs="+", choices=list(EVERY_KIND), help="by default every cell of the task")
    run_args.add_argument("--mock", action="store_true", help="the mock model instead of the box's")
    destroy = commands.add_parser("destroy", help="destroy one box once its capture holds")
    destroy.add_argument("id", type=int)
    destroy.add_argument("--dry-run", action="store_true", help="every gate, the capture check and the snapshot; no "
                                                                "destroy")
    destroy.add_argument("--accept-loss", metavar="REASON",
                         help="destroy a box that can never pass the capture, recording what is lost and why")
    args = parser.parse_args(argv)
    command = {"offers": cmd_offers, "create": cmd_create, "status": cmd_status, "ids": cmd_ids,
               "entry": cmd_entry, "repin": cmd_repin, "run-args": cmd_run_args,
               "destroy": cmd_destroy}[args.command]
    return command(args)


if __name__ == "__main__":
    sys.exit(main())
