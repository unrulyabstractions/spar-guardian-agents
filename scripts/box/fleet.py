"""The vast.ai fleet: find offers, rent one box per (task, model), list the boxes, destroy one once its capture holds.

    uv run python -m scripts.box.fleet offers [--top 20] [--saved search.json]
    uv run python -m scripts.box.fleet create --count 15 [--tasks ...] [--models ...] [--label-prefix spar-screen]
                                              [--dry-run]
    uv run python -m scripts.box.fleet status
    uv run python -m scripts.box.fleet ids
    uv run python -m scripts.box.fleet destroy <id> [--dry-run]

offers searches the on-demand offers (POST /api/v0/bundles/, the request `vastai search offers` sends) and keeps the
ones that can hold a box, cheapest first: x86_64, a host that runs VMs (the box is the kvm image as a VM, with Docker
inside), at least 32 cores and 64 GB of RAM in the share the offer rents, 200 GB of disk, reliability 0.98, an open
port for direct ssh, and a host outside the countries the model providers do not serve. vast.ai gives an instance the
offer's share of the machine's cores (cpu_cores_effective) and the same share of its RAM; cpu_ram is the machine's.

create rents one offer per box in the same step as the search, since offer ids expire within minutes: PUT
/api/v0/asks/<offer>/ with the kvm image as a VM, 200 GB and direct ssh, labelled spar-screen-<task>-<model>. Each
instance goes into the registry runs/boxes.json the moment vast.ai returns its id. A rejected offer moves on to the
next one; any other failure stops the launch, since the instance may exist. The launch ends with an audit of the
account: an instance this launch asked for and did not hear back about is registered then, and any other spar-screen
instance missing from the registry fails the launch. --dry-run does the search and prints every request it would
send, and sends none: its client refuses anything but a read.

status lists the account's instances (GET /api/v1/instances/, every page) joined with the registry, records each
box's ssh endpoint (GET /api/v0/instances/<id>/) and writes logs/boxes/<id>/ssh_config, which box_ssh.sh, box_scp.sh
and rsync use. ids prints the registered boxes not yet destroyed.

destroy refuses unless the id is in the registry, vast.ai labels it spar-screen-* as the registry does,
capture_check.sh passed for it within the last 10 minutes, and a capture check run again now passes. It then destroys
the instance, waits until the account no longer lists it, and appends the entry for VERIFICATION_LOG.md (id, files
swept, captured and lost, cost) to logs/boxes/destroyed.jsonl.

The API key is read from ~/.config/vastai/vast_api_key and goes only into the Authorization header. Nothing prints it,
nor the instance key a create returns, nor the account record beyond its credit.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from scripts.box import box_capture
from scripts.box.fleet_layout import BOXES, LABEL_PREFIX, MODELS, REGISTRY, TASKS, box_label, boxes, is_fleet_label

API = "https://console.vast.ai"
KEY_FILE = Path.home() / ".config" / "vastai" / "vast_api_key"
SSH_KEY = "~/.ssh/id_ed25519"
TIMEOUT_S = 60

IMAGE = "docker.io/vastai/kvm:ubuntu_terminal"
DISK_GB = 200
MIN_CORES = 32
MIN_RAM_GB = 64
MIN_RELIABILITY = 0.98
MAX_DPH = 1.00
"""The dearest offer, in $/h with the disk, that offers and create take unless --max-dph says otherwise."""
BLOCKED_COUNTRIES = {"CN", "HK", "MO", "RU", "BY", "IR", "KP", "SY", "CU"}
"""Where OpenAI or Anthropic do not serve their API, or where the box could not reach it."""
MAX_REJECTIONS = 10
"""Offers vast.ai may refuse in one launch (taken since the search) before the launch stops."""
CAPTURE_FRESH_S = 600
SEARCH_PATH = "/api/v0/bundles/"


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


def http(call: Call) -> dict:
    """Send one call with the account's key in the Authorization header; the reply's JSON."""
    key = KEY_FILE.read_text().strip()
    request = urllib.request.Request(call.url, method=call.method,
                                     data=None if call.body is None else json.dumps(call.body).encode(),
                                     headers={"Authorization": f"Bearer {key}", "Accept": "application/json",
                                              "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as reply:
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
        return self.transport(call)


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
    """The RAM an instance of the offer gets: the machine's RAM (cpu_ram, MB) times the offer's share of its cores."""
    cores = offer.get("cpu_cores") or 0
    share = (offer.get("cpu_cores_effective") or 0) / cores if cores else 0.0
    return (offer.get("cpu_ram") or 0) / 1000 * share


def country(offer: dict) -> str | None:
    """The two-letter code that ends geolocation ("Quebec, CA" -> "CA")."""
    location = offer.get("geolocation") or ""
    code = location.rsplit(",", 1)[-1].strip()
    return code if len(code) == 2 else None


def rejections(offer: dict, max_dph: float = MAX_DPH) -> list[str]:
    """Why an offer cannot hold a box; empty when it can."""
    checks = [
        (offer.get("cpu_arch") == "amd64", "not x86_64"),
        (offer.get("vms_enabled") is True, "no VM support"),
        (not offer.get("is_vm_deverified"), "VM support deverified"),
        ((offer.get("cpu_cores_effective") or 0) >= MIN_CORES, f"fewer than {MIN_CORES} cores"),
        (ram_share_gb(offer) >= MIN_RAM_GB, f"less than {MIN_RAM_GB} GB of RAM in its share"),
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


# ---- the registry ----

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


def register(entry: dict) -> None:
    """Add one instance to the registry at once, under the lock."""
    with registry_lock():
        registry = load_registry()
        if any(box["id"] == entry["id"] for box in registry["boxes"]):
            raise ValueError(f"instance {entry['id']} is registered already")
        registry["boxes"].append(entry)
        save_registry(registry)


def registered(registry: dict, instance_id: int) -> dict | None:
    return next((box for box in registry["boxes"] if box["id"] == instance_id), None)


def box_entry(instance_id: int, label: str, task: str, model: str, offer: dict | None) -> dict:
    """The registry's record of one instance. Only what identifies and prices the box: never the reply's keys."""
    offer = offer or {}
    return {"id": instance_id, "label": label, "task": task, "model": model, "suffix": MODELS[model],
            "offer": offer.get("id"), "machine_id": offer.get("machine_id"), "dph": offer.get("dph_total"),
            "cores": offer.get("cpu_cores_effective"), "ram_gb": round(ram_share_gb(offer), 1) if offer else None,
            "disk_gb": DISK_GB, "reliability": offer.get("reliability"), "geolocation": offer.get("geolocation"),
            "created": now_iso(), "ssh": None, "destroyed": None}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    return vast.send(Call("GET", "/api/v0/users/current")).get("credit")


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
    config.write_text("\n".join([
        "Host box", f"  HostName {target['host']}", f"  Port {target['port']}", "  User root",
        f"  IdentityFile {SSH_KEY}", "  IdentitiesOnly yes", f'  UserKnownHostsFile "{folder / "known_hosts"}"',
        "  StrictHostKeyChecking accept-new", "  ServerAliveInterval 30", "  ServerAliveCountMax 6",
        "  ConnectTimeout 20", "  BatchMode yes", "  LogLevel ERROR", ""]))
    return config


# ---- create ----

def create_call(offer_id: int, label: str) -> Call:
    """The rent request: the kvm image as a VM, its disk, direct ssh and the box's label."""
    return Call("PUT", f"/api/v0/asks/{offer_id}/",
                body={"image": IMAGE, "disk": DISK_GB, "runtype": "ssh_direct", "vm": True, "label": label})


def try_create(vast: Vast, call: Call) -> tuple[str, int | None, str]:
    """("created", id, ""), ("rejected", None, why) when vast.ai refused the offer and made nothing, or ("unknown",
    None, why) when the instance may exist."""
    try:
        reply = vast.send(call)
    except urllib.error.HTTPError as error:
        text = error.read().decode(errors="replace")[:300]
        if 400 <= error.code < 500 and error.code not in (401, 403, 429):
            return "rejected", None, f"HTTP {error.code}: {text}"
        return "unknown", None, f"HTTP {error.code}: {text}"
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return "unknown", None, f"{type(error).__name__}: {error}"
    if reply.get("success") and reply.get("new_contract"):
        return "created", int(reply["new_contract"]), ""
    if reply.get("success") is False:
        return "rejected", None, str(reply.get("msg") or reply.get("error"))[:300]
    return "unknown", None, f"a reply without an instance id (keys {sorted(reply)})"


def audit(vast: Vast, asked: dict[str, tuple[str, str]], since: float) -> int:
    """After a launch: register any instance this launch asked for (by its label, created since the launch began)
    that the registry lacks, then fail on any other spar-screen instance outside the registry."""
    live = list_instances(vast)
    known = {box["id"] for box in load_registry()["boxes"]}
    failures = 0
    for row in live:
        if row["id"] in known or not is_fleet_label(row.get("label")):
            continue
        if row["label"] in asked and (row.get("start_date") or since) >= since - 60:
            task, model = asked[row["label"]]
            register(box_entry(row["id"], row["label"], task, model, None) | {"note": "registered by the audit"})
            print(f"   registered {row['id']} {row['label']}: the launch asked for it and did not hear back")
        else:
            print(f"!! UNREGISTERED {row['id']} {row['label']}: a spar-screen instance outside the registry")
            failures += 1
    mine = [box for box in load_registry()["boxes"] if box["label"] in asked and not box["destroyed"]]
    print(f"== audit: {len(live)} instances on the account, {len(mine)} of this launch registered")
    for box in mine:
        print(f"   {box['id']} {box['label']} offer {box['offer']} ${box['dph'] or 0:.3f}/h")
    return 1 if failures else 0


def cmd_create(args: argparse.Namespace) -> int:
    pairs = boxes(args.tasks, args.models)
    if len(pairs) != args.count:
        print(f"--count {args.count} but the tasks and models make {len(pairs)} boxes: say the count you mean")
        return 2
    if not args.label_prefix.startswith(LABEL_PREFIX):
        print(f"--label-prefix must start with {LABEL_PREFIX}: destroy acts on nothing else")
        return 2
    asked = {box_label(args.label_prefix, task, model): (task, model) for task, model in pairs}
    vast = Vast(writes=not args.dry_run)
    clash = [row for row in list_instances(vast) if row.get("label") in asked]
    if clash:
        print("refused: the account holds these labels already: "
              + ", ".join(f"{row['id']} {row['label']}" for row in clash))
        return 1
    print(f"== credit ${credit(vast) or 0:.2f}; {args.count} boxes; search: {search_call().show()}")
    offers = candidates(vast.send(search_call())["offers"], args.max_dph)
    print(f"== {len(offers)} offers can hold a box at ${args.max_dph:.2f}/h or less")
    if len(offers) < args.count and not args.dry_run:
        print(f"refused: {len(offers)} offers for {args.count} boxes")
        return 1
    started, remaining, rejected, filled, unfilled = time.time(), iter(offers), 0, 0, []
    for label, (task, model) in asked.items():
        for offer in remaining:
            call = create_call(offer["id"], label)
            if args.dry_run:
                print(f"   would send: {call.show()}\n      offer {offer_row(offer)}")
                filled += 1
                break
            outcome, instance_id, why = try_create(vast, call)
            if outcome == "created":
                register(box_entry(instance_id, label, task, model, offer))
                print(f"   created {instance_id} {label} on offer {offer['id']} ${offer['dph_total']:.3f}/h")
                filled += 1
                break
            print(f"   {outcome}: offer {offer['id']} for {label}: {why}")
            if outcome == "unknown":
                return audit(vast, asked, started) or 1
            rejected += 1
            if rejected > MAX_REJECTIONS:
                print(f"== stopped: {rejected} offers refused")
                return audit(vast, asked, started) or 1
        else:
            unfilled.append(label)
            if not args.dry_run:
                break
    if unfilled:
        print(f"== no offer left for: {', '.join(unfilled)}")
    if args.dry_run:
        print(f"== dry run: {filled} of {len(asked)} requests printed, none sent")
        return 1 if unfilled else 0
    return audit(vast, asked, started) or (1 if unfilled else 0)


# ---- status ----

def cmd_status(args: argparse.Namespace) -> int:
    vast = Vast(writes=False)
    live = {row["id"]: row for row in list_instances(vast)}
    failures = 0
    with registry_lock():
        registry = load_registry()
        print(f"== {len(live)} instances on the account; "
              f"{sum(not box['destroyed'] for box in registry['boxes'])} boxes in the registry not destroyed")
        for box in registry["boxes"]:
            if box["destroyed"]:
                continue
            row = live.get(box["id"])
            if row is None:
                print(f"!! {box['id']} {box['label']}: registered, not on the account")
                failures += 1
                continue
            record = instance(vast, box["id"]) or {}
            box["ssh"] = ssh_endpoint(record) or box["ssh"]
            if box["ssh"]:
                write_ssh_config(box["id"], box["ssh"])
            target = box["ssh"] and (box["ssh"]["direct"] or box["ssh"]["proxy"])
            age_h = (time.time() - row["start_date"]) / 3600 if row.get("start_date") else 0
            where = f"{target['host']}:{target['port']}" if target else "no ssh yet"
            print(f"   {box['id']:>9} {box['label']:52s} {row.get('actual_status') or '-':8s} "
                  f"${row.get('dph_total') or 0:.3f}/h {age_h:5.1f} h  {where}")
        save_registry(registry)
    known = {box["id"] for box in registry["boxes"]}
    for row in live.values():
        if row["id"] in known:
            continue
        if is_fleet_label(row.get("label")):
            print(f"!! UNREGISTERED {row['id']} {row['label']}")
            failures += 1
        else:
            print(f"   {row['id']:>9} {row.get('label') or '(no label)':52s} another workstream's; never touched")
    return 1 if failures else 0


def cmd_ids(args: argparse.Namespace) -> int:
    for box in load_registry()["boxes"]:
        if not box["destroyed"]:
            print(box["id"])
    return 0


# ---- destroy ----

def destroy_refusals(instance_id: int, entry: dict | None, live: dict | None, marker: dict | None,
                     now: float) -> list[str]:
    """Why the box may not be destroyed now; empty when every gate holds."""
    if entry is None:
        return [f"{instance_id} is not in the registry: fleet.py destroys only the boxes it created"]
    if entry["destroyed"]:
        return [f"{instance_id} was destroyed at {entry['destroyed']}"]
    refusals = []
    if live is None:
        refusals.append(f"the account lists no instance {instance_id}")
    elif not is_fleet_label(live.get("label")) or live.get("label") != entry["label"]:
        refusals.append(f"vast.ai labels {instance_id} {live.get('label')!r}, the registry {entry['label']!r}: "
                        f"not a box of this fleet")
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


def cmd_destroy(args: argparse.Namespace) -> int:
    vast = Vast(writes=not args.dry_run)
    entry = registered(load_registry(), args.id)
    live = instance(vast, args.id) if entry else None
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
    print("== not captured, by design: " + "; ".join(f"{reason} ({n['files']} files)"
                                                     for reason, n in marker["excluded"].items()))
    call = Call("DELETE", f"/api/v0/instances/{args.id}/")
    if args.dry_run:
        print(f"== dry run: would send {call.show()}")
        return 0
    before = credit(vast)
    hours = (time.time() - live["start_date"]) / 3600 if live.get("start_date") else None
    vast.send(call)
    deadline, gone = time.time() + 180, False
    while time.time() < deadline and not gone:
        time.sleep(10)
        gone = all(row["id"] != args.id for row in list_instances(vast))
    with registry_lock():
        registry = load_registry()
        registered(registry, args.id)["destroyed"] = now_iso()
        save_registry(registry)
    entry_log = {"id": args.id, "label": entry["label"], "destroyed": now_iso(), "gone_from_account": gone,
                 "files_swept": marker["swept"], "files_captured": marker["captured"], "files_lost": marker["lost"],
                 "excluded": marker["excluded"], "dph": live.get("dph_total"), "hours": hours,
                 "cost_usd_estimate": round(live["dph_total"] * hours, 2) if hours and live.get("dph_total") else None,
                 "credit_before": before, "credit_after": credit(vast)}
    BOXES.mkdir(parents=True, exist_ok=True)
    with open(BOXES / "destroyed.jsonl", "a") as log:
        log.write(json.dumps(entry_log) + "\n")
    print(json.dumps(entry_log, indent=1))
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
    create.add_argument("--dry-run", action="store_true", help="search and print every request; send none")
    commands.add_parser("status", help="the account's instances with the registry; writes each box's ssh_config")
    commands.add_parser("ids", help="the registered boxes not yet destroyed")
    destroy = commands.add_parser("destroy", help="destroy one box once its capture holds")
    destroy.add_argument("id", type=int)
    destroy.add_argument("--dry-run", action="store_true", help="every gate and the capture check; no destroy")
    args = parser.parse_args(argv)
    command = {"offers": cmd_offers, "create": cmd_create, "status": cmd_status, "ids": cmd_ids,
               "destroy": cmd_destroy}[args.command]
    return command(args)


if __name__ == "__main__":
    sys.exit(main())
