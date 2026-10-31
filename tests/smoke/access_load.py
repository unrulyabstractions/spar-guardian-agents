"""Many runs at once on one Docker, each a process of its own with its own sidecar, as a box runs them. No model spend.

    uv run python -m tests.smoke.access_load [--samples 15] [--agents 1]

Each run is one subset_sum sample under the mock model. A barrier holds every sample at its first turn until all have
reached it, so the runs truly overlap. Then the odd samples read the key in a loop and the even ones wait. It passes
when every log is valid with nothing dropped, every row sits on a machine of its own run, each reader holds exactly its
reads and each idle sample none but its controls, the calls overlap, and no container of the run is left. Results go to
runs/smoke_load/<stamp>/. A sample that dies releases the barrier for the others.

Each sidecar holds one fanotify group, so fs.fanotify.max_user_groups caps the runs at once, 128 on Colima. A run past
that dies at setup with `fanotify_init: EMFILE`, before any model call, and reads absent. Memory can cap them sooner:
each run is a process on the host, and 120 at once exhausted a 48 GB Mac. The suite refuses more runs than the memory
free now holds, and says how many fit.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

import psutil
from inspect_ai.model import ModelOutput

from src.configuration import settings
from src.domains.interview import SubsetSum
from src.domains.interview._interview_settings import SUBSET
from src.judging.cheatbench_judge import judge_spec
from src.measures.run_directory import ACCESS_LOG, RunDir
from src.tasks import swarm_task
from src.utils.json_files import read_jsonl
from tests.smoke.common import Suite, small
from tests.support.mock_model import call, scripted

SAMPLES = 15
READS = 60
READ_LOOP = "for i in $(seq 1 {reads}); do cat {key} >/dev/null; sleep 0.2; done; echo read"
WAIT = "sleep 14; echo waited"
BARRIER_S = 900                 # every sample reaches its first turn within this
LIMIT_S = 1800                  # every run ends within this
POLL_S = 5
PROJECT = "load"                # the task's name, which starts each compose project's
RUN_MEMORY_B = 360_000_000      # one run's process on the host, at import alone
SPARE_B = 8 * 2**30             # what the host keeps for everything else


async def at_barrier(barrier: Path, sample: int, samples: int, then: ModelOutput) -> ModelOutput:
    """Mark this sample as at its first turn, and wait for the others."""
    (barrier / str(sample)).touch()
    deadline = time.monotonic() + BARRIER_S
    while len(list(barrier.iterdir())) < samples:
        if time.monotonic() > deadline:
            raise RuntimeError(f"sample {sample}: the others never reached their first turn")
        await asyncio.sleep(0.2)
    return then


def run_sample(sample: int, samples: int, agents: int, barrier: Path) -> dict:
    """One run: what its log says, for the parent to judge."""
    benchmark = SubsetSum()
    suite = Suite("smoke_load", judge_spec(benchmark.task_dir, benchmark.name))
    pairs = ("judge=none", "steps_per_agent=2", f"n_agents={agents}", "concurrent_rounds=false")
    config = small(*pairs)
    reader = sample % 2 == 1
    command = READ_LOOP.format(reads=READS, key=SUBSET.cache) if reader else WAIT
    first, *others = config.names
    tool = "bash" if agents == 1 else "shell"

    def go(messages) -> ModelOutput:
        return at_barrier(barrier, sample, samples, call(tool, {"command": command}, "c1"))

    ending = "submit" if agents == 1 else "stop_working"
    script = {first: [go, call(ending, {"answer": "done"} if agents == 1 else {}, "c2")],
              **{agent: [call("stop_working", {}, f"{agent}-stop")] for agent in others}}
    name = f"sample_{sample}"
    log, _, _ = suite.run(name, swarm_task(benchmark, config, name=f"{PROJECT}_{sample}"), scripted(script))
    run = RunDir(suite.runs / name)
    block = run.run_json["access_log"]
    opens = [row for row in read_jsonl(run.path / ACCESS_LOG) if row["origin"] != "control"]
    tool_call = next(event for event in log.samples[0].events if event.event == "tool" and event.id == "c1")
    return {"sample": sample, "reader": reader, "status": block["status"], "dropped": block["dropped"],
            "opens": len(opens), "homeless": sum(row["machine"] is None for row in opens),
            "window": [tool_call.timestamp.timestamp(), tool_call.completed.timestamp()], "run": str(run.path)}


def problems(result: dict) -> list[str]:
    """What a run's result says went wrong under the load."""
    wanted = READS if result["reader"] else 0
    found = [] if result["status"] == "valid" else [f"status {result['status']}"]
    if result["dropped"]:
        found.append(f"{result['dropped']} rows dropped")
    if result["homeless"]:
        found.append(f"{result['homeless']} rows on no machine of this run")
    if result["opens"] != wanted:
        found.append(f"{result['opens']} opens, wanted {wanted}")
    return found


def overlap_s(results: list[dict]) -> float:
    """How long every run's call held at once: positive when they all overlapped."""
    return min(r["window"][1] for r in results) - max(r["window"][0] for r in results)


def release_failed(children: dict[int, subprocess.Popen], barrier: Path) -> None:
    """Mark the samples that died as at the barrier, so the others run on and the suite ends with their results."""
    for sample, child in children.items():
        if child.poll() not in (None, 0):
            (barrier / str(sample)).touch()


def containers(*filters: str) -> list[str]:
    """The names of the containers Docker holds, under `docker ps` filters."""
    return subprocess.run(["docker", "ps", *filters, "--format", "{{.Names}}"], capture_output=True, text=True,
                          check=True).stdout.split()


def too_many_runs(samples: int, available_b: int) -> str | None:
    """Why `samples` processes would not fit in the host's free memory, or None when they fit."""
    if samples * RUN_MEMORY_B + SPARE_B <= available_b:
        return None
    fit = max(0, available_b - SPARE_B) // RUN_MEMORY_B
    return f"{samples} runs at once need more memory than this host has free, {fit} fit"


def launch(samples: int, agents: int) -> int:
    """Every sample as a process, at once. 1 on any failure, and on a count the host's memory cannot hold."""
    refused = too_many_runs(samples, psutil.virtual_memory().available)
    if refused:
        print(f"REFUSED: {refused}")
        return 1
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = settings.RUNS / "smoke_load" / f"launch_{stamp}"
    barrier = root / "barrier"
    barrier.mkdir(parents=True)
    command = [sys.executable, "-m", "tests.smoke.access_load", "--samples", str(samples), "--agents", str(agents),
               "--barrier", str(barrier), "--results", str(root)]
    started = time.monotonic()
    children = {}
    for sample in range(samples):
        with (root / f"{sample}.log").open("w") as out:
            children[sample] = subprocess.Popen([*command, "--sample", str(sample)], stdout=out,
                                                stderr=subprocess.STDOUT, cwd=settings.ROOT)
    peak = 0
    while any(child.poll() is None for child in children.values()) and time.monotonic() - started < LIMIT_S:
        peak = max(peak, len(containers()))
        release_failed(children, barrier)
        time.sleep(POLL_S)
    failed = [sample for sample, child in children.items() if child.poll() != 0]
    for sample in failed:
        if children[sample].poll() is None:
            children[sample].kill()
    results = [json.loads(path.read_text()) for path in sorted(root.glob("result_*.json"))]
    found = {result["sample"]: problems(result) for result in results if problems(result)}
    left = containers("-a", "--filter", f"name=inspect-{PROJECT}_")
    overlap = overlap_s(results) if results else 0.0
    ok = not failed and not found and not left and len(results) == samples and overlap > 0
    print(f"{'PASS' if ok else 'FAIL'}: {len(results)} of {samples} runs of {agents} agent(s) at once, overlap "
          f"{overlap:.1f} s, peak {peak} containers, {time.monotonic() - started:.0f} s, under {root}")
    for label, value in (("failed", failed), ("problems", found), ("left", left)):
        if value:
            print(f"{label}: {value}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--samples", type=int, default=SAMPLES)
    parser.add_argument("--agents", type=int, default=1)
    parser.add_argument("--sample", type=int, help="run one sample, as the parent does")
    parser.add_argument("--barrier", type=Path, help="the barrier's directory, with --sample")
    parser.add_argument("--results", type=Path, help="where --sample writes its result")
    args = parser.parse_args(argv)
    if args.sample is None:
        return launch(args.samples, args.agents)
    result = run_sample(args.sample, args.samples, args.agents, args.barrier)
    (args.results / f"result_{args.sample}.json").write_text(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
