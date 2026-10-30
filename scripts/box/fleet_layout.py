"""What the fleet runs: the tasks, the models, the cells of a task, a box's label, its run names and where things live.

    boxes(["chess"], ["gpt-6-luna", "claude-haiku-5-5"])  -> [("chess", "gpt-6-luna"), ("chess", "claude-haiku-5-5")]
    box_label("spar-screen", "chess", "gpt-6-luna", "l1a2b3c") -> "spar-screen-chess-gpt-6-luna-l1a2b3c"
    cells("knowledge_work")                              -> ["knowledge_work.worktime_solo", ...], one per kind
    run_name("knowledge_work.worktime_solo", "luna", 2)  -> "worktime_solo_luna2"

One box runs one task on one model: the task's cells, one example script per kind, each several samples at once. A
label ends with the launch that rented the box (l and six hex digits), so a launch adopts only its own instances.
Standard library only, so every side imports it: the Mac (fleet.py, box_capture.py) and the box (box_runner.py).
"""
from __future__ import annotations

import re
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "runs" / "boxes.json"
"""Every instance fleet.py created, outside git."""
BOXES = ROOT / "logs" / "boxes"
"""One folder per instance: its ssh config and known hosts, its capture marker and manifest, the copies of its files
outside the repository's runs/, logs/ and out/; and one folder per launch (launch-<launch>/) with what it pinned."""
REMOTE_REPO = "/root/work"
"""The repository's clone on a box."""

LABEL_PREFIX = "spar-screen"
TASKS = {
    "knowledge_work": "knowledge_work",
    "creative_writing": "creative_writing",
    "protein_design": "protein_design",
    "chess": "chess",
    "software_engineering": "swe",
}
"""Each task and its package under examples/, which is also its domain's name in src.domains.DOMAINS."""
MODELS = {"deepseek-flash-openrouter": "dsflash", "gpt-6-luna": "luna", "claude-haiku-5-5": "haiku"}
"""Each model's registry entry and the suffix of its run names."""
KINDS = ("solo", "solo_cheat", "minimal", "minimal_cheat", "minimal_regulated")
"""The cells of a task, as the last part of each example script's name."""
FLOORS = {"software_engineering": (32, 64)}
"""A box's cores and GB of RAM where the task needs more than LIGHT_FLOOR: SWE builds and tests Go on one machine per
agent, and 15 such runs share the box."""
LIGHT_FLOOR = (16, 32)
"""A box's cores and GB of RAM for the other tasks: their agents wait on a model most of the time."""
LAUNCH = re.compile(r"^l[0-9a-f]{6}$")


def boxes(tasks: list[str], models: list[str]) -> list[tuple[str, str]]:
    """One box per (task, model), tasks first."""
    unknown = sorted({*tasks} - TASKS.keys()) + sorted({*models} - MODELS.keys())
    if unknown:
        raise ValueError(f"unknown task or model: {', '.join(unknown)}; tasks {list(TASKS)}, models {list(MODELS)}")
    return [(task, model) for task in tasks for model in models]


def floor_of(task: str) -> tuple[int, int]:
    """The cores and GB of RAM a task's box needs."""
    return FLOORS.get(task, LIGHT_FLOOR)


def new_launch() -> str:
    return "l" + secrets.token_hex(3)


def box_label(prefix: str, task: str, model: str, launch: str) -> str:
    if not LAUNCH.match(launch):
        raise ValueError(f"not a launch id: {launch!r}")
    return f"{prefix}-{task}-{model}-{launch}"


def label_base(label: str) -> str:
    """The label without its launch: the box's identity across launches."""
    base, _, last = label.rpartition("-")
    return base if base and LAUNCH.match(last) else label


def is_fleet_label(label: str | None) -> bool:
    """Whether a label is one fleet.py gives (spar-screen-*): destroy acts on nothing else."""
    return bool(label) and label.startswith(LABEL_PREFIX + "-")


def run_name(cell: str, suffix: str, sample: int) -> str:
    """The run name of sample k of a cell: <stem>_<suffix><k>, as scripts.screen names its runs."""
    return f"{cell.split('.', 1)[1]}_{suffix}{sample}"


def cells(task: str, kinds: tuple[str, ...] = KINDS, examples: Path = ROOT / "examples") -> list[str]:
    """examples.<package>.<stem> without the prefix for each kind, in the order of kinds. Each kind needs exactly one
    script examples/<package>/<stem>_<kind>.py; a missing or doubled cell raises, so a box never runs a partial
    task."""
    package = TASKS[task]
    found, problems = [], []
    for kind in kinds:
        scripts = sorted((examples / package).glob(f"*_{kind}.py"))
        if len(scripts) != 1:
            problems.append(f"{kind}: {len(scripts)} scripts")
        else:
            found.append(f"{package}.{scripts[0].stem}")
    if problems:
        raise ValueError(f"examples/{package} needs one script per kind ({'; '.join(problems)})")
    return found


def layout_problems(tasks: list[str], examples: Path = ROOT / "examples") -> list[str]:
    """Why these tasks cannot run on a fleet: a task without every cell, or a cell stem two tasks share (their run
    names would collide in this repository's runs/, where every box's outputs land)."""
    problems, owner = [], {}
    for task in tasks:
        try:
            task_cells = cells(task, KINDS, examples)
        except ValueError as error:
            problems.append(f"{task}: {error}")
            continue
        for cell in task_cells:
            stem = cell.split(".", 1)[1]
            if stem in owner and owner[stem] != task:
                problems.append(f"{task} and {owner[stem]} both have a cell {stem}: their run names collide")
            owner.setdefault(stem, task)
    return problems
