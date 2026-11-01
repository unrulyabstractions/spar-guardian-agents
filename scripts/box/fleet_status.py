"""fleet.py status: the account's instances joined with the registry.

status lists every page of the account, records each box's ssh endpoint from that listing (one box's own record only
for a row without one) and writes logs/boxes/<id>/ssh_config, which box_ssh.sh, box_scp.sh and rsync use. It fails on a
registered box the account lacks, a box marked destroyed or sent a destroy that the account still lists, a box vast.ai
cannot start, and a spar-screen instance outside the registry, and prints what the registered boxes bill (a stopped box
its disk alone). A box that cannot start stays on the account, billing its disk, and its error goes into the registry,
so create never rents that machine again.
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from scripts.box import fleet_registry
from scripts.box.fleet_layout import LABEL_PREFIX, is_fleet_label, now_iso
from scripts.box.fleet_registry import load_registry, registry_lock, save_registry
from scripts.box.vast_api import Vast, instance, list_instances, ssh_endpoint, start_error

SSH_KEY = "~/.ssh/id_ed25519"


def write_ssh_config(instance_id: int, ssh: dict) -> Path:
    """logs/boxes/<id>/ssh_config: host `box` at the direct endpoint (the proxy when there is none), root with the
    account's key, its own known_hosts, no prompts. ssh -F <it> box, scp and rsync -e 'ssh -F <it>' then need no
    options on their command lines."""
    folder = fleet_registry.BOXES / str(instance_id)
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


def check_box(vast: Vast, box: dict, row: dict | None) -> tuple[int, float]:
    """Hold one registered box to its row of the account, updating the box's record; its failures and what it bills per
    hour."""
    if box["destroyed"]:
        if row is None:
            return 0, 0.0
        print(f"!! {box['id']} {box['label']}: marked destroyed at {box['destroyed']}, still on the "
              f"account ({row.get('actual_status')}, ${row.get('dph_total') or 0:.3f}/h)")
        return 1, 0.0
    if row is None:
        if box.get("destroy_sent"):
            box["destroyed"] = now_iso()
            print(f"   {box['id']} {box['label']}: gone from the account since the destroy sent at "
                  f"{box['destroy_sent']}; marked destroyed")
            return 0, 0.0
        print(f"!! {box['id']} {box['label']}: registered, not on the account")
        return 1, 0.0
    failures = 0
    if box.get("destroy_sent"):
        print(f"!! {box['id']} {box['label']}: destroy sent at {box['destroy_sent']}, still on the account")
        failures += 1
    why = start_error(row)
    if why:
        box["start_error"] = why
        print(f"!! {box['id']} {box['label']}: vast.ai cannot start it ({why}); rent another with --only {box['box']} "
              f"--label-prefix {LABEL_PREFIX}-retry, and this one bills its disk until it goes")
        failures += 1
    rate = (row.get("storage_total_cost") if row.get("cur_state") == "stopped" else row.get("dph_total")) or 0
    box["ssh"] = ssh_endpoint(row) or ssh_endpoint(instance(vast, box["id"]) or {}) or box["ssh"]
    if box["ssh"]:
        write_ssh_config(box["id"], box["ssh"])
    target = box["ssh"] and (box["ssh"]["direct"] or box["ssh"]["proxy"])
    age_h = (time.time() - row["start_date"]) / 3600 if row.get("start_date") else 0
    where = f"{target['host']}:{target['port']}" if target else "no ssh yet"
    print(f"   {box['id']:>9} {box['label']:60s} {row.get('actual_status') or '-':8s} "
          f"${rate:.3f}/h {age_h:5.1f} h  {where}")
    return failures, rate


def cmd_status(args: argparse.Namespace) -> int:
    vast = Vast(writes=False)
    live = {row["id"]: row for row in list_instances(vast)}
    failures, billing = 0, 0.0
    with registry_lock():
        registry = load_registry()
        print(f"== {len(live)} instances on the account; "
              f"{sum(not box['destroyed'] for box in registry['boxes'])} boxes in the registry not destroyed")
        for box in registry["boxes"]:
            box_failures, rate = check_box(vast, box, live.get(box["id"]))
            failures += box_failures
            billing += rate
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
