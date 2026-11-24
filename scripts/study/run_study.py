"""A seed study from a plan file: every target with every pack, one run per cell and seed, on this Mac's Docker.

    uv run python -m scripts.study.run_study studies/plans/ian_influencers_0.yaml --dry-run  # cells, commands
    uv run python -m scripts.study.run_study studies/plans/ian_influencers_0.yaml --yes      # launch: paid calls

A plan (studies/plans/*.yaml, studies/README.md, seeds/README.md) names the study, the seeds, the targets and their
packs:

    study: ian_seed_prototyping_2
    seeds: [1]
    targets:
      ian_binder_luna:                               # the run's name: <target>_<pack>_s<seed>
        module: studies.cells.protein_design.ian_binder_minimal
        model: gpt-6-luna-openrouter
        packs: [none, protein_design/exp1_called_cheating]   # "none" is the unseeded control
    settings: []                                     # more --set pairs after PILOT_SETTINGS
    limits: {max_runs: 10, max_per_task: {swe: 4}, spacing_s: 60}

The launcher takes the first queued cell with room: a cell whose task is full (max_per_task) is passed over, so light
cells do not wait behind SWE cells. Every 20 seconds it reads the plan again: new limits apply at once, and targets
added since the start (scripts.study.gated_study --write, as gates open) join the queue. A launcher started while
runs of the plan are live adopts them by their `--name` and never launches them again.

Each cell runs the pilot's command (the scratchpad's launch.sh of 2026-11-22), under `zsh -lic` so the key comes from
~/.zshrc and never from a command line:

    STUDY=<study> uv run python -m <module> --model <model> --no-judge --name <target>_<pack>_s<seed>
        --set seed=<seed> --set rounds=30 --set calls_per_round=10 --set round_time_s=600 --set time_limit_s=18000
        --set cost_limit_usd=5 --set id_salt=<target>_s<seed> [--set seed_pack=<task>/<pack>]

id_salt is the target's and the seed's, not the pack's, so every pack of a target and seed draws the same IDs, roles and
deal as its control. A cell whose runs/<study>/<name>/run.json exists is skipped (--replace runs it again and sets the
old one aside). The scheduler launches a cell when all of these hold (the pilot's scheduler2.sh): at least
MIN_FREE_MEMORY_PCT of the Mac's memory free by `memory_pressure`, at least MIN_FREE_DISK_GB free on Colima's docker
disk (or the task's own floor, limits min_disk_gb), fewer than max_runs running, fewer than
max_per_task[<task>] of the cell's task running, and spacing_s since the
last launch. Each run's console goes to logs/<study>/run_study/<name>.log. It kills nothing; it waits for every run
it launched and exits 1 when any failed.

A real launch spends money: it needs --yes, and it prints the total of the runs' caps first. The rules of CLAUDE.md
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

from src.configuration import seed_packs, settings
from src.measures.run_directory import RunDir

PILOT_SETTINGS = ["rounds=30", "calls_per_round=10", "round_time_s=600", "time_limit_s=18000", "cost_limit_usd=5"]
"""The pilot's settings, after the seed and before the salt and the pack."""
MIN_FREE_MEMORY_PCT = 40
MIN_FREE_DISK_GB = 10
LIMITS = {"max_runs": 10, "max_per_task": {}, "spacing_s": 60, "min_disk_gb": {}}
"""min_disk_gb: {task: GB} is a task's own floor of free Docker disk in place of MIN_FREE_DISK_GB: a task whose
containers write little (a poem, a binder) may launch where an SWE run, whose agents each build 1 to 2 GB, may not."""
PLAN_KEYS = {"study", "seeds", "targets", "settings", "limits", "closed"}
"""closed: true marks a study that is over: its plan is kept as a record, lists its cells with --dry-run and never
launches (its runs may be named after packs that were renamed since)."""
TARGET_KEYS = {"module", "model", "packs"}
TARGET_OPTIONAL = {"settings"}
"""settings: the target's own --set pairs, after the plan's (a rule level, a regulator's model)."""
NAME = re.compile(r"[a-z0-9_]+")


@dataclass
class Cell:
    target: str
    pack: str               # "<task>/<pack>" or "none"
    seed: int
    module: str
    model: str
    task: str | None        # the packs' task, or its module's task folder (a cell without a pack)
    settings: tuple[str, ...] = ()   # the target's own settings, after the plan's

    @property
    def name(self) -> str:
        return f"{self.target}_{self.pack.split('/')[-1]}_s{self.seed}"

    def argv(self, extra: list[str], replace: bool) -> list[str]:
        """The example's arguments, in the pilot's order."""
        out = ["--model", self.model, "--no-judge", "--name", self.name, *(["--replace"] if replace else []),
               "--set", f"seed={self.seed}"]
        for pair in [*PILOT_SETTINGS, *extra, *self.settings, f"id_salt={self.target}_s{self.seed}"]:
            out += ["--set", pair]
        if self.pack != "none":
            out += ["--set", f"seed_pack={self.pack}"]
        return out


def load_plan(path: Path) -> dict:
    """The plan, checked: its keys, its targets' modules and packs, one task per target."""
    plan = yaml.safe_load(path.read_text()) or {}
    if set(plan) - PLAN_KEYS or not {"study", "seeds", "targets"} <= set(plan):
        raise ValueError(f"{path}: a plan takes {sorted(PLAN_KEYS)} and needs study, seeds and targets")
    if not plan["seeds"] or not all(isinstance(s, int) for s in plan["seeds"]):
        raise ValueError(f"{path}: seeds is a list of integers")
    for target, spec in plan["targets"].items():
        if not NAME.fullmatch(target) or not TARGET_KEYS <= set(spec) <= TARGET_KEYS | TARGET_OPTIONAL:
            raise ValueError(f"{path}: target {target!r} needs a name of a-z, 0-9 and _, and {sorted(TARGET_KEYS)} "
                             f"(and may take {sorted(TARGET_OPTIONAL)})")
        if not (settings.ROOT / Path(*spec["module"].split("."))).with_suffix(".py").is_file():
            raise ValueError(f"{path}: {target}'s module {spec['module']} is no script of this checkout")
        tasks = {seed_packs.pack_of(p)["task"] for p in spec["packs"] if p != "none"}
        if len(tasks) > 1:
            raise ValueError(f"{path}: {target}'s packs are of several tasks: {sorted(tasks)}")
    limits = {**LIMITS, **(plan.get("limits") or {})}
    if set(limits) != set(LIMITS):
        raise ValueError(f"{path}: limits takes {sorted(LIMITS)}")
    return {**plan, "settings": list(plan.get("settings") or []), "limits": limits}


def cells(plan: dict) -> list[Cell]:
    """Every cell of the plan: seed by seed, target by target in the plan's order, pack by pack."""
    out = []
    for seed in plan["seeds"]:
        for target, spec in plan["targets"].items():
            tasks = {seed_packs.pack_of(p)["task"] for p in spec["packs"] if p != "none"}
            task = next(iter(tasks), None) or module_task(spec["module"])
            for pack in spec["packs"]:
                out.append(Cell(target, pack, seed, spec["module"], spec["model"], task,
                                tuple(spec.get("settings") or ())))
    return out


def module_task(module: str) -> str | None:
    """The task of a target without a pack: the task folder its module is in, under examples/ or studies/cells/
    (examples.swe.x and studies.cells.swe.x are swe's), so a baseline cell counts toward its task's limit. On
    2026-11-22 three baselines went uncounted and six SWE runs ran at once, which left Docker's disk 4 GB free."""
    parts = module.split(".")
    if len(parts) == 3 and parts[0] == "examples":
        return parts[1]
    return parts[2] if len(parts) == 4 and parts[:2] == ["studies", "cells"] else None


def command(cell: Cell, study: str, extra: list[str], replace: bool) -> str:
    """The shell line that runs the cell from the checkout's root."""
    return f"STUDY={shlex.quote(study)} exec uv run python -m {cell.module} {shlex.join(cell.argv(extra, replace))}"


def free_memory_pct() -> int | None:
    """The Mac's free memory in percent (`memory_pressure`), None when it cannot say."""
    try:
        out = subprocess.run(["memory_pressure"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = re.search(r"free percentage:\s*(\d+)%", out)
    return int(found.group(1)) if found else None


def free_disk_gb() -> int | None:
    """Free GB on Colima's docker disk, None when it cannot say."""
    try:
        out = subprocess.run(["colima", "ssh", "--", "df", "-BG", "/var/lib/docker"], capture_output=True, text=True,
                             timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = out.strip().splitlines()
    fields = lines[-1].split() if len(lines) > 1 else []
    return int(fields[3].rstrip("G")) if len(fields) >= 4 and fields[3].rstrip("G").isdigit() else None


def task_full(running: dict[str, tuple], cell: Cell, limits: dict) -> str | None:
    """Why the cell's task takes no more runs now (max_per_task), or None."""
    cap = (limits["max_per_task"] or {}).get(cell.task or "")
    if cap is not None and sum(1 for _, c in running.values() if c.task == cell.task) >= cap:
        return f"{cap} {cell.task} runs running"
    return None


def held_all(running: dict[str, tuple], limits: dict, last: float) -> str | None:
    """Why no cell launches now, whatever its task: the runs' limit, the spacing, the Mac's memory. None otherwise."""
    if len(running) >= limits["max_runs"]:
        return f"{len(running)} runs running"
    if time.time() - last < limits["spacing_s"]:
        return "spacing"
    memory = free_memory_pct()
    if memory is None or memory < MIN_FREE_MEMORY_PCT:
        return f"memory {memory}% free"
    return None


def disk_short(cell: Cell, limits: dict, disk: int | None) -> str | None:
    """Why Docker's free disk does not take the cell: under its task's floor (limits min_disk_gb, else
    MIN_FREE_DISK_GB), or unknown. None when it does."""
    floor = (limits.get("min_disk_gb") or {}).get(cell.task or "", MIN_FREE_DISK_GB)
    return f"docker disk {disk} GB free" if disk is None or disk < floor else None


def room(running: dict[str, tuple], cell: Cell, limits: dict, last: float) -> str | None:
    """Why the cell cannot launch now, or None when it can."""
    if len(running) >= limits["max_runs"]:
        return f"{len(running)} runs running"
    return task_full(running, cell, limits) or held_all(running, limits, last) or \
        disk_short(cell, limits, free_disk_gb())


def next_cell(running: dict[str, tuple], queue: list[Cell], limits: dict,
              last: float) -> tuple[Cell | None, str | None]:
    """The first queued cell with room, or why none launches now. The runs' limit, the spacing and memory hold every
    cell. A cell whose task is full, or whose task needs more free disk than Docker has, is passed over, so a light
    cell queued behind SWE cells does not wait for them. On 2026-11-23 four runs ran where Docker held 2.6 of 23 GiB,
    because the head of the queue was an SWE cell, and later poem cells waited five minutes on a disk floor only SWE
    runs need."""
    if why := held_all(running, limits, last):
        return None, why
    disk, why = free_disk_gb(), None
    for cell in queue:
        held = task_full(running, cell, limits) or disk_short(cell, limits, disk)
        if held is None:
            return cell, None
        why = why or held
    return None, why


LIVE = re.compile(r"^(?:uv run python|\S*python[\d.]*) -m (?:studies\.cells|examples)\.\S+ .*?"
                  r"--name ([a-z0-9_]+)(?:\s|$)")
"""A cell script's own process (uv's, then Python's), never a shell whose text quotes one."""


def live_names() -> set[str]:
    """The run names of the cell scripts running on this Mac now, from their command lines (`--name <run>`): the
    runs a launcher of this study started before this one, which this one adopts and never launches again."""
    try:
        out = subprocess.run(["ps", "-axo", "command="], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {m[1] for line in out.splitlines() if (m := LIVE.match(line.strip()))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.study.run_study", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("plan", type=Path, help="a plan file, e.g. studies/plans/ian_influencers_0.yaml")
    parser.add_argument("--study", help="the study to run into instead of the plan's")
    parser.add_argument("--dry-run", action="store_true", help="list the cells and their commands; launch nothing")
    parser.add_argument("--yes", action="store_true", help="launch: the runs make paid model calls")
    parser.add_argument("--replace", action="store_true", help="run a cell whose run exists again (the example's "
                                                                 "--replace sets the old one aside)")
    args = parser.parse_args(argv)
    plan = load_plan(args.plan)
    study = args.study or plan["study"]
    todo, skipped = [], []
    for cell in cells(plan):
        done = RunDir(settings.ROOT / "runs" / study / cell.name).is_run()
        (skipped if done and not args.replace else todo).append(cell)
    cap = float(next((p.split("=", 1)[1] for p in [*PILOT_SETTINGS, *plan["settings"]][::-1]
                      if p.startswith("cost_limit_usd=")), 5))
    print(f"plan {args.plan}: study {study}, {len(todo) + len(skipped)} cells, {len(skipped)} done already, "
          f"{len(todo)} to run; at most ${cap * len(todo):.2f} at the runs' caps of ${cap:.2f}")
    for cell in skipped:
        print(f"  skip   {cell.name} (runs/{study}/{cell.name} holds its run)")
    for cell in todo:
        print(f"  {'plan' if args.dry_run else 'queue'}   {cell.name}")
        print(f"         {command(cell, study, plan['settings'], args.replace)}")
    if args.dry_run:
        print(f"  now    memory {free_memory_pct()}% free (needs {MIN_FREE_MEMORY_PCT}%), docker disk "
              f"{free_disk_gb()} GB free (needs {MIN_FREE_DISK_GB}); limits {plan['limits']}")
        return 0
    if plan.get("closed"):
        print(f"{args.plan}: this study is closed, its plan is a record: copy it under a new study to run it again",
              file=sys.stderr)
        return 2
    if not args.yes:
        print("a launch makes paid model calls: add --yes, or --dry-run to look", file=sys.stderr)
        return 2
    logs = settings.ROOT / "logs" / study / "run_study"
    logs.mkdir(parents=True, exist_ok=True)
    running: dict[str, tuple[subprocess.Popen | None, Cell]] = {}      # None: a run an earlier launcher started
    failed, last, said = [], 0.0, 0.0
    queue = list(todo)
    known = {cell.name for cell in [*todo, *skipped]}
    while queue or running:
        try:                                                    # the plan may gain targets and change its limits
            fresh = load_plan(args.plan)
        except (OSError, ValueError, yaml.YAMLError):
            fresh = None                                        # caught mid-write: the last plan read stands
        if fresh is not None and not fresh.get("closed"):
            plan = fresh
            for cell in cells(plan):
                if cell.name not in known:
                    known.add(cell.name)
                    if args.replace or not RunDir(settings.ROOT / "runs" / study / cell.name).is_run():
                        queue.append(cell)
                        todo.append(cell)
                        print(f"{time.strftime('%H:%M:%S')} QUEUE  {cell.name} (new in the plan)", flush=True)
        alive = live_names()
        for cell in [c for c in queue if c.name in alive]:      # an earlier launcher's run: adopt it
            queue.remove(cell)
            running[cell.name] = (None, cell)
            print(f"{time.strftime('%H:%M:%S')} ADOPT  {cell.name} ({len(running)} running, {len(queue)} queued)",
                  flush=True)
        for name, (proc, cell) in list(running.items()):
            if proc is None and name not in alive:
                del running[name]
                made = RunDir(settings.ROOT / "runs" / study / name).is_run()
                print(f"{time.strftime('%H:%M:%S')} END    {name} adopted, {'its run is stored' if made else 'no run'}",
                      flush=True)
                if not made:
                    failed.append(name)
            elif proc is not None and proc.poll() is not None:
                del running[name]
                print(f"{time.strftime('%H:%M:%S')} END    {name} exit {proc.returncode}", flush=True)
                if proc.returncode:
                    failed.append(name)
        if queue:
            cell, why = next_cell(running, queue, plan["limits"], last)
            if cell is not None:
                queue.remove(cell)
                line = f"cd {shlex.quote(str(settings.ROOT))} && {command(cell, study, plan['settings'], args.replace)}"
                log = open(logs / f"{cell.name}.log", "w")      # the child keeps its own copy of the handle
                running[cell.name] = (subprocess.Popen(["zsh", "-lic", line], stdout=log, stderr=subprocess.STDOUT,
                                                       stdin=subprocess.DEVNULL), cell)
                log.close()
                last = time.time()
                print(f"{time.strftime('%H:%M:%S')} LAUNCH {cell.name} ({len(running)} running, {len(queue)} queued)",
                      flush=True)
            elif time.time() - said >= 60:
                print(f"{time.strftime('%H:%M:%S')} wait   {queue[0].name}: {why} ({len(running)} running)", flush=True)
                said = time.time()
        time.sleep(20)
    print(f"done: {len(todo) - len(failed)} of {len(todo)} runs ended with exit 0"
          + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
