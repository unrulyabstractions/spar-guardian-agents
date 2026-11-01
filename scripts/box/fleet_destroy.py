"""fleet.py destroy: destroy one box once its capture holds.

destroy refuses unless the id is in the registry, vast.ai labels it as the registry does (spar-screen-*),
capture_check.sh passed for it within the last 10 minutes, and a capture check run again now passes. It prints what the
capture leaves behind, makes a read-only snapshot of the captured files and reads it back against the manifest, records
the destroy as sent, destroys, and marks the box destroyed only once the account no longer lists it. It appends the
entry for VERIFICATION_LOG.md (id, files swept, captured and lost, the snapshot, cost) to logs/boxes/destroyed.jsonl.
--accept-loss REASON is for a box that can never pass the capture: it keeps the registry and label gates, runs the check
once to record what is lost when the box answers, and logs the reason with the destroy.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time

from scripts.box import box_capture, fleet_registry
from scripts.box.fleet_layout import is_fleet_label, now_iso
from scripts.box.fleet_registry import load_registry, registered, update_box
from scripts.box.vast_api import Call, Vast, credit, instance, list_instances

CAPTURE_FRESH_S = 600
DESTROY_WAIT_S = 180
NOT_SWEPT = ("Docker's images, and its containers and volumes unless the capture saved them (/var/lib/docker)",
             "/var", "/home", "/opt", "/etc", "/usr", "/srv", "/mnt", "every other path outside /root/work, /root "
             "and /tmp")
"""What the capture never sweeps, printed before every destroy."""


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
    fleet_registry.BOXES.mkdir(parents=True, exist_ok=True)
    with open(fleet_registry.BOXES / "destroyed.jsonl", "a") as log:
        log.write(json.dumps(entry_log) + "\n")
    print(json.dumps(entry_log, indent=1))
    if not gone:
        print(f"!! vast.ai took the destroy of {args.id} and still lists it after {DESTROY_WAIT_S} s: it stays in the "
              f"registry as not destroyed, so status, ids and watch.sh keep showing it; destroy it again")
    return 0 if gone else 1
