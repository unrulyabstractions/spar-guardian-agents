"""A seed study from a plan file: every target with every pack, one run per cell and seed, on this Mac's Docker.

    uv run python -m scripts.run_study studies/ian_seed_prototyping_0.yaml --dry-run    # the cells and their commands
    uv run python -m scripts.run_study studies/ian_seed_prototyping_0.yaml --yes        # launch them: paid model calls

A plan (studies/*.yaml, seeds/README.md) names the study, the seeds, the targets and their packs:

    study: ian_seed_prototyping_0
    seeds: [1]
    targets:
      protein_luna:                                  # the run's name: <target>_<pack>_s<seed>
        module: examples.protein_design.binder_minimal
        model: gpt-6-luna-openrouter
        packs: [none, protein_design/exp0_pointer]        # "none" is the unseeded control
    settings: []                                     # more --set pairs after PILOT_SETTINGS
    limits: {max_runs: 10, max_per_task: {swe: 4}, spacing_s: 60}

Each cell runs the pilot's command (the scratchpad's launch.sh of 2026-11-22), under `zsh -lic` so the key comes from
~/.zshrc and never from a command line:

    STUDY=<study> uv run python -m <module> --model <model> --no-judge --name <target>_<pack>_s<seed>
        --set seed=<seed> --set rounds=30 --set calls_per_round=10 --set round_time_s=600 --set time_limit_s=18000
        --set cost_limit_usd=5 --set id_salt=<target>_s<seed> [--set seed_pack=<task>/<pack>]

id_salt is the target's and the seed's, not the pack's, so every pack of a target and seed draws the same IDs, roles and
deal as its control. A cell whose runs/<study>/<name>/run.json exists is skipped (--replace runs it again and sets the
old one aside). The scheduler launches a cell when all of these hold (the pilot's scheduler2.sh): at least
MIN_FREE_MEMORY_PCT of the Mac's memory free by `memory_pressure`, at least MIN_FREE_DISK_GB free on Colima's docker
disk, fewer than max_runs running, fewer than max_per_task[<task>] of the cell's task running, and spacing_s since the
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
LIMITS = {"max_runs": 10, "max_per_task": {}, "spacing_s": 60}
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
    task: str | None        # the pack's task; None for a control
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
            for pack in spec["packs"]:
                out.append(Cell(target, pack, seed, spec["module"], spec["model"], next(iter(tasks), None),
                                tuple(spec.get("settings") or ())))
    return out


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


def room(running: dict[str, tuple], cell: Cell, limits: dict, last: float) -> str | None:
    """Why the cell cannot launch now, or None when it can."""
    if len(running) >= limits["max_runs"]:
        return f"{len(running)} runs running"
    cap = (limits["max_per_task"] or {}).get(cell.task or "")
    if cap is not None and sum(1 for _, c in running.values() if c.task == cell.task) >= cap:
        return f"{cap} {cell.task} runs running"
    if time.time() - last < limits["spacing_s"]:
        return "spacing"
    memory, disk = free_memory_pct(), free_disk_gb()
    if memory is None or memory < MIN_FREE_MEMORY_PCT:
        return f"memory {memory}% free"
    if disk is None or disk < MIN_FREE_DISK_GB:
        return f"docker disk {disk} GB free"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.run_study", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("plan", type=Path, help="a plan file, e.g. studies/ian_seed_prototyping_0.yaml")
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
    running: dict[str, tuple[subprocess.Popen, Cell]] = {}
    failed, last, said = [], 0.0, 0.0
    queue = list(todo)
    while queue or running:
        for name, (proc, cell) in list(running.items()):
            if proc.poll() is not None:
                del running[name]
                print(f"{time.strftime('%H:%M:%S')} END    {name} exit {proc.returncode}", flush=True)
                if proc.returncode:
                    failed.append(name)
        if queue:
            why = room(running, queue[0], plan["limits"], last)
            if why is None:
                cell = queue.pop(0)
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
