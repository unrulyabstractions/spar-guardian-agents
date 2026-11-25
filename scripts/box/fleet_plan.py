"""A plan's assignment to boxes: each box's targets, its agents at once, and the sum of its runs' dollar caps.

    uv run python -m scripts.box.fleet plan studies/plans/<study>.yaml [--gates studies/gated/<study>.yaml]
                                            [--boxes N] [--max-agents M]

A plan (studies/plans/<study>.yaml) names its targets; run_study launches each target's runs. A group is the targets
that share a box. With a gated spec (--gates) the arms a gate joins (the spec's `when`) form one group per cell, since
a gate reads its earlier arm's stored run on the box that made it, and every arm of the spec counts, open or not.
Without one each target is a group of its own. The groups go to the boxes heaviest first, each to the box with the
fewest agents so far, a run counting its agents once. Two boxes never share a target.

Every box of a launch has one floor, the largest of the plan's tasks (fleet_layout.floor_of). Its agents at once are
--max-agents, else the smaller of AGENTS_PER_CORE agents per core of the floor and MEMORY_SHARE of the floor's memory
over the plan's memory need per agent (limits.needs.<task>.memory_gb). The cap of a run is run_study's: the last
cost_limit_usd of the plan's and the target's settings, else the default.

It reads only this checkout: it rents nothing and sends nothing. Given a commit, it reads the plan and the spec as that
commit holds them, which is what a box's clone runs; without one, the files here.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scripts.box import fleet_layout
from scripts.box.fleet_layout import BOX_KEY, floor_of, from_root
from scripts.study import gated_study, run_study
from scripts.study.run_study import Cell
from src.models.model_registry import key_env, resolve

AGENTS_PER_CORE = 1.4
"""The Mac's figure: it ran 20 agents on Docker's 14 cores (studies/gated/regulator_scale_1.yaml). No box has
measured it."""
MEMORY_SHARE = 0.6
"""The share of a box's memory its agents may take."""


@dataclass(frozen=True)
class BoxPlan:
    """One box of an assignment: its groups of targets, their runs, its agents at once and its floor."""

    name: str
    groups: tuple[tuple[str, ...], ...]
    runs: tuple[Cell, ...]
    max_agents: int
    floor: tuple[int, int]
    settings: tuple[str, ...]

    @property
    def targets(self) -> list[str]:
        return [target for group in self.groups for target in group]

    @property
    def agents(self) -> int:
        """The agents of all the box's runs, each run counted once."""
        return sum(run.n_agents for run in self.runs)

    @property
    def cap_usd(self) -> float:
        """What the box's runs may spend at their caps."""
        return sum(run.cap_usd(list(self.settings)) for run in self.runs)

    @property
    def tasks(self) -> list[str]:
        return sorted({run.task or "" for run in self.runs})


@dataclass(frozen=True)
class Assignment:
    study: str
    plan: str
    """The plan's path from the checkout's root, as the box's clone holds it."""
    gates: str | None
    boxes: tuple[BoxPlan, ...]
    limit_said: str
    """How the boxes' agents at once were found."""

    @property
    def cap_usd(self) -> float:
        return sum(box.cap_usd for box in self.boxes)


def every_target(plan: dict, gates: Path | None, spec_text: str | None = None) -> tuple[dict, list[list[str]]]:
    """The plan with every target a launch may run, and its groups in the plan's order. With a spec (its text as a
    commit holds it, else its file's), every arm of every cell is a target, and the arms a gate joins are one group
    per cell."""
    if gates is None:
        return plan, [[target] for target in plan["targets"]]
    spec = gated_study.load_spec(gates, spec_text)
    if spec.study != plan["study"]:
        raise ValueError(f"{gates} is of study {spec.study}, the plan of {plan['study']}")
    targets, groups = {}, []
    for stem, worker in spec.cells():
        joined = {arm.name: {arm.name} for arm in spec.arms}
        for arm in spec.arms:
            for other in arm.when:
                merged = joined[arm.name] | joined[other]
                for name in merged:
                    joined[name] = merged
        roots, cell_groups = [], []
        for arm in spec.arms:
            name, target = spec.target(stem, worker, arm)
            targets[name] = target
            if joined[arm.name] not in roots:
                roots.append(joined[arm.name])
                cell_groups.append([])
            cell_groups[roots.index(joined[arm.name])].append(name)
        groups += cell_groups
    return {**plan, "seeds": [spec.seed], "targets": targets}, groups


def memory_per_agent(plan: dict, runs: list[Cell]) -> float | None:
    """The plan's memory need per agent: the largest of its tasks' limits.needs (a run's need over its fewest agents);
    None when a task states none."""
    needs, per_agent = plan["limits"].get("needs") or {}, []
    for task in sorted({run.task or "" for run in runs}):
        need = needs.get(task) or {}
        if "memory_gb" not in need:
            return None
        team = min(run.n_agents for run in runs if (run.task or "") == task)
        per_agent.append(need["memory_gb"] / (team if need.get("per", "run") == "run" else 1))
    return max(per_agent)


def agents_at_once(floor: tuple[int, int], per_agent: float | None, max_agents: int | None) -> tuple[int, str]:
    """A box's agents at once and how they were found."""
    if max_agents is not None:
        return max_agents, f"--max-agents {max_agents}"
    if per_agent is None:
        raise ValueError("the plan states no memory need per agent for a task (limits.needs.<task>.memory_gb): add "
                         "it, or pass --max-agents")
    cores, ram_gb = floor
    by_cores, by_memory = int(AGENTS_PER_CORE * cores), int(MEMORY_SHARE * ram_gb / per_agent)
    return min(by_cores, by_memory), (
        f"the smaller of {AGENTS_PER_CORE} per core ({by_cores}; the Mac's figure, no box has measured it) and "
        f"{MEMORY_SHARE:.0%} of {ram_gb} GB at {per_agent:g} GB per agent ({by_memory})")


def box_name(study: str, k: int) -> str:
    """A box's name, which its label carries: the study with hyphens, then its number."""
    return f"{study.replace('_', '-')}-{k}"


def load(plan_path: Path, gates: Path | None, commit: str | None = None) -> tuple[dict, list[tuple[str, ...]],
                                                                               list[Cell]]:
    """The plan with every target a launch may run, its groups and its runs, from the commit's plan and spec, else the
    files here. ValueError for a plan or spec that does not load or the commit lacks, or a closed study."""
    def text(path: Path | None) -> str | None:
        return None if commit is None or path is None else fleet_layout.committed(commit, path)

    plan, groups = every_target(run_study.load_plan(Path(plan_path), text(plan_path)), gates, text(gates))
    if plan.get("closed"):
        raise ValueError(f"{plan_path}: the study is closed, its plan is a record")
    return plan, [tuple(group) for group in groups], run_study.cells(plan)


def assign(plan_path: Path, gates: Path | None = None, boxes: int = 1, max_agents: int | None = None,
           commit: str | None = None) -> Assignment:
    """The plan's targets on `boxes` boxes, the plan and spec read as the commit holds them, else here. ValueError for
    a plan or spec that does not load, fewer groups than boxes, or no way to size the boxes."""
    if boxes < 1 or (max_agents is not None and max_agents < 1):
        raise ValueError("--boxes and --max-agents take a positive number")
    plan, groups, runs = load(plan_path, gates, commit)
    if len(groups) < boxes:
        raise ValueError(f"{len(groups)} groups of targets for {boxes} boxes: a box would run nothing")
    floor = floor_of(sorted({run.task or "" for run in runs}))
    limit, said = agents_at_once(floor, memory_per_agent(plan, runs), max_agents)
    runs_of = {group: [run for run in runs if run.target in group] for group in groups}
    held: list[list[tuple[str, ...]]] = [[] for _ in range(boxes)]

    def agents(k: int) -> int:
        return sum(run.n_agents for group in held[k] for run in runs_of[group])

    for group in sorted(groups, key=lambda group: -sum(run.n_agents for run in runs_of[group])):
        held[min(range(boxes), key=lambda k: (agents(k), k))].append(group)
    out = []
    for k, box_groups in enumerate(held, 1):
        box_groups.sort(key=groups.index)
        out.append(BoxPlan(box_name(plan["study"], k), tuple(box_groups),
                           tuple(run for group in box_groups for run in runs_of[group]), limit, floor,
                           tuple(plan["settings"])))
    return Assignment(plan["study"], from_root(plan_path), from_root(gates) if gates else None, tuple(out), said)


def box_for(plan_path: Path, gates: Path | None, name: str, targets: list[str], max_agents: int,
            commit: str | None = None) -> BoxPlan:
    """The box of a registry entry: these targets of the plan, read again as the commit holds it, else here.
    ValueError for a target the plan lacks or a group the targets cut."""
    plan, groups, runs = load(plan_path, gates, commit)
    mine = [group for group in groups if set(group) & set(targets)]
    cut = [group for group in mine if not set(group) <= set(targets)]
    missing = sorted(set(targets) - {target for group in mine for target in group})
    if cut or missing:
        raise ValueError(f"{plan_path} no longer holds the box's targets as one or more whole groups "
                         f"(missing {missing}, cut {cut})")
    return BoxPlan(name, tuple(mine), tuple(run for run in runs if run.target in targets), max_agents,
                   floor_of(sorted({run.task or "" for run in runs})), tuple(plan["settings"]))


def run_models(run: Cell, settings: tuple[str, ...]) -> set[str]:
    """Every model a run calls: its workers' and each role's (a *_model setting)."""
    pairs = [*settings, *run.settings]
    return {run.model, *(pair.split("=", 1)[1] for pair in pairs if pair.split("=", 1)[0].endswith("_model"))}


def key_problems(box: BoxPlan) -> list[str]:
    """One line per model of the box's runs that needs a key other than BOX_KEY, the one key a box holds."""
    models = sorted({model for run in box.runs for model in run_models(run, box.settings)})
    return [f"{model} needs {' or '.join(key_env(resolve(model)))}, and a box holds only {BOX_KEY}"
            for model in models if key_env(resolve(model)) not in ([], [BOX_KEY])]


def traces_here(study: str, name: str) -> list[str]:
    """What this checkout holds of a run of this name: its run folder runs/<study>/<name>/, an eval log in
    logs/<study>/<name>/, its launcher's console log logs/<study>/run_study/<name>.log. Inspect writes the eval log at
    a run's start, so a run that failed here leaves one."""
    run, logs = run_study.RUNS / study / name, run_study.LOGS / study
    found = [f"runs/{study}/{name}"] if run.exists() else []
    found += [f"logs/{study}/{name}/{log.name}" for log in sorted((logs / name).glob("*.eval"))]
    return found + ([f"logs/{study}/run_study/{name}.log"] if (logs / "run_study" / f"{name}.log").is_file() else [])


def trace_problem(name: str, traces: list[str]) -> str:
    """The refusal of a box run whose name this checkout holds traces of, with its remedy."""
    return (f"{name} has traces here ({', '.join(traces)}): a box would make it again beside them and its capture "
            f"would refuse it. Set them aside first (run_directory.set_aside_attempt moves the run and eval log "
            f"folders into attempts/; move the console log beside them), or leave its target out")


def held_here(study: str, box: BoxPlan) -> dict[str, list[str]]:
    """{run name: its traces here} of the box's runs this checkout holds a trace of (traces_here)."""
    return {run.name: traces for run in box.runs if (traces := traces_here(study, run.name))}


def runs_said(n: int) -> str:
    return f"{n} run" if n == 1 else f"{n} runs"


def lines(assignment: Assignment) -> list[str]:
    """The assignment as printed: the study, then each box with its groups, then the study's total."""
    boxes = assignment.boxes
    runs = sum(len(box.runs) for box in boxes)
    cores, ram_gb = boxes[0].floor
    out = [f"== {assignment.plan}: study {assignment.study}"
           + (f", gates {assignment.gates}" if assignment.gates else "")
           + f"; {sum(len(box.groups) for box in boxes)} groups, {sum(len(box.targets) for box in boxes)} targets, "
           f"{runs} runs on {len(boxes)} boxes",
           f"   each box: at least {cores} cores and {ram_gb} GB; {boxes[0].max_agents} agents at once, "
           f"{assignment.limit_said}"]
    for box in boxes:
        largest = max(run.n_agents for run in box.runs)
        out.append(f"{box.name}: {runs_said(len(box.runs))}, {box.agents} agents in all, {box.max_agents} at once; "
                   f"${box.cap_usd:.2f} at the runs' caps")
        for group in box.groups:
            group_runs = [run for run in box.runs if run.target in group]
            out.append(f"   {' > '.join(group)}  ({runs_said(len(group_runs))}, "
                       f"${sum(run.cap_usd(list(box.settings)) for run in group_runs):.2f})")
        if largest > box.max_agents:
            out.append(f"   !! a run of {largest} agents passes {box.max_agents}: it runs once no agent is live")
        for problem in key_problems(box):
            out.append(f"   !! {problem}")
        for name, traces in held_here(assignment.study, box).items():
            out.append(f"   !! {name} has traces here: {', '.join(traces)}")
    out.append(f"== the study: {runs} runs, ${assignment.cap_usd:.2f} at the runs' caps")
    return out
