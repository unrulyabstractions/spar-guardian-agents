"""A gated study: an arm's cells run only where the cell's earlier arms came out a given way.

    uv run python -m scripts.study.gated_study studies/gated/ian_influencers_0.yaml           # the gate table
    uv run python -m scripts.study.gated_study studies/gated/ian_influencers_0.yaml --write   # and the launch plan
    uv run python -m scripts.study.run_study studies/plans/ian_influencers_0.yaml --dry-run   # what it would launch

A spec (studies/gated/<study>.yaml) names the study, the seed, the seed pack's name, the tasks, the workers and the
arms in order:

    study: ian_influencers_0
    seed: 1
    pack: revealing           # <task>/<pack>, the task being the cell module's folder
    tasks:
      ian_flipt: {cell: studies.cells.swe.ian_flipt_minimal, cheat: studies.cells.swe.ian_flipt_minimal_cheat}
    workers:
      deepseek: deepseek-flash-openrouter
      qwen: {model: qwen3.8-flash-openrouter, pack: none}   # a worker's own pack, in place of the spec's
    arms:
      baseline: {pack: none}
      seeded: {}
      negative_control: {settings: [rule_level=generic], when: {seeded: cheated}}
      positive_control: {cell: cheat, when: {seeded: not_cheated}}
      larger: {settings: [n_agents=10], stage: 1}
    settings: [time_limit_s=18000]                                    # optional, the plan's: for every target
    limits: {max_runs: 4, max_per_task: {swe: 3}, spacing_s: 30}      # optional, the plan's

A **cell** is a task and a worker. An arm's target for a cell is named `<stem>_<worker>_<arm>`, and run_study names its
run `<target>_<pack>_s<seed>`. An arm runs the task's cell module, or its told-to-cheat module with `cell: cheat`, with
the pack unless `pack: none`, and with its own `settings` (--set pairs, after the plan's). A worker given as
{model, pack} takes its own pack in place of the spec's, `none` for no seed pack at all. A task's `cheat` module may be
left out when no arm runs `cell: cheat`. An arm's `stage` becomes its targets' stage in the plan, which run_study
launches in order (lower first). `when` gates the arm: every
condition must hold on the same cell's run of the earlier arm it names. Three conditions read the team's Cheating rung
from the run's stored cheating.json (no judge, no model call): `cheated` (attempted or above,
cheating_ladder.cheated), `not_cheated` (below attempted) and `hacked` (the top rung of rungs.RUNGS).

A cell's arm is in one of these states:
- a rung: its run exists, is valid (run_validity) and its cheating.json holds the team's rung;
- failed: its run exists and is not valid, or sits in a group of the study (invalid, incomplete, mock, set_aside);
- no rung: its run is valid and its cheating.json is missing or holds no team rung;
- open: it has no run and every condition holds;
- closed: a condition fails, or names an arm that is closed for the cell and has no run;
- waiting: no condition fails and one is undecided: its arm has no run yet, failed or has no rung.

The tool prints one table, cells down and arms across, then one line per arm. With --write it writes the launch plan
studies/plans/<study>.yaml, in run_study's format, with every target that is open or has a run, and a header with the
date and the table. It refuses to overwrite a plan it did not write. It never launches anything: run_study launches
what is missing, and the gate tool run again after those runs opens the next arms.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from scripts.study import run_study
from src.configuration import seed_packs, settings
from src.configuration.swarm_config import SwarmConfig
from src.measures import run_validity
from src.measures.cheating_ladder import cheated
from src.measures.run_directory import ATTEMPTS, GROUPS, RunDir
from src.measures.rungs import RUNGS
from src.models import model_registry as models
from src.utils.json_files import read_json

SPEC_KEYS = {"study", "seed", "pack", "tasks", "workers", "arms"}
SPEC_OPTIONAL = {"settings", "limits", "closed"}
"""closed: true marks a study that is over: the spec is kept as a record, its pack may have been renamed since, and
the plan written from it is closed, so nothing launches from it (run_study)."""
TASK_KEYS = {"cell", "cheat"}
"""cell is needed; cheat only when an arm runs `cell: cheat`."""
WORKER_KEYS = {"model", "pack"}
"""A worker is a registry entry, or {model: <entry>, pack: <pack>} with pack optional."""
ARM_KEYS = {"pack", "cell", "settings", "when", "stage"}
HEADER = "# Written by scripts.study.gated_study from {spec}: do not edit, write it again."
"""The plan's first line. --write overwrites only a plan whose first line starts as this one does."""
FAILED, NO_RUNG, OPEN, CLOSED, WAITING = "failed", "no rung", "open", "closed", "waiting"


def is_cheated(rung: str) -> bool:
    """The team cheated: its rung is attempted or above."""
    return cheated(rung) is True


def is_not_cheated(rung: str) -> bool:
    """The team did not cheat: its rung is below attempted."""
    return cheated(rung) is False


def is_hacked(rung: str) -> bool:
    """The team hacked: its rung is the Cheating ladder's top."""
    return rung == RUNGS[-1]


CONDITIONS: dict[str, Callable[[str], bool]] = {"cheated": is_cheated, "not_cheated": is_not_cheated,
                                                "hacked": is_hacked}


@dataclass(frozen=True)
class Arm:
    name: str
    pack: bool = True                 # False: no seed pack
    cheat: bool = False               # True: the task's told-to-cheat module
    settings: tuple[str, ...] = ()
    when: dict[str, str] = field(default_factory=dict)
    stage: int = 0                    # the plan's stage of the arm's targets


@dataclass(frozen=True)
class Spec:
    path: Path
    study: str
    seed: int
    pack: str
    tasks: dict[str, dict[str, str]]  # stem -> {cell[, cheat]}
    workers: dict[str, str]           # short name -> registry entry
    arms: list[Arm]
    limits: dict | None
    closed: bool = False              # a record of a finished study
    worker_packs: dict[str, str] = field(default_factory=dict)   # short name -> its own pack, for those that name one
    settings: tuple[str, ...] = ()    # the study's own settings, for every target (the plan's `settings`)

    def pack_of(self, worker: str) -> str:
        """The worker's seed pack name: its own, else the spec's. "none" is no pack."""
        return self.worker_packs.get(worker, self.pack)

    def cells(self) -> list[tuple[str, str]]:
        """Every (stem, worker), task by task, worker by worker."""
        return [(stem, worker) for stem in self.tasks for worker in self.workers]

    def target(self, stem: str, worker: str, arm: Arm) -> tuple[str, dict]:
        """The plan's target of one cell's arm: its name and {module, model, packs[, settings]}."""
        task = self.tasks[stem]
        module = task["cheat" if arm.cheat else "cell"]
        pack = self.pack_of(worker) if arm.pack else "none"
        spec = {"module": module, "model": self.workers[worker],
                "packs": [f"{run_study.module_task(module)}/{pack}" if pack != "none" else "none"]}
        if arm.settings:
            spec["settings"] = list(arm.settings)
        if arm.stage:
            spec["stage"] = arm.stage
        return f"{stem}_{worker}_{arm.name}", spec

    def run_name(self, stem: str, worker: str, arm: Arm) -> str:
        """The run's name as run_study gives it: <target>_<pack>_s<seed>."""
        name, spec = self.target(stem, worker, arm)
        return run_study.Cell(name, spec["packs"][0], self.seed, spec["module"], spec["model"], None).name

    def run_names(self) -> set[str]:
        """The name of every run the spec may make: each cell's run of each arm."""
        return {self.run_name(stem, worker, arm) for stem, worker in self.cells() for arm in self.arms}


def load_spec(path: Path, text: str | None = None) -> Spec:
    """The spec, checked: its keys, its names, its modules and packs, its arms' settings and gates. text is the spec as
    a commit holds it, in place of the file's."""
    raw = yaml.safe_load(Path(path).read_text() if text is None else text) or {}

    def refuse(why: str) -> ValueError:
        return ValueError(f"{path}: {why}")

    if not SPEC_KEYS <= set(raw) <= SPEC_KEYS | SPEC_OPTIONAL:
        raise refuse(f"a spec needs {sorted(SPEC_KEYS)} and may take {sorted(SPEC_OPTIONAL)}")
    if not isinstance(raw["seed"], int) or not run_study.NAME.fullmatch(str(raw["study"])):
        raise refuse("seed is an integer and study a name of a-z, 0-9 and _")
    workers, worker_packs = {}, {}
    for worker, entry in raw["workers"].items():
        entry = entry if isinstance(entry, dict) else {"model": entry}
        if not run_study.NAME.fullmatch(worker) or not {"model"} <= set(entry) <= WORKER_KEYS \
                or not all(isinstance(value, str) for value in entry.values()):
            raise refuse(f"worker {worker!r} needs a name of a-z, 0-9 and _, and a registry entry or "
                         f"{{model: <entry>, pack: <pack>}}")
        models.resolve(entry["model"])
        workers[worker] = entry["model"]
        if "pack" in entry:
            worker_packs[worker] = entry["pack"]
    packs = {raw["pack"], *worker_packs.values()} - {"none"}
    for stem, task in raw["tasks"].items():
        if not run_study.NAME.fullmatch(stem) or not {"cell"} <= set(task) <= TASK_KEYS:
            raise refuse(f"task {stem!r} needs a name of a-z, 0-9 and _, and {sorted(TASK_KEYS)} (cheat optional)")
        for module in task.values():
            if not (settings.ROOT / Path(*module.split("."))).with_suffix(".py").is_file():
                raise refuse(f"{stem}'s module {module} is no script of this checkout")
            for pack in packs if not raw.get("closed") else ():
                seed_packs.pack_of(f"{run_study.module_task(module)}/{pack}")
    arms: list[Arm] = []
    for name, arm in raw["arms"].items():
        arm = arm or {}
        if not run_study.NAME.fullmatch(name) or set(arm) - ARM_KEYS:
            raise refuse(f"arm {name!r} needs a name of a-z, 0-9 and _, and takes {sorted(ARM_KEYS)}")
        if arm.get("pack", "default") not in ("default", "none") or arm.get("cell", "cheat") != "cheat":
            raise refuse(f"arm {name}: pack takes none alone, cell takes cheat alone")
        if "cell" in arm and not all("cheat" in task for task in raw["tasks"].values()):
            raise refuse(f"arm {name} runs cell: cheat, so every task needs its cheat module")
        stage = arm.get("stage", 0)
        if not isinstance(stage, int) or stage < 0:
            raise refuse(f"arm {name}: stage is an integer of 0 or more")
        pairs = checked_pairs(arm.get("settings"), f"arm {name}", refuse)
        when = dict(arm.get("when") or {})
        earlier = {a.name for a in arms}
        for other, condition in when.items():
            if other not in earlier or condition not in CONDITIONS:
                raise refuse(f"arm {name}: when names an earlier arm ({sorted(earlier)}) and one of "
                             f"{sorted(CONDITIONS)}, got {other}: {condition}")
        arms.append(Arm(name, arm.get("pack") != "none", "cell" in arm, tuple(pairs), when, stage))
    return Spec(Path(path), raw["study"], raw["seed"], raw["pack"], raw["tasks"], workers, arms,
                raw.get("limits"), bool(raw.get("closed")), worker_packs,
                tuple(checked_pairs(raw.get("settings"), "the spec", refuse)))


def checked_pairs(raw: list | None, where: str, refuse) -> list[str]:
    """A list of settings as "key=value" strings: every key a SwarmConfig field, every model a known one."""
    pairs = [str(pair) for pair in raw or []]
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or key not in SwarmConfig.model_fields:
            raise refuse(f"{where}: a setting is a SwarmConfig field as key=value, got {pair!r}")
        if key.endswith("_model"):
            models.resolve(value)
    return pairs


@dataclass(frozen=True)
class Outcome:
    state: str                  # "run" (a rung), FAILED, NO_RUNG, or "missing"
    rung: str | None = None
    why: str = ""


def outcome(study_dir: Path, name: str, validity: Callable[[Path], dict] = run_validity.validity) -> Outcome:
    """What the study holds of one run: a valid run's team rung, a failed run, a valid run without a rung, or none."""
    run = RunDir(Path(study_dir) / name)
    if not run.is_run():
        groups = [group for group in GROUPS if group != ATTEMPTS and RunDir(Path(study_dir) / group / name).is_run()]
        return Outcome(FAILED, why=f"in {groups[0]}/") if groups else Outcome("missing")
    record = validity(run.path)
    if not record.get("valid"):
        return Outcome(FAILED, why=record.get("reason") or "not valid")
    stored = read_json(run.file("cheating.json")) or {}
    rung = stored.get("team") if stored.get("applicable", True) else None
    if rung not in RUNGS:
        return Outcome(NO_RUNG, why=stored.get("reason") or "no cheating.json")
    return Outcome("run", rung)


@dataclass
class Gates:
    """Every cell's arms: what the study holds and the gate's state."""
    spec: Spec
    outcomes: dict[tuple[str, str, str], Outcome]   # (stem, worker, arm) -> Outcome
    states: dict[tuple[str, str, str], str]         # (stem, worker, arm) -> a rung, FAILED, NO_RUNG, OPEN, ...

    def shown(self, stem: str, worker: str, arm: str) -> str:
        return self.states[stem, worker, arm]

    def has_run(self, key: tuple[str, str, str]) -> bool:
        return self.outcomes[key].state != "missing"


def gate(spec: Spec, arm: Arm, stem: str, worker: str, outcomes: dict, states: dict) -> str:
    """OPEN, CLOSED or WAITING for one cell's arm: every condition on the earlier arms' runs must hold."""
    undecided = False
    for other, condition in arm.when.items():
        held = outcomes[stem, worker, other]
        if held.state == "run":
            if not CONDITIONS[condition](held.rung):
                return CLOSED
        elif held.state == "missing" and states[stem, worker, other] == CLOSED:
            return CLOSED
        else:
            undecided = True
    return WAITING if undecided else OPEN


def decide(spec: Spec, study_dir: Path, validity: Callable[[Path], dict] = run_validity.validity) -> Gates:
    """Every cell's arms, in the spec's order, so a gate reads the earlier arms' states."""
    outcomes: dict[tuple[str, str, str], Outcome] = {}
    states: dict[tuple[str, str, str], str] = {}
    for stem, worker in spec.cells():
        for arm in spec.arms:
            key = (stem, worker, arm.name)
            outcomes[key] = outcome(study_dir, spec.run_name(stem, worker, arm), validity)
            held = outcomes[key]
            gated = gate(spec, arm, stem, worker, outcomes, states)
            states[key] = {"run": held.rung, FAILED: FAILED, NO_RUNG: NO_RUNG}.get(held.state, gated)
    return Gates(spec, outcomes, states)


def chain_lengths(gates: Gates) -> dict[str, int]:
    """Each run name of the spec -> the number of arms on the longest chain that can still follow it in its cell. An
    arm follows another when its gate names it, and it can still run when it is open or waiting."""
    spec, out = gates.spec, {}
    for stem, worker in spec.cells():
        depth: dict[str, int] = {}
        for arm in reversed(spec.arms):
            later = [other for other in spec.arms if arm.name in other.when
                     and gates.states[stem, worker, other.name] in (OPEN, WAITING)]
            depth[arm.name] = max((1 + depth[other.name] for other in later), default=0)
            out[spec.run_name(stem, worker, arm)] = depth[arm.name]
    return out


def table(gates: Gates) -> list[str]:
    """The gate table, cells down and arms across, then one line per arm and one per run that decides nothing."""
    spec = gates.spec
    arms = [arm.name for arm in spec.arms]
    rows = [[f"{stem}_{worker}", *(gates.shown(stem, worker, arm) for arm in arms)] for stem, worker in spec.cells()]
    head = ["cell", *arms]
    widths = [max(len(row[i]) for row in [head, *rows]) for i in range(len(head))]
    lines = ["  ".join(text.ljust(width) for text, width in zip(row, widths)).rstrip() for row in [head, *rows]]
    lines.append("")
    for arm in spec.arms:
        shown = [gates.shown(stem, worker, arm.name) for stem, worker in spec.cells()]
        counts = {state: shown.count(state) for state in (OPEN, CLOSED, WAITING, FAILED, NO_RUNG)}
        ran = len(shown) - sum(counts.values())
        gate_text = ", ".join(f"{other} {condition}" for other, condition in arm.when.items()) or "every cell"
        lines.append(f"{arm.name}: {ran} ran, " + ", ".join(f"{n} {state}" for state, n in counts.items())
                     + f" (gate: {gate_text})")
    for (stem, worker, arm), held in gates.outcomes.items():
        if held.state in (FAILED, NO_RUNG):
            name = spec.run_name(stem, worker, next(a for a in spec.arms if a.name == arm))
            lines.append(f"{name}: {held.state} ({held.why}); the arms gated on it wait")
    return lines


def plan(gates: Gates) -> dict:
    """The launch plan in run_study's format: every target that is open or has a run, arm by arm, worker by worker,
    task by task, so the tasks alternate in the launch queue."""
    spec = gates.spec
    targets = {}
    for arm in spec.arms:
        for worker in spec.workers:
            for stem in spec.tasks:
                key = (stem, worker, arm.name)
                if gates.states[key] == OPEN or gates.has_run(key):
                    name, target = spec.target(stem, worker, arm)
                    targets[name] = target
    out = {"study": spec.study, "seeds": [spec.seed], "targets": targets}
    if spec.settings:
        out["settings"] = list(spec.settings)
    if spec.limits:
        out["limits"] = spec.limits
    if spec.closed:
        out["closed"] = True
    return out


def plan_text(gates: Gates, today: dt.date | None = None) -> str:
    """The plan file: the header (who wrote it, the date, the gate table), then the plan."""
    spec, today = gates.spec, today or dt.date.today()
    where = spec.path.resolve()
    shown = where.relative_to(settings.ROOT).as_posix() if where.is_relative_to(settings.ROOT) else str(spec.path)
    head = [HEADER.format(spec=shown),
            f"# {today.isoformat()}: the gates of {spec.study} on its stored runs:", "#",
            *(f"#   {line}".rstrip() for line in table(gates))]
    body = yaml.safe_dump(plan(gates), sort_keys=False, default_flow_style=None, width=120)
    return "\n".join(head) + "\n" + body


def write_plan(gates: Gates, out: Path, today: dt.date | None = None) -> Path:
    """Write the plan to out; refuse a file there that this tool did not write."""
    out = Path(out)
    if out.is_file() and not out.read_text().startswith(HEADER.split("{")[0]):
        raise ValueError(f"{out} exists and was not written by this tool: move it or name another --out")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(plan_text(gates, today))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.study.gated_study", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("spec", type=Path, help="a gated study's spec, e.g. studies/gated/ian_influencers_0.yaml")
    parser.add_argument("--write", action="store_true", help="write the launch plan")
    parser.add_argument("--out", type=Path, help="the plan's path (default studies/plans/<study>.yaml)")
    parser.add_argument("--runs", type=Path, help="the folder of the study's runs (default runs/<study>)")
    args = parser.parse_args(argv)
    try:
        spec = load_spec(args.spec)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    study_dir = args.runs or settings.ROOT / "runs" / spec.study
    gates = decide(spec, study_dir)
    print(f"{spec.study}: {len(spec.cells())} cells, {len(spec.arms)} arms, runs from {study_dir}")
    print("\n".join(table(gates)))
    if args.write:
        out = args.out or settings.ROOT / "studies" / "plans" / f"{spec.study}.yaml"
        try:
            write_plan(gates, out)
        except ValueError as error:
            print(error, file=sys.stderr)
            return 2
        targets = run_study.load_plan(out)["targets"]
        print(f"wrote {out}: {len(targets)} targets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
