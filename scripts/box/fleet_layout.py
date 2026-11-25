"""What every box shares: where things live, a box's label, the one key a box holds and the least a box of a task needs.

    box_label("spar-screen", "regulator-scale-1-2", "l1a2b3c") -> "spar-screen-regulator-scale-1-2-l1a2b3c"
    floor_of(["swe"])                                           -> (32, 64)

A label ends with the launch that rented the box (l and six hex digits), so a launch adopts only its own instances. A
task is run_study's: the folder of a cell module under studies/cells/ or examples/ (swe, creative_writing,
protein_design).
"""
from __future__ import annotations

import re
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from scripts.study import host_facts

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "runs" / "boxes.json"
"""Every instance fleet.py created, outside git."""
BOXES = ROOT / "logs" / "boxes"
"""One folder per instance: its ssh config and known hosts, its capture marker and manifest, the copies of its files
outside the repository's runs/ and logs/; and one folder per launch (launch-<launch>/) with what it pinned."""
REMOTE_REPO = "/root/work"
"""The repository's clone on a box."""
LAUNCHER_LOG = "/root/launcher.log"
"""What the launcher on a box prints: box_run.sh sends its output here."""

LABEL_PREFIX = "spar-screen"
BOX_KEY = host_facts.KEY
"""The one model key a box holds and the launcher on Linux reads, so every model on a box routes through
OpenRouter."""
FLOORS = {"swe": (32, 64)}
"""A box's cores and GB of RAM where a task needs more than LIGHT_FLOOR: SWE builds and tests Go on one machine per
agent."""
LIGHT_FLOOR = (16, 32)
"""A box's cores and GB of RAM for the other tasks: their agents wait on a model most of the time."""
LAUNCH = re.compile(r"^l[0-9a-f]{6}$")


def floor_of(tasks: list[str]) -> tuple[int, int]:
    """The cores and GB of RAM a box of these tasks needs: the largest of each."""
    floors = [FLOORS.get(task, LIGHT_FLOOR) for task in tasks] or [LIGHT_FLOOR]
    return max(cores for cores, _ in floors), max(ram for _, ram in floors)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_launch() -> str:
    return "l" + secrets.token_hex(3)


def box_label(prefix: str, box: str, launch: str) -> str:
    if not LAUNCH.match(launch):
        raise ValueError(f"not a launch id: {launch!r}")
    return f"{prefix}-{box}-{launch}"


def label_base(label: str) -> str:
    """The label without its launch: the box's identity across launches."""
    base, _, last = label.rpartition("-")
    return base if base and LAUNCH.match(last) else label


def from_root(path: Path | str) -> str:
    """A path from the checkout's root; ValueError for one outside it, which the box's clone would not hold."""
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(ROOT):
        raise ValueError(f"{path} is outside this checkout, so the box's clone does not hold it")
    return resolved.relative_to(ROOT).as_posix()


def committed(commit: str, path: Path | str) -> str:
    """A file of this checkout as the commit holds it (git show <commit>:<path>), which is what a box's clone runs.
    ValueError for a path outside the checkout or one the commit lacks."""
    rel = from_root(path)
    shown = subprocess.run(["git", "-C", str(ROOT), "show", f"{commit}:{rel}"], capture_output=True)
    if shown.returncode:
        raise ValueError(f"commit {commit[:12]} holds no {rel}: commit it and push it first")
    return shown.stdout.decode()


def is_fleet_label(label: str | None) -> bool:
    """Whether a label is one fleet.py gives (spar-screen-*): destroy acts on nothing else."""
    return bool(label) and label.startswith(LABEL_PREFIX + "-")
