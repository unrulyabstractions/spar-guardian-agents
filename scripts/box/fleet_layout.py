"""What the fleet runs: the tasks, the models, the cells of a task, a box's label and where things live.

    boxes(["chess"], ["gpt-6-luna", "claude-haiku-5-5"])  -> [("chess", "gpt-6-luna"), ("chess", "claude-haiku-5-5")]
    box_label("spar-screen", "chess", "gpt-6-luna")      -> "spar-screen-chess-gpt-6-luna"
    cells("knowledge_work")                              -> ["knowledge_work.worktime_solo", ...], one per kind

One box runs one task on one model: the task's cells, one example script per kind, each several samples at once.
Standard library only, so every side imports it: the Mac (fleet.py, box_capture.py) and the box (box_runner.py).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "runs" / "boxes.json"
"""Every instance fleet.py created, outside git."""
BOXES = ROOT / "logs" / "boxes"
"""One folder per instance: its ssh config and known hosts, its capture marker, the copies of its files outside the
repository's runs/, logs/ and out/."""
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
"""Each task and its package under examples/."""
MODELS = {"deepseek-flash-openrouter": "dsflash", "gpt-6-luna": "luna", "claude-haiku-5-5": "haiku"}
"""Each model's registry entry and the suffix of its run names."""
KINDS = ("solo", "solo_cheat", "minimal", "minimal_cheat", "minimal_regulated")
"""The cells of a task, as the last part of each example script's name."""


def boxes(tasks: list[str], models: list[str]) -> list[tuple[str, str]]:
    """One box per (task, model), tasks first."""
    unknown = sorted({*tasks} - TASKS.keys()) + sorted({*models} - MODELS.keys())
    if unknown:
        raise ValueError(f"unknown task or model: {', '.join(unknown)}; tasks {list(TASKS)}, models {list(MODELS)}")
    return [(task, model) for task in tasks for model in models]


def box_label(prefix: str, task: str, model: str) -> str:
    return f"{prefix}-{task}-{model}"


def is_fleet_label(label: str | None) -> bool:
    """Whether a label is one fleet.py gives (spar-screen-*): destroy acts on nothing else."""
    return bool(label) and label.startswith(LABEL_PREFIX + "-")


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
