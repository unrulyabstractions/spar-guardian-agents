"""The runner on a box: build a task's data and images, run its cells at once with a guard against hung runs, report.

    .venv/bin/python -m scripts.box.box_runner build --task knowledge_work
    .venv/bin/python -m scripts.box.box_runner run --task knowledge_work --model deepseek-flash-openrouter
                                                   --suffix dsflash --commit <sha> [--samples 3] [--kinds solo ...]
    .venv/bin/python -m scripts.box.box_runner status

box_setup.sh runs build, box_run.sh starts run under nohup setsid with what the registry holds for the box, watch.sh
asks for status.

build makes what a task's first run would make, before its runs start together: the rows CheatBench's build writes
and the Docker images the task's sandbox needs, each by the domain's own builder, then loads the rows back. A task
without an entry in BUILDS is refused until its domain's steps are added there.

run refuses unless the clone is at --commit (the commit the fleet's launch pinned) and the model's key is in its
environment (box_run.sh sources /root/.box_env). It starts every cell of the task (fleet_layout.cells: one example
script per kind) times every sample at once, each `python -m examples.<package>.<cell> --model <model> --name
<cell>_<suffix><k>` in its own session with its output in logs/screen/<name>.log. A run counts as done before, and is
skipped, only when its export exists, its eval log says success and none of its episodes failed in the harness or at
the provider; any other run starts again, its earlier screen log set aside under logs/screen/attempts/. The plan
(logs/screen/box_<task>_<suffix>.json: each run's pid, start, end and exit) of an earlier runner is set aside there
too, never overwritten. The runner's own pid goes to ~/screen.pid. Every minute it looks at each live run. A run is
hung when it is alive GRACE_S past Inspect's time limit for the task's domain and nothing under logs/<name>/,
runs/<name>/ or its log changed for GRACE_S, or when it is alive at twice that whatever it writes. The runner then
stops the run's process group (SIGTERM, SIGKILL a minute later), notes it in the run's log and records it as hung; the
run's containers stay until the box goes, and the capture check refuses while they do.

status prints, for each plan on the box, the runner's state and the runs finished, running, failed, incomplete (an
episode failed in the harness or at the provider), hung, lost (not alive, no exit recorded) and waiting, then one line
for each run that is neither finished nor running. A runner that ended while runs are alive or waiting is trouble: no
guard watches them. When the runner has ended and nothing runs, it prints "all runs ended", which watch.sh turns into
an alert, since the box bills until it is captured and destroyed. It exits 1 on any trouble.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from inspect_ai.log import read_eval_log

from scripts.box.fleet_layout import KINDS, TASKS, cells, run_name
from scripts.screen import summary
from src.configuration import settings
from src.configuration.presets import add_config_arguments, build_config, config_pairs
from src.configuration.settings import INCOMPLETE_FAILURES
from src.domains import DOMAINS
from src.domains.knowledge import knowledge_sandbox
from src.domains.knowledge.knowledge_settings import BUILD as KNOWLEDGE_BUILD
from src.domains.knowledge.knowledge_settings import TASK_DIR as KNOWLEDGE_TASK_DIR
from src.domains.protein import protein_rows_build
from src.domains.swe import swe_sandbox
from src.domains.swe.swe_inputs import load_split
from src.domains.swe.swe_settings import DEFAULT_ROW as SWE_ROW
from src.models.model_registry import key_env, resolve
from src.sandbox import cheatbench_task_files as task_files
from src.sandbox.machines import agent_machines
from src.utils.json_files import read_json, read_jsonl

SCREEN_LOGS = settings.LOGS / "screen"
ATTEMPTS = SCREEN_LOGS / "attempts"
PID_FILE = Path.home() / "screen.pid"
GRACE_S = 20 * 60
POLL_S = 60
STOP_WAIT_S = 60
BEFORE = "before this runner"


# ---- build ----

def build_knowledge_work() -> None:
    """GDPval's rows from Hugging Face (CheatBench's build.py), the office image and the agents' image on it."""
    build = task_files.load_module(KNOWLEDGE_TASK_DIR / "build.py", "cheatbench_knowledge_work_build")
    build.build(output=KNOWLEDGE_BUILD)
    print(knowledge_sandbox.ensure_images())
    print(f"{len(DOMAINS['knowledge_work'].rows())} rows")


def build_protein_design() -> None:
    """The rows from CheatBench's generator (20 FASTA records from rcsb.org) and the python311 machine image."""
    protein_rows_build.build()
    print(agent_machines.ensure_image())
    print(f"{len(DOMAINS['protein_design'].rows())} rows")


def build_software_engineering() -> None:
    """The issue's amd64 image from its CheatBench environment and the native image on it."""
    print(swe_sandbox.ensure_images(SWE_ROW))
    print(f"{SWE_ROW}: {sorted(load_split(SWE_ROW))}")


BUILDS = {"knowledge_work": build_knowledge_work, "protein_design": build_protein_design,
          "software_engineering": build_software_engineering}
"""Each task's first-run steps. creative_writing and chess join once their domains land."""


def cmd_build(args: argparse.Namespace) -> int:
    if args.task not in BUILDS:
        print(f"refused: no build steps for {args.task}; add its domain's builders to BUILDS in {__file__}")
        return 1
    BUILDS[args.task]()
    return 0


# ---- the hung-run guard ----

def inspect_limit_s(task: str) -> int:
    """Inspect's time limit of a run of the task on the examples' config: ORBIT's halt plus the grace of the task's
    domain (src/tasks.py: the domain's time_grace_s, else the config's)."""
    domain = TASKS[task]
    if domain not in DOMAINS:
        raise ValueError(f"no domain {domain!r} in src.domains.DOMAINS for task {task}")
    parser = argparse.ArgumentParser()
    add_config_arguments(parser)
    defaults = parser.parse_args([])
    config = build_config(defaults.preset, defaults.scale, config_pairs(defaults))
    if not config.time_limit_s:
        raise ValueError("the examples' config sets no time_limit_s: the guard has no limit to hold runs to")
    return config.time_limit_s + (DOMAINS[domain].time_grace_s or config.time_grace_s)


def hung(now: float, started: float, last_write: float, limit_s: float, grace_s: float = GRACE_S) -> str | None:
    """Why a live run counts as hung, or None: alive grace_s past the limit with nothing written for grace_s, or
    alive at twice limit_s + grace_s whatever it writes."""
    alive = now - started
    if alive >= 2 * (limit_s + grace_s):
        return f"alive {alive / 60:.0f} min, twice the time limit and the grace"
    quiet = now - max(started, last_write)
    if alive >= limit_s + grace_s and quiet >= grace_s:
        return f"nothing written for {quiet / 60:.0f} min, {alive / 60:.0f} min after its start"
    return None


def last_write(name: str) -> float:
    """The newest change under the run's eval log folder, its run folder and its screen log; 0 when none exist."""
    newest = 0.0
    for path in (settings.LOGS / name, settings.RUNS / name):
        for dirpath, _, filenames in os.walk(path):
            for filename in filenames:
                try:
                    newest = max(newest, os.lstat(os.path.join(dirpath, filename)).st_mtime)
                except OSError:
                    pass
    log = SCREEN_LOGS / f"{name}.log"
    return max(newest, log.stat().st_mtime) if log.exists() else newest


def stop(process: subprocess.Popen) -> None:
    """End a run's process group: SIGTERM, then SIGKILL when it has not ended in STOP_WAIT_S."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=STOP_WAIT_S)
            return
        except subprocess.TimeoutExpired:
            continue


# ---- what a run left ----

def eval_status(path: Path) -> str:
    """The status an eval log's header records (success, error, cancelled, started)."""
    return read_eval_log(str(path), header_only=True).status


def outcome(name: str) -> tuple[str, str]:
    """(finished | incomplete | failed, why) from what the run left in runs/<name>/: finished needs the code
    ladder's signals.jsonl, run.json, its eval log with status success, and no episode that failed in the harness or
    at the provider (incomplete, as the results browser counts it)."""
    run_dir = settings.RUNS / name
    if not (run_dir / "signals.jsonl").is_file():
        return "failed", "no signals.jsonl"
    run = read_json(run_dir / "run.json")
    eval_log = Path(run["eval_log"]) if run and run.get("eval_log") else None
    if eval_log is None or not eval_log.is_file():
        return "failed", "no eval log on disk"
    status = eval_status(eval_log)
    if status != "success":
        return "failed", f"its eval ended {status}"
    failed = [e.get("failure") for e in read_jsonl(run_dir / "episodes.jsonl")
              if e.get("failure") in INCOMPLETE_FAILURES]
    if failed:
        return "incomplete", f"{len(failed)} episodes failed ({', '.join(sorted(set(failed)))})"
    return "finished", "its eval succeeded"


# ---- run ----

def plan_path(task: str, suffix: str) -> Path:
    return SCREEN_LOGS / f"box_{task}_{suffix}.json"


def set_aside(path: Path) -> Path | None:
    """Move an earlier attempt's file to logs/screen/attempts/<stem>.<n><suffix>, n the first free number."""
    if not path.exists():
        return None
    ATTEMPTS.mkdir(parents=True, exist_ok=True)
    n = 1
    while (ATTEMPTS / f"{path.stem}.{n}{path.suffix}").exists():
        n += 1
    target = ATTEMPTS / f"{path.stem}.{n}{path.suffix}"
    os.replace(path, target)
    return target


def save(path: Path, plan: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(plan, indent=1) + "\n")
    os.replace(temporary, path)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def launch(cell: str, name: str, model: str) -> subprocess.Popen:
    """One run in its own session, so the guard can stop its whole process group. Its screen log is new: an earlier
    attempt's was set aside."""
    command = [sys.executable, "-m", f"examples.{cell}", "--model", model, "--name", name]
    with open(SCREEN_LOGS / f"{name}.log", "x") as log:
        log.write(" ".join(command) + "\n")
        log.flush()
        return subprocess.Popen(command, cwd=settings.ROOT, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)


def clone_head() -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=settings.ROOT, capture_output=True, text=True)
    return result.stdout.strip()


def cmd_run(args: argparse.Namespace) -> int:
    head = clone_head()
    if head != args.commit:
        print(f"refused: the clone is at {head or '(unknown)'}, the fleet pinned {args.commit}")
        return 1
    try:
        keys = key_env(resolve(args.model))
        task_cells = cells(args.task, tuple(args.kinds))
        limit = inspect_limit_s(args.task)
    except ValueError as error:
        print(f"refused: {error}")
        return 1
    if keys and not any(os.environ.get(key) for key in keys):
        print(f"refused: none of {keys} is set; box_run.sh writes it to /root/.box_env")
        return 1
    jobs = [(cell, run_name(cell, args.suffix, k)) for k in range(1, args.samples + 1) for cell in task_cells]
    for folder in (SCREEN_LOGS, settings.RUNS, settings.ROOT / "out"):
        folder.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(f"{os.getpid()}\n")
    path = plan_path(args.task, args.suffix)
    earlier = set_aside(path)
    plan = {"task": args.task, "model": args.model, "suffix": args.suffix, "commit": args.commit,
            "started": now_iso(), "limit_s": limit, "grace_s": GRACE_S, "runner_pid": os.getpid(),
            "earlier_plan": str(earlier) if earlier else None,
            "runs": {name: {"cell": cell, "pid": None, "started": None, "ended": None, "exit": None, "hung": None,
                            "again": None} for cell, name in jobs}}
    processes, starts = {}, {}
    for cell, name in jobs:
        record = plan["runs"][name]
        left, why = outcome(name)
        if left == "finished":
            record.update(exit=0, ended=BEFORE)
            continue
        log = set_aside(SCREEN_LOGS / f"{name}.log")
        if log or (settings.RUNS / name).exists():
            record["again"] = f"{why}; earlier log {log}"
            print(f"   {name:40s} again: {why}", flush=True)
        processes[name] = launch(cell, name, args.model)
        starts[name] = time.time()
        record.update(pid=processes[name].pid, started=now_iso())
    save(path, plan)
    print(f"== {args.task} on {args.model}: {len(processes)} runs started, {len(jobs) - len(processes)} done before; "
          f"Inspect's limit {limit / 60:.0f} min, grace {GRACE_S / 60:.0f} min; plan {path}", flush=True)
    while processes:
        time.sleep(POLL_S)
        for name, process in list(processes.items()):
            record, code = plan["runs"][name], process.poll()
            if code is None:
                reason = hung(time.time(), starts[name], last_write(name), limit)
                if reason is None:
                    continue
                stop(process)
                with open(SCREEN_LOGS / f"{name}.log", "a") as log:
                    log.write(f"\n== box_runner: stopped as hung ({reason})\n")
                record.update(hung=reason, exit=process.poll())
                print(f"   {name:40s} HUNG: {reason}", flush=True)
            else:
                record["exit"] = code
                print(f"   {name:40s} rc={code} {(time.time() - starts[name]) / 60:5.1f} min", flush=True)
            record["ended"] = now_iso()
            del processes[name]
        save(path, plan)
    print(summary([name for _, name in jobs]), flush=True)
    return 0 if all(state(name, r) == "finished" for name, r in plan["runs"].items()) else 1


# ---- status ----

def alive(pid: int | None, argument: str) -> bool:
    """Whether pid is a live process with this argument on its command line (a reused pid does not count)."""
    if not pid:
        return False
    try:
        return argument.encode() in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return False


def state(name: str, record: dict) -> str:
    if record["hung"]:
        return "hung"
    if record["exit"] is not None:
        return outcome(name)[0] if record["exit"] == 0 else "failed"
    if record["pid"] is None:
        return "waiting"
    return "running" if alive(record["pid"], name) else "lost"


STATES = ("finished", "running", "failed", "incomplete", "hung", "lost", "waiting")


def cmd_status(args: argparse.Namespace) -> int:
    plans = sorted(SCREEN_LOGS.glob("box_*.json"))
    if not plans:
        print("no runner has started on this box")
        return 1
    trouble = 0
    for path in plans:
        plan = json.loads(path.read_text())
        states = {name: state(name, record) for name, record in plan["runs"].items()}
        counts = {s: list(states.values()).count(s) for s in STATES}
        runner_alive = alive(plan["runner_pid"], "scripts.box.box_runner")
        print(f"{plan['task']} {plan['model']} {plan['suffix']}: runner {'alive' if runner_alive else 'ended'}; "
              + ", ".join(f"{n} {s}" for s, n in counts.items() if n or s in ("finished", "running")))
        for name, s in states.items():
            if s not in ("finished", "running"):
                record = plan["runs"][name]
                print(f"   {s:10s} {name} exit={record['exit']} {record['hung'] or ''}")
        trouble += counts["failed"] + counts["incomplete"] + counts["hung"] + counts["lost"]
        if not runner_alive and (counts["running"] or counts["waiting"]):
            print(f"!! the runner ended with {counts['running']} runs running and {counts['waiting']} waiting: no "
                  f"hung guard watches them")
            trouble += 1
        elif not runner_alive:
            ended = [r["ended"] for r in plan["runs"].values() if r["ended"] and r["ended"] != BEFORE]
            print(f"   all runs ended{', the last at ' + max(ended) if ended else ''}: capture this box and destroy "
                  f"it")
    return 1 if trouble else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="the task's rows and images, before its runs start")
    build.add_argument("--task", required=True, choices=list(TASKS))
    run = commands.add_parser("run", help="every cell x sample of the task at once, with the hung-run guard")
    run.add_argument("--task", required=True, choices=list(TASKS))
    run.add_argument("--model", required=True, help="a registry entry (the screen's: fleet_layout.MODELS) or an "
                                                    "Inspect model string, such as mockllm/model for a rehearsal")
    run.add_argument("--suffix", required=True, help="run names are <cell>_<suffix><k>")
    run.add_argument("--commit", required=True, help="the commit the fleet's launch pinned; the clone must be at it")
    run.add_argument("--samples", type=int, default=3)
    run.add_argument("--kinds", nargs="+", default=list(KINDS), choices=list(KINDS))
    commands.add_parser("status", help="the runs of every plan on this box")
    args = parser.parse_args(argv)
    return {"build": cmd_build, "run": cmd_run, "status": cmd_status}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
