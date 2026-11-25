"""A seed study from a plan file: every target with every pack, one run per cell and seed, on this host's Docker.

    uv run python -m scripts.study.run_study studies/plans/ian_influencers_0.yaml --dry-run  # cells, commands
    uv run python -m scripts.study.run_study studies/plans/ian_influencers_0.yaml --yes      # launch: paid calls
    uv run python -m scripts.study.run_study <plan> --yes [--gates <spec>] [--only <target> ...]
        [--limit key=value ...] [--mock]

--only runs only the named targets of the plan; with --gates an arm runs only when its whole chain of gates lies among
the named targets. --limit replaces one of the plan's limits for this launch (LIMIT_OPTIONS: max_runs, max_agents,
spacing_s, max_per_task.<task>), again after each read of the plan; an unknown key is refused. --mock gives every model
of every cell, the roles' models too, Inspect's mock model (MOCK_MODEL), runs into the study <study>_mock, reads no
key and pays nothing; it takes no --gates. --dry-run shows what each of them changes.

A plan (studies/plans/*.yaml, studies/README.md, seeds/README.md) names the study, the seeds, the targets and their
packs:

    study: ian_seed_prototyping_2
    seeds: [1]
    targets:
      ian_binder_luna:                               # the run's name: <target>_<pack>_s<seed>
        module: studies.cells.protein_design.ian_binder_minimal
        model: gpt-6-luna-openrouter
        packs: [none, protein_design/revealing]   # "none" is the unseeded control
    settings: [time_limit_s=18000]                   # the study's own --set pairs, for every target
    limits: {max_runs: 10, max_per_task: {swe: 4}, spacing_s: 60}

The launcher takes the first queued cell with room: a cell whose task is full (max_per_task) is passed over, so light
cells do not wait behind SWE cells. The queue is ordered by expected remaining chain time, longest first: a cell's
expected minutes (the mean wall time of the study's stored runs of its task and worker model, DEFAULT_RUN_MINUTES
without one) times one plus the number of gated arms that can still follow it. Every 20 seconds it reads the plan
again: new limits apply at once, and targets added since the start (scripts.study.gated_study --write, as gates
open) join the queue. A launcher started while runs of the plan are live adopts them by their `--name` and never
launches them again.

With `--gates <spec.yaml>` the launcher opens the gates itself: at the start and after each run ends it decides the
spec's gates on the stored runs, writes the plan (gated_study's write_plan) and queues the newly open cells at once,
with one GATES line each time. It queues no cell outside the spec. A live run whose console log, eval log folder and
Inspect sample buffer have not changed for STALL_MINUTES is printed once as `STALL <name> <minutes>`; nothing is
killed.

Each cell runs one command. The host (scripts/study/host_facts.py) says how: on the Mac under `zsh -lic`, so the key
comes from ~/.zshrc, and on Linux under `bash -c` with the launcher's own OPENROUTER_API_KEY, refused before the first
launch when it is not set. A mock cell runs under `bash -c` with no key in its environment. --openrouter-key-env NAME
makes the cell pay with the OpenRouter key in the variable NAME (the line then opens with OPENROUTER_API_KEY="${NAME}",
the name and never the key). The key is never on a command line:

    STUDY=<study> uv run python -m <module> --model <model> --no-judge --name <target>_<pack>_s<seed>
        --set seed=<seed> [--set <each of the plan's settings>] [--set <each of the target's settings>]
        --set id_salt=<target>_s<seed> [--set seed_pack=<task>/<pack>]

The launcher adds no setting of its own. A value that neither the plan's `settings` nor the target's names is the
default: SwarmConfig's (src/configuration/swarm_config.py: rounds, calls_per_round, round_time_s, cost_limit_usd) at
the default preset and scale (time_limit_s). So a study's file says everything its runs change. A plan whose runs
would inherit a time limit shorter than their rounds allow is refused until it names time_limit_s.

id_salt is the target's and the seed's, not the pack's, so every pack of a target and seed draws the same IDs, roles and
deal as its control. A cell whose runs/<study>/<name>/run.json exists is skipped (--replace runs it again and sets the
old one aside). The scheduler launches a cell when all of these hold (the pilot's scheduler2.sh): at least
MIN_FREE_MEMORY_PCT of the host's memory free, at least MIN_FREE_DISK_GB free on the disk of Docker's data (or the
task's own floor, limits min_disk_gb), fewer than max_runs running, fewer than
max_per_task[<task>] of the cell's task running, and spacing_s since the
last launch. Each run's console goes to logs/<study>/run_study/<name>.log. It kills nothing; it waits for every run
it launched and exits 1 when any failed.

A run counts its agents: the last `n_agents=` of the plan's and the target's settings, else DEFAULT_AGENTS. With
limits max_agents, a cell launches only when its agents and the agents of every cell script live on this host (any
study's, read from their command lines) fit under it. A cell larger than max_agents launches once no agent is live, so
nothing waits for ever. A cell that does not fit is passed over like one whose task is full. A target's `stage`
(default 0) orders the queue before the critical path: no cell launches while a cell of an earlier stage is queued, so a
plan can hold its largest swarms until every smaller one has started.

A real launch spends money: it needs --yes, and it prints the total of the runs' caps first. A mock launch needs --yes
too, since it starts containers. The rules of CLAUDE.md
(a spend check, live pricing in the first 10 minutes) apply to it as to any paid run.
"""
from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml
from inspect_ai._util.appdirs import inspect_data_dir

from scripts.study import gated_study
from scripts.study.host_facts import HOST
from src.configuration import seed_packs, settings
from src.configuration.presets import build_config
from src.configuration.swarm_config import ROLES
from src.measures.run_directory import RunDir
from src.models.cost_cap import DEFAULT_CAP_USD

MIN_FREE_MEMORY_PCT = 40
MIN_FREE_DISK_GB = 10
LIMITS = {"max_runs": 10, "max_per_task": {}, "spacing_s": 60, "min_disk_gb": {}, "needs": {}, "max_agents": None}
"""min_disk_gb: {task: GB} is a task's own floor of free Docker disk in place of MIN_FREE_DISK_GB: a task whose
containers write little (a poem, a binder) may launch where an SWE run, whose agents each build 1 to 2 GB, may not.
needs: {task: {disk_gb: X, memory_gb: Y}} is what one run of the task takes, or one agent of it with `per: agent`;
scripts.study.preflight reads it. max_agents: the most agents live at once on this host, None for no limit."""
NEED_KEYS = {"disk_gb", "memory_gb", "per"}
PER = ("run", "agent")
DEFAULT_AGENTS = 3
"""A run's agents when no setting names n_agents: the default preset's small scale. A cell module that sets its own
team (ian_flipt_minimal_regulated) is counted at this too."""
DEFAULT_RUN_MINUTES = 15.0
"""A run's expected wall time when the study stores no run of its task."""
STALL_MINUTES = 10
RUNS = settings.ROOT / "runs"
LOGS = settings.ROOT / "logs"
"""Where a study's runs and logs go: runs/<study>/ and logs/<study>/."""
PLAN_KEYS = {"study", "seeds", "targets", "settings", "limits", "closed"}
"""closed: true marks a study that is over: its plan is kept as a record, lists its cells with --dry-run and never
launches (its runs may be named after packs that were renamed since)."""
TARGET_KEYS = {"module", "model", "packs"}
TARGET_OPTIONAL = {"settings", "stage", "seeds"}
"""settings: the target's own --set pairs, after the plan's (a rule level, a regulator's model). stage: an integer,
0 by default; the queue launches the lower stages first. seeds: the target's own seeds, in place of the plan's (one
target of a study run three times, the others once)."""
NAME = re.compile(r"[a-z0-9_]+")
OPENROUTER_KEY = "OPENROUTER_API_KEY"
"""The variable every OpenRouter model's key is read from (src/models/model_registry.yaml)."""
KEY_ENV = re.compile(r"[A-Z][A-Z0-9_]*")
"""A variable's name, as --openrouter-key-env takes it."""
LIMIT_OPTIONS = ("max_runs", "max_agents", "spacing_s", "max_per_task.<task>")
"""The limits --limit may replace for one launch."""
MOCK_MODEL = "mockllm/model"
"""Inspect's mock model, which --mock gives every agent: no key and no spend."""
MOCK_SUFFIX = "_mock"
"""A mock launch's study is the plan's study with this suffix, so its runs never mix with real ones."""


@dataclass
class Cell:
    target: str
    pack: str               # "<task>/<pack>" or "none"
    seed: int
    module: str
    model: str
    task: str | None        # the packs' task, or its module's task folder (a cell without a pack)
    settings: tuple[str, ...] = ()   # the target's own settings, after the plan's
    n_agents: int = DEFAULT_AGENTS   # the run's team, from the plan's and the target's settings
    stage: int = 0                   # the target's stage: lower stages launch first

    @property
    def name(self) -> str:
        return f"{self.target}_{self.pack.split('/')[-1]}_s{self.seed}"

    def argv(self, extra: list[str], replace: bool) -> list[str]:
        """The cell script's arguments: the model, the name, the seed, the plan's settings (`extra`), the target's,
        the salt, then the pack. Nothing else is set, so every other value is the default."""
        out = ["--model", self.model, "--no-judge", "--name", self.name, *(["--replace"] if replace else []),
               "--set", f"seed={self.seed}"]
        for pair in [*extra, *self.settings, f"id_salt={self.target}_s{self.seed}"]:
            out += ["--set", pair]
        if self.pack != "none":
            out += ["--set", f"seed_pack={self.pack}"]
        return out

    def cap_usd(self, extra: list[str]) -> float:
        """The run's dollar cap: the last cost_limit_usd of the plan's and the target's settings, else the default."""
        return float(setting([*extra, *self.settings], "cost_limit_usd") or DEFAULT_CAP_USD)


def setting(pairs: list[str] | tuple[str, ...], key: str) -> str | None:
    """The value of the last `key=value` among the pairs, None when none sets the key."""
    return next((pair.split("=", 1)[1] for pair in reversed(pairs) if pair.split("=", 1)[0] == key), None)


def pack_task(pack: str, plan: dict) -> str:
    """The task of a pack, `<task>/<pack>`. An open plan's pack must exist (seed_packs.pack_of). A closed plan is a
    record: its pack may have been renamed since, so its task is read from its name."""
    return pack.split("/")[0] if plan.get("closed") else seed_packs.pack_of(pack)["task"]


def load_plan(path: Path) -> dict:
    """The plan, checked: its keys, its targets' modules and packs, one task per target."""
    plan = yaml.safe_load(path.read_text()) or {}
    if set(plan) - PLAN_KEYS or not {"study", "seeds", "targets"} <= set(plan):
        raise ValueError(f"{path}: a plan takes {sorted(PLAN_KEYS)} and needs study, seeds and targets")
    if not is_seeds(plan["seeds"]):
        raise ValueError(f"{path}: seeds is a list of integers")
    for target, spec in plan["targets"].items():
        if "seeds" in spec and not is_seeds(spec["seeds"]):
            raise ValueError(f"{path}: {target}'s seeds is a list of integers")
        if not NAME.fullmatch(target) or not TARGET_KEYS <= set(spec) <= TARGET_KEYS | TARGET_OPTIONAL:
            raise ValueError(f"{path}: target {target!r} needs a name of a-z, 0-9 and _, and {sorted(TARGET_KEYS)} "
                             f"(and may take {sorted(TARGET_OPTIONAL)})")
        if not (settings.ROOT / Path(*spec["module"].split("."))).with_suffix(".py").is_file():
            raise ValueError(f"{path}: {target}'s module {spec['module']} is no script of this checkout")
        tasks = {pack_task(p, plan) for p in spec["packs"] if p != "none"}
        if len(tasks) > 1:
            raise ValueError(f"{path}: {target}'s packs are of several tasks: {sorted(tasks)}")
        if not isinstance(spec.get("stage", 0), int) or spec.get("stage", 0) < 0:
            raise ValueError(f"{path}: {target}'s stage is an integer of 0 or more")
    limits = {**LIMITS, **(plan.get("limits") or {})}
    check_limits(path, limits)
    out = {**plan, "settings": [str(pair) for pair in plan.get("settings") or []], "limits": limits}
    if not out.get("closed"):
        check_time_limit(path, out)
    return out


def check_limits(path: Path, limits: dict) -> None:
    """Refuse limits with a key LIMITS does not name, a bad need or a max_agents that is no positive integer."""
    if set(limits) != set(LIMITS):
        raise ValueError(f"{path}: limits takes {sorted(LIMITS)}")
    for task, need in (limits["needs"] or {}).items():
        if set(need) - NEED_KEYS or need.get("per", "run") not in PER:
            raise ValueError(f"{path}: needs of {task} take {sorted(NEED_KEYS)}, per being one of {PER}")
    if limits["max_agents"] is not None and (not isinstance(limits["max_agents"], int) or limits["max_agents"] < 1):
        raise ValueError(f"{path}: max_agents is a positive integer")


def check_time_limit(path: Path, plan: dict) -> None:
    """Refuse an open plan with a target that would inherit a time limit shorter than its rounds allow. A run of R
    rounds of round_time_s each may last R x round_time_s; the default limit is the scale's and is shorter than the
    default rounds. A target whose settings, or the plan's, name time_limit_s is taken at its word."""
    default = build_config("default")
    for target, spec in plan["targets"].items():
        pairs = [*plan["settings"], *(str(pair) for pair in spec.get("settings") or [])]
        if setting(pairs, "time_limit_s") is not None:
            continue
        rounds = int(setting(pairs, "rounds") or default.rounds)
        round_s = int(setting(pairs, "round_time_s") or default.round_time_s)
        if default.time_limit_s < rounds * round_s:
            raise ValueError(f"{path}: {target} names no time_limit_s, and the default {default.time_limit_s} s is "
                             f"less than its {rounds} rounds of {round_s} s ({rounds * round_s} s): add "
                             f"time_limit_s={rounds * round_s} to the plan's settings")


def is_seeds(seeds: object) -> bool:
    """Whether this is a seeds list: one integer or more."""
    return isinstance(seeds, list) and bool(seeds) and all(isinstance(seed, int) for seed in seeds)


def cells(plan: dict) -> list[Cell]:
    """Every cell of the plan: seed by seed, target by target in the plan's order, pack by pack. A target runs the
    seeds it states, else the plan's; the seeds go in the plan's order, then the targets' own further ones."""
    out = []
    seeds_of = {target: spec.get("seeds", plan["seeds"]) for target, spec in plan["targets"].items()}
    for seed in dict.fromkeys([*plan["seeds"], *(seed for seeds in seeds_of.values() for seed in seeds)]):
        for target, spec in plan["targets"].items():
            if seed not in seeds_of[target]:
                continue
            tasks = {pack_task(p, plan) for p in spec["packs"] if p != "none"}
            task = next(iter(tasks), None) or module_task(spec["module"])
            own = tuple(spec.get("settings") or ())
            agents = int(setting([*plan.get("settings", []), *own], "n_agents") or DEFAULT_AGENTS)
            for pack in spec["packs"]:
                out.append(Cell(target, pack, seed, spec["module"], spec["model"], task, own, agents,
                                spec.get("stage", 0)))
    return out


def module_task(module: str) -> str | None:
    """The task of a target without a pack: the task folder its module is in, under examples/ or studies/cells/
    (examples.swe.x and studies.cells.swe.x are swe's), so a baseline cell counts toward its task's limit. On
    2026-11-22 three baselines went uncounted and six SWE runs ran at once, which left Docker's disk 4 GB free."""
    parts = module.split(".")
    if len(parts) == 3 and parts[0] == "examples":
        return parts[1]
    return parts[2] if len(parts) == 4 and parts[:2] == ["studies", "cells"] else None


def command(cell: Cell, study: str, extra: list[str], replace: bool, key_env: str | None = None) -> str:
    """The shell line that runs the cell from the checkout's root. key_env names the variable of the login shell whose
    OpenRouter key the cell pays with (--openrouter-key-env); the line holds the variable's name, never the key."""
    key = f'{OPENROUTER_KEY}="${{{key_env}}}" ' if key_env else ""
    argv = shlex.join(cell.argv(extra, replace))
    return f"{key}STUDY={shlex.quote(study)} exec uv run python -m {cell.module} {argv}"


def limit_overrides(pairs: list[str]) -> dict:
    """The --limit pairs as limits: {key: N}, max_per_task.<task>=N into {"max_per_task": {task: N}}. Each value is
    an integer of 0 or more; a key outside LIMIT_OPTIONS is refused."""
    out: dict = {}
    for pair in pairs:
        key, sep, text = pair.partition("=")
        name, dot, task = key.partition(".")
        value = yaml.safe_load(text) if sep and text else None
        if not sep or (f"{name}.<task>" if dot else name) not in LIMIT_OPTIONS or (dot and not NAME.fullmatch(task)):
            raise ValueError(f"--limit takes {', '.join(LIMIT_OPTIONS)} as key=value, got {pair!r}")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"--limit {pair!r}: the value is an integer of 0 or more")
        if dot:
            out.setdefault("max_per_task", {})[task] = value
        else:
            out[name] = value
    return out


def with_limits(path: Path, plan: dict, overrides: dict) -> dict:
    """The plan with the --limit overrides in place of its own limits. max_per_task names a task of the plan's cells."""
    if not overrides:
        return plan
    tasks = {cell.task for cell in cells(plan)}
    unknown = sorted(set(overrides.get("max_per_task") or {}) - tasks)
    if unknown:
        raise ValueError(f"--limit max_per_task.{unknown[0]}: the plan's tasks are {sorted(map(str, tasks))}")
    limits = {**plan["limits"], **overrides,
              "max_per_task": {**(plan["limits"]["max_per_task"] or {}), **(overrides.get("max_per_task") or {})}}
    check_limits(path, limits)
    return {**plan, "limits": limits}


def mocked(plan: dict) -> dict:
    """The plan with every model Inspect's mock model: each target's model, and each role's model after the target's
    own settings, so no setting of the plan can name a paid model."""
    roles = [f"{role}_model={MOCK_MODEL}" for role in ROLES]
    targets = {target: {**spec, "model": MOCK_MODEL, "settings": [*(spec.get("settings") or []), *roles]}
               for target, spec in plan["targets"].items()}
    return {**plan, "targets": targets}


@dataclass(frozen=True)
class Options:
    """What a launch changes of its plan: the --limit overrides, and --mock."""
    limits: dict
    mock: bool = False


def launch_plan(path: Path, options: Options) -> dict:
    """The plan file read and checked, with this launch's options applied."""
    plan = with_limits(path, load_plan(path), options.limits)
    return mocked(plan) if options.mock else plan


def arm_chain(spec: gated_study.Spec, arm: str) -> set[str]:
    """The arm and every arm its gate names, and theirs, down to the arms with no gate."""
    when = next(a for a in spec.arms if a.name == arm).when
    return {arm}.union(*(arm_chain(spec, other) for other in when))


def gated_runs(spec: gated_study.Spec, only: set[str] | None) -> set[str]:
    """The spec's run names a launch may make: every one, or with --only those whose target and the targets of its
    whole chain of gates are all among the named targets."""
    out = set()
    for stem, worker in spec.cells():
        for arm in spec.arms:
            chain = {spec.target(stem, worker, a)[0] for a in spec.arms if a.name in arm_chain(spec, arm.name)}
            if only is None or chain <= only:
                out.add(spec.run_name(stem, worker, arm))
    return out


def chosen(cell: Cell, only: set[str] | None, allowed: set[str] | None) -> bool:
    """Whether the launch runs the cell: its target among --only, and its run among the spec's with --gates."""
    return (only is None or cell.target in only) and (allowed is None or cell.name in allowed)


def free_disk_gb() -> int | None:
    """Free GB on the disk that holds Docker's data, None when the host cannot say."""
    disk = HOST.docker_disk()
    return disk[1] if disk else None


def task_full(running: dict[str, tuple], cell: Cell, limits: dict) -> str | None:
    """Why the cell's task takes no more runs now (max_per_task), or None."""
    cap = (limits["max_per_task"] or {}).get(cell.task or "")
    if cap is not None and sum(1 for _, c in running.values() if c.task == cell.task) >= cap:
        return f"{cap} {cell.task} runs running"
    return None


def held_all(running: dict[str, tuple], limits: dict, last: float) -> str | None:
    """Why no cell launches now, whatever its task: the runs' limit, the spacing, the host's memory. None otherwise."""
    if len(running) >= limits["max_runs"]:
        return f"{len(running)} runs running"
    if time.time() - last < limits["spacing_s"]:
        return "spacing"
    memory = HOST.free_memory_pct()
    if memory is None or memory < MIN_FREE_MEMORY_PCT:
        return f"memory {memory}% free"
    return None


def disk_short(cell: Cell, limits: dict, disk: int | None) -> str | None:
    """Why Docker's free disk does not take the cell: under its task's floor (limits min_disk_gb, else
    MIN_FREE_DISK_GB), or unknown. None when it does."""
    floor = (limits.get("min_disk_gb") or {}).get(cell.task or "", MIN_FREE_DISK_GB)
    return f"docker disk {disk} GB free" if disk is None or disk < floor else None


def agents_full(live: int | None, cell: Cell, limits: dict) -> str | None:
    """Why the cell's agents do not fit beside the live ones under max_agents, or None. A cell larger than the limit
    fits once no agent is live, so it never waits for ever. live is None when no limit is set."""
    cap = limits.get("max_agents")
    if cap is None or not live or live + cell.n_agents <= cap:
        return None
    return f"{live} agents live, {cell.n_agents} more pass max_agents {cap}"


def live_agents(running: dict[str, tuple], limits: dict) -> int | None:
    """The agents live on this host: the launcher's own runs and every other cell script's (live_runs), each run once.
    None when the plan sets no max_agents, so a plan without one never reads the process table for it."""
    if limits.get("max_agents") is None:
        return None
    others = {name: n for name, n in live_runs().items() if name not in running}
    return sum(cell.n_agents for _, cell in running.values()) + sum(others.values())


def room(running: dict[str, tuple], cell: Cell, limits: dict, last: float) -> str | None:
    """Why the cell cannot launch now, or None when it can."""
    if len(running) >= limits["max_runs"]:
        return f"{len(running)} runs running"
    return task_full(running, cell, limits) or held_all(running, limits, last) or \
        agents_full(live_agents(running, limits), cell, limits) or disk_short(cell, limits, free_disk_gb())


def next_cell(running: dict[str, tuple], queue: list[Cell], limits: dict,
              last: float) -> tuple[Cell | None, str | None]:
    """The first queued cell with room, or why none launches now. The runs' limit, the spacing and memory hold every
    cell. A cell whose task is full, whose agents pass max_agents, or whose task needs more free disk than Docker has,
    is passed over, so a light cell queued behind SWE cells does not wait for them. A cell of a later stage than the
    earliest queued one waits. On 2026-11-23 four runs ran where Docker held 2.6 of 23 GiB, because the head of the
    queue was an SWE cell, and later poem cells waited five minutes on a disk floor only SWE runs need."""
    if why := held_all(running, limits, last):
        return None, why
    disk, live, why = free_disk_gb(), live_agents(running, limits), None
    first = min((cell.stage for cell in queue), default=0)
    for cell in queue:
        held = (f"stage {cell.stage} waits for stage {first}" if cell.stage > first else None) or \
            task_full(running, cell, limits) or agents_full(live, cell, limits) or disk_short(cell, limits, disk)
        if held is None:
            return cell, None
        why = why or held
    return None, why


LIVE = re.compile(r"^(?:uv run python|\S*python[\d.]*) -m (?:studies\.cells|examples)\.\S+ .*?"
                  r"--name ([a-z0-9_]+)(?:\s|$)")
"""A cell script's own process (uv's, then Python's), never a shell whose text quotes one."""
TEAM = re.compile(r"--set n_agents=(\d+)(?:\s|$)")


def live_runs() -> dict[str, int]:
    """The cell scripts running on this host now, from their command lines: each run's name (`--name <run>`) and its
    agents (the last `--set n_agents=N`, else DEFAULT_AGENTS). uv's process and Python's count as one run."""
    found = {}
    for line in HOST.process_lines():
        if m := LIVE.match(line.strip()):
            teams = TEAM.findall(line)
            found[m[1]] = int(teams[-1]) if teams else DEFAULT_AGENTS
    return found


def live_names() -> set[str]:
    """The run names of the cell scripts running on this host now (live_runs): the runs a launcher of this study
    started before this one, which this one adopts and never launches again."""
    return set(live_runs())


def run_minutes(run: RunDir) -> float | None:
    """A stored run's wall time in minutes: its longest episode's wall_time. None when no episode records one."""
    walls = [e["wall_time"] for e in run.episodes() if isinstance(e.get("wall_time"), (int, float))]
    return max(walls) / 60 if walls else None


def run_key(run: RunDir) -> tuple[str | None, str | None]:
    """(task, worker model) of a stored run: its launch module's task folder and its model entry."""
    return module_task((run.run_json.get("launch") or {}).get("module") or ""), run.run_json.get("model_key")


def stored_minutes(study_dir: Path) -> dict[tuple[str | None, str | None], float]:
    """The mean wall time in minutes of the study's stored runs at its top level, by (task, worker model)."""
    found: dict[tuple, list[float]] = {}
    for path in sorted(Path(study_dir).iterdir()) if Path(study_dir).is_dir() else []:
        run = RunDir(path)
        if run.is_run() and (minutes := run_minutes(run)) is not None:
            found.setdefault(run_key(run), []).append(minutes)
    return {key: sum(values) / len(values) for key, values in found.items()}


def expected_minutes(timings: dict, cell: Cell) -> float:
    """A cell's expected wall time: its task and model's mean, else its task's mean, else DEFAULT_RUN_MINUTES."""
    if (cell.task, cell.model) in timings:
        return timings[cell.task, cell.model]
    same_task = [minutes for (task, _), minutes in timings.items() if task == cell.task]
    return sum(same_task) / len(same_task) if same_task else DEFAULT_RUN_MINUTES


def chain_minutes(cell: Cell, timings: dict, chains: dict[str, int]) -> float:
    """The cell's expected remaining chain time: its run and every arm that can still follow it, one after another."""
    return expected_minutes(timings, cell) * (1 + chains.get(cell.name, 0))


def by_critical_path(queue: list[Cell], timings: dict, chains: dict[str, int]) -> list[Cell]:
    """The queue by stage, then longest expected remaining chain first; equal chains keep their order."""
    return sorted(queue, key=lambda cell: (cell.stage, -chain_minutes(cell, timings, chains)))


def gate_step(spec_path: Path, plan_path: Path, study_dir: Path) -> tuple[dict[str, int], str]:
    """Decide the spec's gates on the stored runs and write the plan: the chain lengths and a line to print."""
    spec = gated_study.load_spec(spec_path)
    gates = gated_study.decide(spec, study_dir)
    gated_study.write_plan(gates, plan_path)
    opened = sum(1 for state in gates.states.values() if state == gated_study.OPEN)
    waiting = sum(1 for state in gates.states.values() if state == gated_study.WAITING)
    return gated_study.chain_lengths(gates), f"{opened} open, {waiting} waiting; wrote {plan_path}"


def new_cells(plan: dict, known: set[str], study: str, replace: bool, only: set[str] | None,
              allowed: set[str] | None) -> list[Cell]:
    """The plan's cells the launch runs (chosen), not seen before and not done (or every one with replace). Each is
    added to known."""
    out = []
    for cell in cells(plan):
        if cell.name in known or not chosen(cell, only, allowed):
            continue
        known.add(cell.name)
        if replace or not RunDir(RUNS / study / cell.name).is_run():
            out.append(cell)
    return out


def progress_files(study: str, name: str) -> list[Path]:
    """What a live run writes as it works: its console log, its eval log folder's files and the Inspect sample buffer
    of each eval log there. Inspect writes the .eval file only at the start and the end, the buffer all along."""
    logs = LOGS / study
    folder = [path for path in (logs / name).rglob("*") if path.is_file()] if (logs / name).is_dir() else []
    buffer = inspect_data_dir("samplebuffer")
    buffered = [path for log in folder if log.suffix == ".eval" for path in buffer.glob(f"*/{log.name}.*")]
    return [logs / "run_study" / f"{name}.log", *folder, *buffered]


def stall_minutes(study: str, name: str, now: float) -> int | None:
    """The minutes since a live run last wrote anything, when that is at least STALL_MINUTES. None otherwise, and
    None when it has written nothing yet."""
    times = [path.stat().st_mtime for path in progress_files(study, name) if path.is_file()]
    minutes = int((now - max(times)) // 60) if times else 0
    return minutes if minutes >= STALL_MINUTES else None


def stamp() -> str:
    """The time of day for the launcher's lines."""
    return time.strftime("%H:%M:%S")


def reload_plan(path: Path, plan: dict, options: Options) -> dict:
    """The plan file read again with the launch's options, or the last plan read when the file is caught mid-write
    or closed."""
    try:
        fresh = launch_plan(path, options)
    except (OSError, ValueError, yaml.YAMLError):
        return plan
    return plan if fresh.get("closed") else fresh


def parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m scripts.study.run_study", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("plan", type=Path, help="a plan file, e.g. studies/plans/ian_influencers_0.yaml")
    parser.add_argument("--study", help="the study to run into instead of the plan's")
    parser.add_argument("--dry-run", action="store_true", help="list the cells and their commands; launch nothing")
    parser.add_argument("--yes", action="store_true", help="launch: the runs make paid model calls")
    parser.add_argument("--replace", action="store_true", help="run a cell whose run exists again (the example's "
                                                                 "--replace sets the old one aside)")
    parser.add_argument("--gates", type=Path, help="a gated spec whose gates the launcher opens itself after each run")
    parser.add_argument("--openrouter-key-env", metavar="NAME", help="the variable of the login shell that holds the "
                        f"OpenRouter key this launch pays with, in place of {OPENROUTER_KEY}")
    parser.add_argument("--only", action="extend", nargs="+", metavar="TARGET",
                        help="run only these targets of the plan; with --gates an arm runs only when its whole chain "
                             "of gates lies among them")
    parser.add_argument("--limit", action="extend", nargs="+", default=[], metavar="KEY=VALUE",
                        help=f"replace one of the plan's limits for this launch: {', '.join(LIMIT_OPTIONS)}")
    parser.add_argument("--mock", action="store_true", help=f"every model {MOCK_MODEL}, the runs into the study "
                        f"<study>{MOCK_SUFFIX}; no key is read and nothing is paid")
    args = parser.parse_args(argv)
    if args.openrouter_key_env and not KEY_ENV.fullmatch(args.openrouter_key_env):
        parser.error("--openrouter-key-env takes a variable's name, such as OPENROUTER_API_KEY_OWN")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    try:
        options = Options(limit_overrides(args.limit), args.mock)
        plan = launch_plan(args.plan, options)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    study = (args.study or plan["study"]) + (MOCK_SUFFIX if args.mock else "")
    only = set(args.only) if args.only else None
    allowed = None                                              # with --gates: the spec's run names alone
    known_targets = set(plan["targets"])
    if args.gates:
        if args.mock:
            print("--mock decides no gates: rehearse a gated plan's targets with --mock --only", file=sys.stderr)
            return 2
        spec = gated_study.load_spec(args.gates)
        if spec.study != study:
            print(f"--gates {args.gates} is of study {spec.study}, the launch of {study}", file=sys.stderr)
            return 2
        allowed = gated_runs(spec, only)
        known_targets |= {spec.target(stem, worker, arm)[0] for stem, worker in spec.cells() for arm in spec.arms}
    if only is not None and (unknown := sorted(only - known_targets)):
        print(f"--only {' '.join(unknown)}: no target of {args.plan}" + (" or of its spec" if args.gates else ""),
              file=sys.stderr)
        return 2
    todo, skipped = [], []
    for cell in cells(plan):
        if not chosen(cell, only, allowed):
            continue
        done = RunDir(RUNS / study / cell.name).is_run()
        (skipped if done and not args.replace else todo).append(cell)
    caps = sorted({cell.cap_usd(plan["settings"]) for cell in todo}) or \
        [float(setting(plan["settings"], "cost_limit_usd") or DEFAULT_CAP_USD)]
    spend = f"none paid: every model is {MOCK_MODEL}" if args.mock else (
        f"at most ${sum(cell.cap_usd(plan['settings']) for cell in todo):.2f} at the runs' caps of "
        f"{' and '.join(f'${cap:.2f}' for cap in caps)}")
    print(f"plan {args.plan}: study {study}, {len(todo) + len(skipped)} cells, {len(skipped)} done already, "
          f"{len(todo)} to run; {spend}")
    for cell in skipped:
        print(f"  skip   {cell.name} (runs/{study}/{cell.name} holds its run)")
    for cell in todo:
        print(f"  {'plan' if args.dry_run else 'queue'}   {cell.name}  ({cell.n_agents} agents, "
              f"${cell.cap_usd(plan['settings']):.2f} cap" + (f", stage {cell.stage})" if cell.stage else ")"))
        print(f"         {command(cell, study, plan['settings'], args.replace, args.openrouter_key_env)}")
    if args.dry_run:
        print(f"  now    memory {HOST.free_memory_pct()}% free (needs {MIN_FREE_MEMORY_PCT}%), docker disk "
              f"{free_disk_gb()} GB free (needs {MIN_FREE_DISK_GB}), agents live {live_agents({}, plan['limits'])}; "
              f"limits {plan['limits']}")
        return 0
    if plan.get("closed"):
        print(f"{args.plan}: this study is closed, its plan is a record: copy it under a new study to run it again",
              file=sys.stderr)
        return 2
    if not args.yes:
        print("a launch makes paid model calls: add --yes, or --dry-run to look", file=sys.stderr)
        return 2
    if not args.mock and (why := HOST.key_problem()):
        print(why, file=sys.stderr)
        return 2
    study_dir = RUNS / study
    chains: dict[str, int] = {}
    if args.gates:
        try:
            chains, said_gates = gate_step(args.gates, args.plan, study_dir)
        except (OSError, ValueError, yaml.YAMLError) as error:
            print(f"--gates: {error}", file=sys.stderr)
            return 2
        print(f"{stamp()} GATES  {said_gates}", flush=True)
    logs = LOGS / study / "run_study"
    logs.mkdir(parents=True, exist_ok=True)
    running: dict[str, tuple[subprocess.Popen | None, Cell]] = {}      # None: a run an earlier launcher started
    failed, last, said = [], 0.0, 0.0
    queue = list(todo)
    known = {cell.name for cell in [*todo, *skipped]}
    timings = stored_minutes(study_dir)
    stalled: set[str] = set()
    while queue or running:
        ended = False
        plan = reload_plan(args.plan, plan, options)           # the plan may gain targets and change its limits
        for cell in new_cells(plan, known, study, args.replace, only, allowed):
            queue.append(cell)
            todo.append(cell)
            print(f"{stamp()} QUEUE  {cell.name} (new in the plan)", flush=True)
        alive = live_names()
        for cell in [c for c in queue if c.name in alive]:      # an earlier launcher's run: adopt it
            queue.remove(cell)
            running[cell.name] = (None, cell)
            print(f"{stamp()} ADOPT  {cell.name} ({len(running)} running, {len(queue)} queued)", flush=True)
        for name, (proc, cell) in list(running.items()):
            if proc is None and name not in alive:
                del running[name]
                made = RunDir(study_dir / name).is_run()
                print(f"{stamp()} END    {name} adopted, {'its run is stored' if made else 'no run'}", flush=True)
                if not made:
                    failed.append(name)
                ended = True
            elif proc is not None and proc.poll() is not None:
                del running[name]
                print(f"{stamp()} END    {name} exit {proc.returncode}", flush=True)
                if proc.returncode:
                    failed.append(name)
                ended = True
        if ended:
            timings = stored_minutes(study_dir)
        if ended and args.gates:                                # open the gates now, and queue what they opened
            try:
                chains, said_gates = gate_step(args.gates, args.plan, study_dir)
                print(f"{stamp()} GATES  {said_gates}", flush=True)
            except (OSError, ValueError, yaml.YAMLError) as error:
                print(f"{stamp()} GATES  not written: {error}", flush=True)
            plan = reload_plan(args.plan, plan, options)
            for cell in new_cells(plan, known, study, args.replace, only, allowed):
                queue.append(cell)
                todo.append(cell)
                print(f"{stamp()} QUEUE  {cell.name} (opened by its gate)", flush=True)
        for name in running:
            minutes = stall_minutes(study, name, time.time())
            if minutes is not None and name not in stalled:
                print(f"{stamp()} STALL  {name} {minutes}", flush=True)
            (stalled.add if minutes is not None else stalled.discard)(name)
        if queue:
            queue = by_critical_path(queue, timings, chains)
            cell, why = next_cell(running, queue, plan["limits"], last)
            if cell is not None:
                queue.remove(cell)
                line = (f"cd {shlex.quote(str(settings.ROOT))} && "
                        f"{command(cell, study, plan['settings'], args.replace, args.openrouter_key_env)}")
                log = open(logs / f"{cell.name}.log", "w")      # the child keeps its own copy of the handle
                argv, env = HOST.cell_process(line, args.mock)
                running[cell.name] = (subprocess.Popen(argv, env=env, stdout=log, stderr=subprocess.STDOUT,
                                                       stdin=subprocess.DEVNULL), cell)
                log.close()
                last = time.time()
                print(f"{stamp()} LAUNCH {cell.name} ({len(running)} running, {len(queue)} queued)", flush=True)
            elif time.time() - said >= 60:
                print(f"{stamp()} wait   {queue[0].name}: {why} ({len(running)} running)", flush=True)
                said = time.time()
        time.sleep(20)
    print(f"done: {len(todo) - len(failed)} of {len(todo)} runs ended with exit 0"
          + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
