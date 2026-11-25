"""What a plan's launch would need, beside what this host has, before any run starts. It launches nothing and makes no
model call.

    uv run python -m scripts.study.preflight studies/plans/ian_influencers_0.yaml
    uv run python -m scripts.study.preflight studies/plans/ian_influencers_0.yaml
        --gates studies/gated/ian_influencers_0.yaml

It prints Docker's disk (its size and free space) beside the host's free disk, and how Docker's disk grows when the
plan's peak needs more than Docker has free: on the Mac the Colima command that grows its VM's disk, on Linux the size
of disk to launch on. It prints the host's free memory over the launcher's floor, and Docker's own memory. The host's
facts come from scripts/study/host_facts.py. It prints each task's limit beside how many runs (or agents) the free
disk and memory allow, from the plan's `limits.needs` ({<task>: {disk_gb: X, memory_gb: Y}} for one run, with
`per: agent` for one agent) or the defaults measured on 2026-11-23. It ends with an estimate of the study's wall time
from the stored runs' timings and names the critical path. With --gates the chains of the gated spec count toward
each cell's path.

The peak is the most the limits let run at once: the cells still to run, heaviest first, each taken while max_runs,
its task's max_per_task and max_agents allow (a cell larger than max_agents alone, as the launcher starts it). The
estimate is the longest of four: the longest chain, the work over max_runs, a task's work over its max_per_task, and
the agent-minutes over max_agents. It ignores the spacing and the disk and memory holds. It exits 1 when the peak
needs more disk than Docker has free, more memory than the host has over the launcher's floor, or more than
DOCKER_MEMORY_SHARE of Docker's memory.
"""
from __future__ import annotations

import argparse
import math
import re
import shutil
import subprocess
from pathlib import Path

from scripts.study import gated_study
from scripts.study import run_study as rs
from scripts.study.host_facts import GB, HOST
from src.configuration import settings

DEFAULT_NEEDS = {"swe": {"disk_gb": 7.0, "memory_gb": 1.5}}
"""One run's needs by task, measured on 2026-11-23; a plan's limits.needs replaces them."""
OTHER_NEEDS = {"disk_gb": 0.5, "memory_gb": 0.2}
"""One run's needs for a task DEFAULT_NEEDS does not name."""
DOCKER_MEMORY_SHARE = 0.8
"""The share of Docker's memory the peak may take; the rest is the VM's, the daemon's and the graders'."""


def needs_of(limits: dict, task: str | None) -> dict:
    """The task's disk_gb and memory_gb, for one run or with per "agent" for one agent: the plan's limits.needs,
    else DEFAULT_NEEDS, else OTHER_NEEDS."""
    return {**OTHER_NEEDS, **DEFAULT_NEEDS.get(task or "", {}), **(limits.get("needs") or {}).get(task or "", {})}


def run_need(cell: rs.Cell, limits: dict, key: str) -> float:
    """What one run of the cell takes of one need (disk_gb or memory_gb): the task's need, times its agents when the
    need is per agent."""
    need = needs_of(limits, cell.task)
    return need[key] * (cell.n_agents if need.get("per") == "agent" else 1)


def peak_cells(todo: list[rs.Cell], limits: dict) -> list[rs.Cell]:
    """The most the limits let run at once: the cells, heaviest on disk first, each taken while max_runs, its task's
    max_per_task and max_agents allow. A cell larger than max_agents is taken only into an empty peak."""
    caps, max_agents = limits.get("max_per_task") or {}, limits.get("max_agents")
    out: list[rs.Cell] = []
    for cell in sorted(todo, key=lambda cell: -run_need(cell, limits, "disk_gb")):
        same = sum(1 for other in out if other.task == cell.task)
        agents = sum(other.n_agents for other in out)
        if len(out) >= limits["max_runs"] or same >= caps.get(cell.task or "", len(todo)):
            continue
        if max_agents is not None and out and agents + cell.n_agents > max_agents:
            continue
        out.append(cell)
    return out


def peak_runs(peak: list[rs.Cell]) -> dict[str | None, int]:
    """The peak's runs by task."""
    count: dict[str | None, int] = {}
    for cell in peak:
        count[cell.task] = count.get(cell.task, 0) + 1
    return count


def peak_need(peak: list[rs.Cell], limits: dict, key: str) -> float:
    """What the peak takes of one need (disk_gb or memory_gb)."""
    return sum(run_need(cell, limits, key) for cell in peak)


def host_free_disk_gb() -> float:
    """Free GB on the host's disk that holds the home folder, where Colima keeps its VM's disk on the Mac."""
    return shutil.disk_usage(Path.home()).free / GB


def docker_memory_gb() -> float | None:
    """Docker's memory in GB (`docker info`, Colima's VM), None when it cannot say."""
    try:
        out = subprocess.run(["docker", "info", "--format", "{{.MemTotal}}"], capture_output=True, text=True,
                             timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return int(out.strip()) / GB if re.fullmatch(r"\d+", out.strip()) else None


def memory_room_gb(total: float | None, free_pct: int | None) -> float | None:
    """The GB of memory runs may take before the host falls under the launcher's floor (MIN_FREE_MEMORY_PCT)."""
    if total is None or free_pct is None:
        return None
    return total * (free_pct - rs.MIN_FREE_MEMORY_PCT) / 100


def grown_disk_gb(size: int, free: int, need: float) -> int | None:
    """The size, in tens of GB, Docker's disk must grow to for the need plus MIN_FREE_DISK_GB; None when it fits."""
    short = need + rs.MIN_FREE_DISK_GB - free
    return int(math.ceil((size + short) / 10) * 10) if short > 0 else None


def estimate(todo: list[rs.Cell], limits: dict, timings: dict, chains: dict[str, int]) -> tuple[float, str, str]:
    """(minutes, the bound that sets them, the critical path's cell): the longest of the longest chain, the work over
    max_runs and each task's work over its max_per_task."""
    if not todo:
        return 0.0, "nothing to run", "none"
    path = max(todo, key=lambda cell: rs.chain_minutes(cell, timings, chains))
    work = {cell.name: rs.chain_minutes(cell, timings, chains) for cell in todo}
    bounds = {f"the chain of {path.name} ({work[path.name]:.0f} minutes)": work[path.name],
              f"{sum(work.values()):.0f} minutes of runs over max_runs {limits['max_runs']}":
                  sum(work.values()) / limits["max_runs"] if limits["max_runs"] else math.inf}
    if limits.get("max_agents"):
        agent_work = sum(work[cell.name] * cell.n_agents for cell in todo)
        bounds[f"{agent_work:.0f} agent-minutes over max_agents {limits['max_agents']}"] = \
            agent_work / limits["max_agents"]
    for task, cap in (limits.get("max_per_task") or {}).items():
        task_work = sum(work[cell.name] for cell in todo if cell.task == task)
        if task_work:
            bounds[f"{task_work:.0f} minutes of {task} over max_per_task {cap}"] = task_work / cap if cap else math.inf
    why = max(bounds, key=bounds.get)
    return bounds[why], why, path.name


def report(plan: dict, todo: list[rs.Cell], timings: dict, chains: dict[str, int]) -> tuple[list[str], bool]:
    """The preflight's lines and whether the limits ask for more than the disk or the memory allows."""
    limits, lines, over = plan["limits"], [], False
    disk, host_disk = HOST.docker_disk(), host_free_disk_gb()
    total, free_pct = HOST.memory_gb(), HOST.free_memory_pct()
    room, docker_memory = memory_room_gb(total, free_pct), docker_memory_gb()
    peak = peak_cells(todo, limits)
    by_task = peak_runs(peak)
    disk_need, memory_need = peak_need(peak, limits, "disk_gb"), peak_need(peak, limits, "memory_gb")
    size, free = disk if disk else (None, None)
    lines.append(f"docker disk: {size} GB, {free} GB free; {HOST.name}'s disk: {host_disk:.0f} GB free")
    lines.append(f"memory: {total and round(total)} GB, {free_pct}% free, {room and round(room, 1)} GB over the "
                 f"launcher's floor of {rs.MIN_FREE_MEMORY_PCT}%; "
                 f"Docker's: {docker_memory and round(docker_memory, 1)} GB")
    lines.append(f"peak at the limits: {len(peak)} runs of {sum(c.n_agents for c in peak)} agents "
                 f"({', '.join(f'{t} {n}' for t, n in by_task.items())}; max_agents {limits.get('max_agents')}), "
                 f"{disk_need:.1f} GB disk, {memory_need:.1f} GB memory")
    for task, n in by_task.items():
        need = needs_of(limits, task)
        unit = "agent" if need.get("per") == "agent" else "run"
        by_disk = int(free // need["disk_gb"]) if free is not None else None
        by_memory = int(room // need["memory_gb"]) if room is not None else None
        cap = (limits.get("max_per_task") or {}).get(task or "")
        lines.append(f"  {task}: max_per_task {cap}, peak {n}; one {unit} {need['disk_gb']} GB disk, "
                     f"{need['memory_gb']} GB memory; the disk allows {by_disk} {unit}s, the memory {by_memory}")
    if free is None or disk_need > free:
        over = True
        lines.append(f"DISK   the peak needs {disk_need:.1f} GB, Docker has {free} GB free")
    if size is not None and (grow := grown_disk_gb(size, free, disk_need)):
        lines.append(f"grow   {HOST.grow_disk(size, grow, host_disk)}")
    if room is None or memory_need > room:
        over = True
        lines.append(f"MEMORY the peak needs {memory_need:.1f} GB, {HOST.name} has {room} GB over its floor")
    if docker_memory is not None and memory_need > DOCKER_MEMORY_SHARE * docker_memory:
        over = True
        lines.append(f"DOCKER the peak needs {memory_need:.1f} GB, over {DOCKER_MEMORY_SHARE:.0%} of Docker's "
                     f"{docker_memory:.1f} GB")
    minutes, why, path = estimate(todo, limits, timings, chains)
    if math.isinf(minutes):
        lines.append(f"estimate: none, {why}: nothing launches")
    else:
        lines.append(f"estimate: {minutes:.0f} minutes, set by {why}; the critical path starts at {path}")
    return lines, over


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.study.preflight", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("plan", type=Path, help="a plan file, e.g. studies/plans/ian_influencers_0.yaml")
    parser.add_argument("--runs", type=Path, help="the folder of the study's runs (default runs/<study>)")
    parser.add_argument("--gates", type=Path, help="the plan's gated spec, whose chains count toward each path")
    args = parser.parse_args(argv)
    plan = rs.load_plan(args.plan)
    study_dir = args.runs or settings.ROOT / "runs" / plan["study"]
    todo = [cell for cell in rs.cells(plan) if not rs.RunDir(study_dir / cell.name).is_run()]
    chains = {}
    if args.gates:
        chains = gated_study.chain_lengths(gated_study.decide(gated_study.load_spec(args.gates), study_dir))
    lines, over = report(plan, todo, rs.stored_minutes(study_dir), chains)
    print(f"preflight {args.plan}: {len(todo)} cells to run; limits {plan['limits']}")
    print("\n".join(lines))
    return 1 if over else 0


if __name__ == "__main__":
    raise SystemExit(main())
