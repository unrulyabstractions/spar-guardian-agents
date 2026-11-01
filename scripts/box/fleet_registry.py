"""The fleet's records: the registry runs/boxes.json of every instance, and each launch's pins.

A launch pins the commit, the package list and the Python and uv versions in logs/boxes/launch-<launch>/, so every box
of the launch runs the same code and packages whatever this checkout does later. repin moves a box to another commit.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

from scripts.box.fleet_layout import BOX_KEY, BOXES, REGISTRY, ROOT, floor_of, now_iso
from scripts.box.study_table import Box
from scripts.box.vast_offers import DISK_GB, ram_share_gb


def minimums(task: str) -> dict[str, int]:
    """What box_setup.sh requires a box of the task to have (nproc, MemTotal, the root filesystem; GB of 10^9 bytes):
    the offer's floor, less what a VM's kernel and filesystem take."""
    cores, ram_gb = floor_of(task)
    return {"min_cores": cores - 2, "min_ram_gb": int(0.9 * ram_gb), "min_disk_gb": int(0.85 * DISK_GB)}


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


def box_entry(instance_id: int, label: str, box: Box, offer: dict | None, launch: str) -> dict:
    """The registry's record of one instance. Only what identifies and prices the box: never the reply's keys."""
    offer = offer or {}
    return {"id": instance_id, "label": label, "box": box.name, "stage": box.stage, "task": box.task,
            "launch": launch, "offer": offer.get("id"), "machine_id": offer.get("machine_id"),
            "dph": offer.get("dph_total"), "cores": offer.get("cpu_cores_effective"),
            "ram_gb": round(ram_share_gb(offer), 1) if offer else None, "disk_gb": DISK_GB,
            "reliability": offer.get("reliability"), "geolocation": offer.get("geolocation"),
            "created": now_iso(), "ssh": None, "destroy_sent": None, "destroyed": None}


def open_dph(registry: dict) -> float:
    """What the registered boxes not destroyed bill per hour, by their offers."""
    return sum(box["dph"] or 0 for box in registry["boxes"] if not box["destroyed"])


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


def open_entry(instance_id: int) -> dict:
    """A registered box that is not destroyed, with its launch's pins."""
    entry = registered(load_registry(), instance_id)
    if entry is None or entry["destroyed"]:
        raise RuntimeError(f"{instance_id} is not a registered box that is not destroyed")
    pins = load_launch(entry["launch"])
    return entry | pins | minimums(entry["task"]) | {"commit": entry.get("commit", pins["commit"]), "key": BOX_KEY}
