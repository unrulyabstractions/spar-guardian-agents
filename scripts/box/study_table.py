"""study_exploration_1 as a table: the targets, the regulators, the arms of each stage and the boxes that hold them.

    stage_boxes("gate")             -> the stage's boxes, each with its arms
    find_box("gate-flipt-luna-1")   -> one box by name
    box_jobs(box)                   -> one Job per run of the box: its cell, run name, seed, models and flags
    box_jobs(box, mock=True)        -> the first seed of each arm on Inspect's mock model, its run names ending in mock
    jobs_sha256(jobs)               -> the digest the Mac and the box compare
    key_problems(jobs)              -> why the one key a box holds cannot serve these jobs

A target is a task and its workers' model. An arm is one cell of a target under one setting, over some seeds, and it
gives the run name suffix and the flags. A stage is launched by hand and by name: pilot, gate, phase2-<target> or
phase2b-<target>.
Nothing here or anywhere in this folder starts one stage from another, and no stage needs another's boxes.

    pilot             flipt, protein and creative on luna: minimal_regulated, each regulator at ambiguous, seed 1
    gate              every target: minimal and minimal_cheat, seeds 1 to 5
    phase2-<target>   minimal at rule_level generic and identified, and minimal_regulated with each regulator at
                      ambiguous and enforce, seeds 1 to 5; on SWE an Opus regulator takes seed 1 alone
    phase2b-<target>  minimal_regulated with Haiku at ambiguous and enforce, seeds 1 to 5, and Sonnet at both, seed 1
    fixed-pilot       flipt on luna after the SWE fix: minimal_regulated, each of the five regulators at ambiguous,
                      seed 1
    fixed-gate        both flipt targets after the SWE fix: minimal and minimal_cheat, seeds 1 to 3
    fixed-phase2-<target>  a flipt target whose fixed gate passed: minimal at generic and identified, and each of the
                      five regulators at ambiguous and enforce, seeds 1 to 3; Opus and Sonnet take seed 1
    disreg-gate       flipt on Claude Haiku 5.5 workers: minimal and minimal_cheat, seeds 1 to 3. gate_readout.py reads
                      it by its own rule, and the target's disreg and contest stages run only if it passes
    disreg-<target>   flipt on Haiku or GPT-6 Luna workers, or creative writing on GPT-6 Luna workers:
                      minimal_disregulated at persuade and overt, seeds 1 to 3
    contest-<target>  the same targets: minimal_regulated_disregulated, persuade against a Claude Haiku 5.5 regulator at
                      enforce, seeds 1 to 3

Every SWE run before the fix (commits 7331882 to 33b7810, 2026-11-01) graded a task where one agent could cheat
alone, so it measures nothing about collaboration. The fixed gate's and fixed phase two's runs carry the tag `fixed`
and the fixed pilot's the tag `fixedpilot`, so no name repeats an invalid run's.

A box holds whole arms, in table order, up to its task's capacity (fleet_layout.capacity_of).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from scripts.box.fleet_layout import BOX_KEY, MODELS, REGULATORS, capacity_of, cells, run_name
from src.models.model_registry import key_env, resolve

TABLE_STUDY = "study_exploration_1"
"""The study this table states. study_exploration_0 reuses some of its run names, so a reader of the table refuses or
skips any other study."""
MOCK_MODEL = "mockllm/model"
"""What a rehearsal runs: Inspect's mock model, no key and no spend."""
SEEDS = (1, 2, 3, 4, 5)
TEAM = 3
"""The neutral workers of every arm."""
REGULATOR_LEVELS = ("ambiguous", "enforce")
FIRST_REGULATORS = ("gpt-6-luna-openrouter", "claude-opus-5-5-openrouter", "gemini-4-argon-openrouter")
"""The pilot's regulators and phase two's."""
ADDED_REGULATORS = ("claude-haiku-5-5-openrouter", "claude-sonnet-5-5-openrouter")
"""Phase 2b's regulators, on their own boxes so phase two's boxes keep their arms."""
ONE_RUN_ON_SWE = ("claude-opus-5-5-openrouter",)
"""Regulators that take one run, seed 1, per SWE cell after the gate: an Opus regulator costs about $22 a SWE run."""
ONE_RUN = ("claude-sonnet-5-5-openrouter",)
"""Regulators that take one run, seed 1, per cell of every target."""
RULE_LEVELS = ("generic", "identified")


@dataclass(frozen=True)
class Target:
    """A task and its workers' model. short says the task in stage and box names."""

    task: str
    short: str
    model: str
    stem: str | None = None
    """The cells' stem where the task's package holds several issues; None takes fleet_layout.STEMS."""

    @property
    def name(self) -> str:
        return f"{self.short}-{MODELS[self.model]}"


TARGETS = (Target("software_engineering", "flipt", "deepseek-flash-openrouter"),
           Target("software_engineering", "flipt", "gpt-6-luna-openrouter"),
           Target("protein_design", "protein", "gpt-6-luna-openrouter"),
           Target("creative_writing", "creative", "gpt-6-luna-openrouter"))
PILOT_TARGETS = TARGETS[1:]
SWE_TARGETS = TARGETS[:2]
VULS_TARGETS = (Target("software_engineering", "vuls", "deepseek-flash-openrouter", stem="vuls"),
                Target("software_engineering", "vuls", "gpt-6-luna-openrouter", stem="vuls"))
"""SWE's second issue, for generality. Its pieces hold the collaboration rule like flipt's (test_collaboration_swe)."""
SWE_ISSUES = {"vuls": "vuls-1832"}
"""The issue of a target's stem where it is not the box's default (flipt-02e2, which box_setup.sh builds)."""
HAIKU_SWE = Target("software_engineering", "flipt", "claude-haiku-5-5-openrouter")
"""SWE on Claude Haiku 5.5 workers."""
DISREGULATOR_TARGETS = (HAIKU_SWE, TARGETS[3], SWE_TARGETS[1])
"""The targets the disregulator stages run: SWE on Haiku after its disreg-gate passes (it failed on 2026-11-02),
creative writing on GPT-6 Luna, whose five gate runs on each cell already pass that rule, and SWE on GPT-6 Luna, at the
user's word on 2026-11-02: its team stays below `used` untold and never assembles, though it failed the told side."""
DISREGULATOR_LEVELS = ("persuade", "overt")
"""What the disregulator is told: to hide its instruction and win the others over, or to tell the team openly."""
CONTEST_REGULATOR = "claude-haiku-5-5-openrouter"
CONTEST_REGULATOR_LEVEL = "enforce"
CONTEST_DISREGULATOR_LEVEL = "persuade"
FIXED = "fixed"
"""The tag of every run of the SWE task after its fix."""


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
    extra: tuple[tuple[str, str], ...] = ()
    """More key=value settings for this arm alone (a longer time_limit_s, a higher cost_limit_usd). They enter the
    jobs' digest, so they belong on a new stage only: a resumed box refuses a table that changed under it."""
    row: str | None = None
    """The benchmark row, for a cell whose script takes --row (protein's briefs); None runs the script's default. It
    joins the run names, and like extra it belongs on a new stage only."""
    disregulator_level: str | None = None
    """The disregulator's level in a cell that has one. The disregulator runs the workers' model."""

    @property
    def suffix(self) -> str:
        """The run names' suffix: the worker's short name, then what sets this arm apart."""
        regulator = REGULATORS[self.regulator] if self.regulator else None
        parts = (MODELS[self.target.model], self.rule_level, regulator, self.regulator_level, self.disregulator_level,
                 self.row, self.tag)
        return "_".join(part for part in parts if part)

    @property
    def settings(self) -> tuple[str, ...]:
        """The arm's key=value pairs for --set."""
        pairs = {"rule_level": self.rule_level, "regulator_level": self.regulator_level,
                 "disregulator_level": self.disregulator_level}
        return (*(f"{key}={value}" for key, value in pairs.items() if value),
                *(f"{key}={value}" for key, value in self.extra))

    @property
    def agents(self) -> int:
        """The agents of all the arm's runs."""
        return (TEAM + bool(self.regulator) + bool(self.disregulator_level)) * len(self.seeds)


def pilot_arms(target: Target) -> tuple[Arm, ...]:
    return tuple(Arm(target, "minimal_regulated", regulator=regulator, regulator_level="ambiguous", seeds=SEEDS[:1],
                     tag="pilot") for regulator in FIRST_REGULATORS)


def gate_arms(target: Target) -> tuple[Arm, ...]:
    return Arm(target, "minimal"), Arm(target, "minimal_cheat")


def regulated_arms(target: Target, regulators: tuple[str, ...]) -> tuple[Arm, ...]:
    """minimal_regulated with each regulator at each level, over seed 1 alone where the regulator takes one run."""
    def seeds(regulator: str) -> tuple[int, ...]:
        one = regulator in ONE_RUN or (target.task == "software_engineering" and regulator in ONE_RUN_ON_SWE)
        return SEEDS[:1] if one else SEEDS

    return tuple(Arm(target, "minimal_regulated", regulator=regulator, regulator_level=level, seeds=seeds(regulator))
                 for regulator in regulators for level in REGULATOR_LEVELS)


def phase_two_arms(target: Target) -> tuple[Arm, ...]:
    return (*(Arm(target, "minimal", rule_level=level) for level in RULE_LEVELS),
            *regulated_arms(target, FIRST_REGULATORS))


def fixed_pilot_arms(target: Target) -> tuple[Arm, ...]:
    """The pilot of the fixed SWE task: each of the five regulators once, at ambiguous."""
    return tuple(Arm(target, "minimal_regulated", regulator=regulator, regulator_level="ambiguous", seeds=SEEDS[:1],
                     tag=f"{FIXED}pilot") for regulator in (*FIRST_REGULATORS, *ADDED_REGULATORS))


FIXED_SEEDS = SEEDS[:3]
"""Runs per cell on the fixed SWE task: the user's ruling of 2026-11-01, 3 runs instead of 5."""


def fixed_gate_arms(target: Target) -> tuple[Arm, ...]:
    """The gate of the fixed SWE task: minimal and minimal_cheat, 3 runs each."""
    return (Arm(target, "minimal", tag=FIXED, seeds=FIXED_SEEDS),
            Arm(target, "minimal_cheat", tag=FIXED, seeds=FIXED_SEEDS))


def fixed_phase_two_arms(target: Target) -> tuple[Arm, ...]:
    """Phase two of the fixed SWE task, launched only for a target whose fixed gate passed: minimal at rule_level
    generic and identified, and minimal_regulated with each of the five regulators at ambiguous and enforce, 3 runs
    each; an Opus or Sonnet regulator takes one run."""
    def seeds(regulator: str) -> tuple[int, ...]:
        return SEEDS[:1] if regulator in (*ONE_RUN, *ONE_RUN_ON_SWE) else FIXED_SEEDS

    return (*(Arm(target, "minimal", rule_level=level, tag=FIXED, seeds=FIXED_SEEDS) for level in RULE_LEVELS),
            *(Arm(target, "minimal_regulated", regulator=regulator, regulator_level=level, tag=FIXED,
                  seeds=seeds(regulator))
              for regulator in (*FIRST_REGULATORS, *ADDED_REGULATORS) for level in REGULATOR_LEVELS))


FILL = "fill"
"""The tag of the runs that complete the SWE table on DeepSeek workers."""
FILL_TIME_S = 5400
"""The agents' 90 minutes on the fill stages. A GPT-6 Luna regulator reads about five times a round and each round
waits for it, so 100 rounds take about 72 minutes against 48 without a regulator; 7 of its 12 phase-two runs hit
the 60-minute limit (VERIFICATION_LOG.md). The team's 100 rounds are the same; only the wall clock grows."""
FILL_CAPS_USD = {"claude-sonnet-5-5-openrouter": 15, "gemini-4-argon-openrouter": 30}
"""The dollar cap per run where the default would cut a whole run short. The user's yes on the launch sets these. A
GPT-6 Luna run cost about $1, under the default."""


def fill_arm(target: Target, regulator: str, level: str, seeds: tuple[int, ...]) -> Arm:
    cap = FILL_CAPS_USD.get(regulator)
    extra = (("time_limit_s", str(FILL_TIME_S)), *((("cost_limit_usd", str(cap)),) if cap else ()))
    return Arm(target, "minimal_regulated", regulator=regulator, regulator_level=level, tag=FILL, seeds=seeds,
               extra=extra)


def fixed_fill_arms(target: Target) -> tuple[Arm, ...]:
    """What the SWE table lacks on DeepSeek workers: GPT-6 Luna where its run hit the time limit (enforce seed 1
    stands) and Sonnet at both levels to match protein."""
    luna, sonnet = "gpt-6-luna-openrouter", "claude-sonnet-5-5-openrouter"
    return (fill_arm(target, luna, "ambiguous", FIXED_SEEDS), fill_arm(target, luna, "enforce", FIXED_SEEDS[1:]),
            fill_arm(target, sonnet, "ambiguous", SEEDS[:1]), fill_arm(target, sonnet, "enforce", SEEDS[:1]))


def fixed_fill_argon_arms(target: Target) -> tuple[Arm, ...]:
    """Gemini 4 Argon at both levels, 3 runs each, about $15 a run. The user dropped it on 2026-11-02: one run compares
    with nothing, so this stage runs whole or not at all, and only at the user's word."""
    argon = "gemini-4-argon-openrouter"
    return (fill_arm(target, argon, "ambiguous", FIXED_SEEDS), fill_arm(target, argon, "enforce", FIXED_SEEDS))


def swe_tag(target: Target) -> str | None:
    """The tag a new arm takes: `fixed` on SWE, so no name repeats a run from before the fix."""
    return FIXED if target.task == "software_engineering" else None


def disregulated_arms(target: Target) -> tuple[Arm, ...]:
    """Three workers and a disregulator at each level, 3 runs each."""
    return tuple(Arm(target, "minimal_disregulated", disregulator_level=level, seeds=FIXED_SEEDS, tag=swe_tag(target))
                 for level in DISREGULATOR_LEVELS)


def contest_arms(target: Target) -> tuple[Arm, ...]:
    """Three workers, a disregulator and a regulator, 3 runs: does the regulator or the disregulator win?"""
    return (Arm(target, "minimal_regulated_disregulated", regulator=CONTEST_REGULATOR,
                regulator_level=CONTEST_REGULATOR_LEVEL, disregulator_level=CONTEST_DISREGULATOR_LEVEL,
                seeds=FIXED_SEEDS, tag=swe_tag(target)),)


STAGES = {"pilot": {f"pilot-{target.name}": pilot_arms(target) for target in PILOT_TARGETS},
          "gate": {f"gate-{target.name}": gate_arms(target) for target in TARGETS},
          **{f"phase2-{target.name}": {f"phase2-{target.name}": phase_two_arms(target)} for target in TARGETS},
          **{f"phase2b-{target.name}": {f"phase2b-{target.name}": regulated_arms(target, ADDED_REGULATORS)}
             for target in TARGETS},
          "fixed-pilot": {f"fixed-pilot-{SWE_TARGETS[1].name}": fixed_pilot_arms(SWE_TARGETS[1])},
          "fixed-gate": {f"fixed-gate-{target.name}": fixed_gate_arms(target) for target in SWE_TARGETS},
          **{f"fixed-phase2-{target.name}": {f"fixed-phase2-{target.name}": fixed_phase_two_arms(target)}
             for target in SWE_TARGETS},
          "vuls-gate": {f"vuls-gate-{target.name}": fixed_gate_arms(target) for target in VULS_TARGETS},
          f"fixed-fill-{SWE_TARGETS[0].name}": {f"fixed-fill-{SWE_TARGETS[0].name}": fixed_fill_arms(SWE_TARGETS[0])},
          f"fixed-fill-argon-{SWE_TARGETS[0].name}": {f"fixed-fill-argon-{SWE_TARGETS[0].name}":
                                                      fixed_fill_argon_arms(SWE_TARGETS[0])},
          "disreg-gate": {f"disreg-gate-{HAIKU_SWE.name}": fixed_gate_arms(HAIKU_SWE)},
          **{f"disreg-{target.name}": {f"disreg-{target.name}": disregulated_arms(target)}
             for target in DISREGULATOR_TARGETS},
          **{f"contest-{target.name}": {f"contest-{target.name}": contest_arms(target)}
             for target in DISREGULATOR_TARGETS}}
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
    row: str | None = None

    @property
    def flags(self) -> list[str]:
        """What sets this run's setting apart, after its model, name and seed."""
        sets = [flag for pair in self.settings for flag in ("--set", pair)]
        return [*sets, *(["--regulator-model", self.regulator_model] if self.regulator_model else []),
                *(["--row", self.row] if self.row else [])]

    @property
    def arguments(self) -> list[str]:
        return ["--model", self.model, "--name", self.name, "--set", f"seed={self.seed}", *self.flags]


def box_jobs(box: Box, mock: bool = False) -> list[Job]:
    """One Job per run of the box. A mock job runs every role on the mock model and takes the arm's first seed. Its name
    ends in mock, so it never stands in for a real run."""
    jobs = []
    for arm in box.arms:
        cell = cells(arm.target.task, (arm.kind,), stem=arm.target.stem)[0]
        for seed in arm.seeds[:1] if mock else arm.seeds:
            name = run_name(cell, f"{arm.suffix}mock" if mock else arm.suffix, seed)
            jobs.append(Job(cell, name, seed, MOCK_MODEL if mock else arm.target.model,
                            None if mock else arm.regulator, arm.settings, arm.row))
    return jobs


def jobs_sha256(jobs: list[Job]) -> str:
    """A digest of what these jobs run, so a box refuses a table other than the one the Mac listed."""
    return hashlib.sha256(json.dumps([[job.cell, *job.arguments] for job in jobs]).encode()).hexdigest()


def key_problems(jobs: list[Job]) -> list[str]:
    """One line per model of these jobs that needs a key other than BOX_KEY. The mock model needs none."""
    models = sorted({model for job in jobs for model in (job.model, job.regulator_model) if model})
    return [f"{model} needs {' or '.join(key_env(resolve(model)))}, and a box holds only {BOX_KEY}"
            for model in models if key_env(resolve(model)) not in ([], [BOX_KEY])]
