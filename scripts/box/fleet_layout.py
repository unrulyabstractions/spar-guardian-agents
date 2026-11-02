"""What every box shares: the tasks, the models and their short names, a task's cells and box size, a box's label, its
run names and where things live. study_table.py says which of them this study runs.

    box_label("spar-screen", "gate-protein-luna-1", "l1a2b3c") -> "spar-screen-gate-protein-luna-1-l1a2b3c"
    cells("protein_design", ("minimal",))                      -> ["protein_design.binder_minimal"]
    run_name("protein_design.binder_minimal", "luna", 2)       -> "binder_minimal_luna2"

One box runs one task. A label ends with the launch that rented the box (l and six hex digits), so a launch adopts only
its own instances. It imports the standard library and src/configuration/task_names.py alone, which imports nothing
else, so the Mac and the box both import it.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from src.configuration.task_names import names_of

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "runs" / "boxes.json"
"""Every instance fleet.py created, outside git."""
BOXES = ROOT / "logs" / "boxes"
"""One folder per instance: its ssh config and known hosts, its capture marker and manifest, the copies of its files
outside the repository's runs/, logs/ and out/; and one folder per launch (launch-<launch>/) with what it pinned."""
REMOTE_REPO = "/root/work"
"""The repository's clone on a box."""

LABEL_PREFIX = "spar-screen"
TASKS = {task: names_of(task).domain
         for task in ("knowledge_work", "creative_writing", "protein_design", "chess", "software_engineering")}
"""Each task a box runs, as study_table.py names it, and its package under examples/, which is also its domain's key in
src.domains.DOMAINS (src/configuration/task_names.py). The table names creative writing by its domain key and the
others by CheatBench's task name; names_of reads either."""
STEMS = {"software_engineering": "flipt"}
"""The stem of a task's cells where its package holds several: one box runs one issue."""
MODELS = {"deepseek-flash-openrouter": "dsflash", "gpt-6-luna-openrouter": "luna"}
"""Each worker model's registry entry and the short name that starts its run names' suffix."""
REGULATORS = {"gpt-6-luna-openrouter": "luna", "claude-opus-5-5-openrouter": "opus",
              "gemini-4-argon-openrouter": "argon", "claude-haiku-5-5-openrouter": "haiku",
              "claude-sonnet-5-5-openrouter": "sonnet"}
"""Each regulator model's registry entry and its short name in a run name."""
BOX_KEY = "OPENROUTER_API_KEY"
"""The one model key a box holds, so every model on a box routes through OpenRouter."""
KINDS = ("solo", "solo_cheat", "minimal", "minimal_cheat", "minimal_regulated")
"""The cells every task has, as the last part of each example script's name."""
TASK_KINDS = {"knowledge_work": ("minimal_all",), "chess": ("minimal_all",)}
"""The cells of one task alone: a team with no split."""
FLOORS = {"software_engineering": (32, 64)}
"""A box's cores and GB of RAM where the task needs more than LIGHT_FLOOR: SWE builds and tests Go on one machine per
agent."""
LIGHT_FLOOR = (16, 32)
"""A box's cores and GB of RAM for the other tasks: their agents wait on a model most of the time."""
CAPACITY = {"software_engineering": 21}
"""The agents a box runs at once where the task holds fewer than LIGHT_CAPACITY. Every run of a box starts at once, so
its first builds fall together, and an SWE agent takes 0.55 cores on average and 1.8 at peak. 21 is the most a box at
this floor has run."""
LIGHT_CAPACITY = 36
"""The agents a box at LIGHT_FLOOR runs at once: the most protein design and creative writing have run on one."""
LAUNCH = re.compile(r"^l[0-9a-f]{6}$")


def floor_of(task: str) -> tuple[int, int]:
    """The cores and GB of RAM a task's box needs."""
    return FLOORS.get(task, LIGHT_FLOOR)


def capacity_of(task: str) -> int:
    """The agents a task's box runs at once."""
    return CAPACITY.get(task, LIGHT_CAPACITY)


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


def is_fleet_label(label: str | None) -> bool:
    """Whether a label is one fleet.py gives (spar-screen-*): destroy acts on nothing else."""
    return bool(label) and label.startswith(LABEL_PREFIX + "-")


def run_name(cell: str, suffix: str, sample: int) -> str:
    """The run name of sample k of a cell: <stem>_<suffix><k>, as scripts.screen names its runs."""
    return f"{cell.split('.', 1)[1]}_{suffix}{sample}"


def kinds_of(task: str) -> tuple[str, ...]:
    """The cells a task has."""
    return (*KINDS, *TASK_KINDS.get(task, ()))


def cells(task: str, kinds: tuple[str, ...] | None = None, examples: Path = ROOT / "examples",
          stem: str | None = None) -> list[str]:
    """examples.<package>.<stem> without the prefix for each kind (every kind of the task by default), in the order of
    kinds. stem picks one of a package's several issues (STEMS by default). Each kind needs exactly one script
    examples/<package>/<stem>_<kind>.py: a missing or doubled cell raises."""
    package = TASKS[task]
    found, problems = [], []
    for kind in kinds or kinds_of(task):
        scripts = sorted((examples / package).glob(f"{stem or STEMS.get(task, '*')}_{kind}.py"))
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
            task_cells = cells(task, examples=examples)
        except ValueError as error:
            problems.append(f"{task}: {error}")
            continue
        for cell in task_cells:
            stem = cell.split(".", 1)[1]
            if stem in owner and owner[stem] != task:
                problems.append(f"{task} and {owner[stem]} both have a cell {stem}: their run names collide")
            owner.setdefault(stem, task)
    return problems
