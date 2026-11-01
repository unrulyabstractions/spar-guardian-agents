"""study_exploration_1 as a table: the targets, the regulators, the arms of each stage and the boxes that hold them.

    stage_boxes("gate")             -> the stage's boxes, each with its arms
    find_box("gate-flipt-luna-1")   -> one box by name
    box_jobs(box)                   -> one Job per run of the box: its cell, run name, seed, models and flags
    box_jobs(box, mock=True)        -> the first seed of each arm on Inspect's mock model, its run names ending in mock
    jobs_sha256(jobs)               -> the digest the Mac and the box compare
    key_problems(jobs)              -> why the one key a box holds cannot serve these jobs

A target is a task and its workers' model. An arm is one cell of a target under one setting, over some seeds, and it
gives the run name suffix and the flags. A stage is launched by hand and by name: pilot, gate, or phase2-<target>.
Nothing here or anywhere in this folder starts one stage from another, and no stage needs another's boxes.

    pilot             flipt, protein and creative on luna: minimal_regulated, each regulator at ambiguous, seed 1
    gate              every target: minimal and minimal_cheat, seeds 1 to 5
    phase2-<target>   minimal at rule_level generic and identified, and minimal_regulated with each regulator at
                      ambiguous and enforce, seeds 1 to 5

A box holds whole arms, in table order, up to its task's capacity (fleet_layout.capacity_of).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from scripts.box.fleet_layout import BOX_KEY, MODELS, REGULATORS, capacity_of, cells, run_name
from src.models.model_registry import key_env, resolve

MOCK_MODEL = "mockllm/model"
"""What a rehearsal runs: Inspect's mock model, no key and no spend."""
SEEDS = (1, 2, 3, 4, 5)
TEAM = 3
"""The neutral workers of every arm."""
REGULATOR_LEVELS = ("ambiguous", "enforce")
RULE_LEVELS = ("generic", "identified")


@dataclass(frozen=True)
class Target:
    """A task and its workers' model. short says the task in stage and box names."""

    task: str
    short: str
    model: str

    @property
    def name(self) -> str:
        return f"{self.short}-{MODELS[self.model]}"


TARGETS = (Target("software_engineering", "flipt", "deepseek-flash-openrouter"),
           Target("software_engineering", "flipt", "gpt-6-luna-openrouter"),
           Target("protein_design", "protein", "gpt-6-luna-openrouter"),
           Target("creative_writing", "creative", "gpt-6-luna-openrouter"))
PILOT_TARGETS = TARGETS[1:]


@dataclass(frozen=True)
class Arm:
    """One cell of a target under one setting, over some seeds."""

    target: Target
    kind: str
    rule_level: str | None = None
    regulator: str | None = None
    regulator_level: str | None = None
    seeds: tuple[int, ...] = SEEDS
    tag: str | None = None

    @property
    def suffix(self) -> str:
        """The run names' suffix: the worker's short name, then what sets this arm apart."""
        regulator = REGULATORS[self.regulator] if self.regulator else None
        parts = (MODELS[self.target.model], self.rule_level, regulator, self.regulator_level, self.tag)
        return "_".join(part for part in parts if part)

    @property
    def settings(self) -> tuple[str, ...]:
        """The arm's key=value pairs for --set."""
        pairs = {"rule_level": self.rule_level, "regulator_level": self.regulator_level}
        return tuple(f"{key}={value}" for key, value in pairs.items() if value)

    @property
    def agents(self) -> int:
        """The agents of all the arm's runs."""
        return (TEAM + bool(self.regulator)) * len(self.seeds)


def regulated_arms(target: Target, levels: tuple[str, ...], **fields) -> tuple[Arm, ...]:
    """minimal_regulated with each regulator at each level."""
    return tuple(Arm(target, "minimal_regulated", regulator=regulator, regulator_level=level, **fields)
                 for regulator in REGULATORS for level in levels)


def pilot_arms(target: Target) -> tuple[Arm, ...]:
    return regulated_arms(target, ("ambiguous",), seeds=SEEDS[:1], tag="pilot")


def gate_arms(target: Target) -> tuple[Arm, ...]:
    return Arm(target, "minimal"), Arm(target, "minimal_cheat")


def phase_two_arms(target: Target) -> tuple[Arm, ...]:
    return (*(Arm(target, "minimal", rule_level=level) for level in RULE_LEVELS),
            *regulated_arms(target, REGULATOR_LEVELS))


STAGES = {"pilot": {f"pilot-{target.name}": pilot_arms(target) for target in PILOT_TARGETS},
          "gate": {f"gate-{target.name}": gate_arms(target) for target in TARGETS},
          **{f"phase2-{target.name}": {f"phase2-{target.name}": phase_two_arms(target)} for target in TARGETS}}
"""Each stage's arms, by the stem of their boxes' names."""


@dataclass(frozen=True)
class Box:
    """One rented box: whole arms of one task."""

    name: str
    stage: str
    arms: tuple[Arm, ...]

    @property
    def task(self) -> str:
        return self.arms[0].target.task

    @property
    def agents(self) -> int:
        """The agents the box runs at once: all its runs start together."""
        return sum(arm.agents for arm in self.arms)


def pack(stem: str, stage: str, arms: tuple[Arm, ...]) -> list[Box]:
    """Whole arms onto boxes in table order, each on the first box with room. An arm stays whole, so a box's listing
    reads as a setting and its seeds."""
    groups: list[list[Arm]] = []
    for arm in arms:
        room = capacity_of(arm.target.task)
        group = next((group for group in groups if sum(a.agents for a in group) + arm.agents <= room), None)
        if group is None:
            groups.append([arm])
        else:
            group.append(arm)
    return [Box(f"{stem}-{n}", stage, tuple(group)) for n, group in enumerate(groups, 1)]


def stage_boxes(stage: str) -> list[Box]:
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; choose one of {list(STAGES)}")
    return [box for stem, arms in STAGES[stage].items() for box in pack(stem, stage, arms)]


def find_box(name: str) -> Box:
    box = next((box for stage in STAGES for box in stage_boxes(stage) if box.name == name), None)
    if box is None:
        raise ValueError(f"no box {name!r} in the study")
    return box


@dataclass(frozen=True)
class Job:
    """One run: python -m examples.<cell> <arguments>."""

    cell: str
    name: str
    seed: int
    model: str
    regulator_model: str | None
    settings: tuple[str, ...]

    @property
    def flags(self) -> list[str]:
        """What sets this run's setting apart, after its model, name and seed."""
        sets = [flag for pair in self.settings for flag in ("--set", pair)]
        return [*sets, *(["--regulator-model", self.regulator_model] if self.regulator_model else [])]

    @property
    def arguments(self) -> list[str]:
        return ["--model", self.model, "--name", self.name, "--set", f"seed={self.seed}", *self.flags]


def box_jobs(box: Box, mock: bool = False) -> list[Job]:
    """One Job per run of the box. A mock job runs every role on the mock model and takes the arm's first seed. Its name
    ends in mock, so it never stands in for a real run."""
    jobs = []
    for arm in box.arms:
        cell = cells(arm.target.task, (arm.kind,))[0]
        for seed in arm.seeds[:1] if mock else arm.seeds:
            name = run_name(cell, f"{arm.suffix}mock" if mock else arm.suffix, seed)
            jobs.append(Job(cell, name, seed, MOCK_MODEL if mock else arm.target.model,
                            None if mock else arm.regulator, arm.settings))
    return jobs


def jobs_sha256(jobs: list[Job]) -> str:
    """A digest of what these jobs run, so a box refuses a table other than the one the Mac listed."""
    return hashlib.sha256(json.dumps([[job.cell, *job.arguments] for job in jobs]).encode()).hexdigest()


def key_problems(jobs: list[Job]) -> list[str]:
    """One line per model of these jobs that needs a key other than BOX_KEY. The mock model needs none."""
    models = sorted({model for job in jobs for model in (job.model, job.regulator_model) if model})
    return [f"{model} needs {' or '.join(key_env(resolve(model)))}, and a box holds only {BOX_KEY}"
            for model in models if key_env(resolve(model)) not in ([], [BOX_KEY])]
