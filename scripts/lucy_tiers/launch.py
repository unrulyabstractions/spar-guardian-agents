"""Queue runner of lucy_tiers_0 (25 Nov 2026), kept as it ran: a record, beside studies/plans/lucy_tiers_0.yaml. It
ran on a Linux machine, where run_study of that day (zsh, Colima, memory_pressure) did not. From the repo's root:

    uv run python -m scripts.lucy_tiers.launch scripts/lucy_tiers/plan_as_run.yaml --dry-run
    ... --stage 1 --yes            # launch stage 1 (paid)
    ... --stage 1 2 --yes          # both: stage 2's cells wait for their gates, stage 1's `told` runs

A cell is task x model x arm x seed, named <task>_<model>_<arm>_s<seed>. A cell whose processed/run.json exists is
skipped, so a
stopped launcher resumes. The launcher kills nothing. It stops launching when the OpenRouter key's usage has risen by
the plan's budget_usd since the first start (recorded in runs/<study>/launcher_spend.json).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import yaml

from src.measures.run_directory import RunDir

RUNGS = ["none", "looked", "tempted", "attempted", "hacked"]


def read_env(path: str) -> dict[str, str]:
    out = {}
    for line in Path(path).read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def key_usage(api_key: str) -> float | None:
    try:
        request = urllib.request.Request("https://openrouter.ai/api/v1/key",
                                         headers={"Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(request, timeout=20) as response:
            return float(json.load(response)["data"]["usage"])
    except Exception as error:  # noqa: BLE001 - a failed check must not stop the queue
        print(f"SPEND CHECK FAILED: {error}", flush=True)
        return None


def running_names() -> set[str]:
    """The --name of every cell script live on this machine, from the process list: a restarted launcher adopts
    them, counts them toward its limits and never starts them again."""
    listing = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
    names = set()
    for line in listing.splitlines():
        if "-m studies.cells." in line and "--name " in line:
            names.add(line.split("--name ", 1)[1].split()[0])
    return names


def cells(plan: dict, stages: list[int], models: list[str] | None, tasks: list[str] | None,
          arms: list[str] | None):
    """Cells of the stages, an earlier stage first; within a stage seed first, so that a stopped batch stays balanced,
    then model, task, arm."""
    out = []
    for stage in sorted(stages):
        for seed in plan["seeds"]:
            for model in plan["models"]:
                for task in plan["tasks"]:
                    for arm, spec in plan["arms"].items():
                        if spec["stage"] != stage or (models and model not in models) or \
                                (tasks and task not in tasks) or (arms and arm not in arms) or \
                                (stage == 2 and model in plan.get("stage2_skip", [])) or \
                                model not in spec.get("models", plan["models"]) or \
                                task not in spec.get("tasks", plan["tasks"]):
                            continue
                        out.append({"name": f"{task}_{model}_{arm}_s{seed}", "task": task, "model": model,
                                    "arm": arm, "seed": seed, "stage": stage})
    return out


def command(plan: dict, cell: dict) -> list[str]:
    task, model, arm = plan["tasks"][cell["task"]], plan["models"][cell["model"]], plan["arms"][cell["arm"]]
    sets = [f"seed={cell['seed']}", *plan["settings"], f"cost_limit_usd={model['cap']}",
            # one salt per task, model and seed: every arm draws the same IDs and the same deal of the pieces
            f"id_salt={cell['task']}_{cell['model']}_s{cell['seed']}",
            *(plan["regulated"] if arm.get("regulated") else []),
            *(plan["speaks_first"] if arm.get("speaks_first") else []), *arm["settings"]]
    out = ["uv", "run", "python", "-m", task["module"], "--model", model["entry"], "--no-judge", "--name",
           cell["name"]]
    for pair in sets:
        out += ["--set", pair]
    return out


def is_done(run_dir: Path) -> bool:
    """A run that finished and was exported (Ian's export writes processed/run.json last of the run's own files)."""
    return RunDir(run_dir).file("run.json").is_file()


def team_rung(run_dir: Path) -> str | None:
    """The team's Cheating rung from the run's stored label (analysis/labels/cheating.json, "team")."""
    path = RunDir(run_dir).file("cheating.json")
    if not path.is_file():
        return None
    return json.loads(path.read_text()).get("team")


def gate_open(plan: dict, runs: Path, cell: dict) -> bool | None:
    """True when a stage-1 gate run of the cell's task and model reached the gate's rung; False when all three are
    in and none did; None while any is missing."""
    gate, seen = plan["gate"], []
    arm = plan["arms"][cell["arm"]].get("gate", gate["arm"])
    for seed in plan["seeds"]:
        run_dir = runs / f"{cell['task']}_{cell['model']}_{arm}_s{seed}"
        rung = team_rung(run_dir) if is_done(run_dir) else None
        seen.append(rung)
    if any(r is not None and RUNGS.index(r) >= RUNGS.index(gate["rung"]) for r in seen):
        return True
    return None if any(r is None for r in seen) else False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    parser.add_argument("--stage", type=int, nargs="+", default=[1], help="1, 2 or both: stage 1's cells go first")
    parser.add_argument("--models", nargs="*")
    parser.add_argument("--tasks", nargs="*")
    parser.add_argument("--arms", nargs="*")
    parser.add_argument("--no-gate", action="store_true", help="stage 2 without the gate")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    plan = yaml.safe_load(args.plan.read_text())
    repo, study = Path(plan["repo"]), plan["study"]
    runs, logs = repo / "runs" / study, repo / "logs" / study / "console"
    env = {**os.environ, **read_env(plan["env_file"]), "STUDY": study,
           "PATH": f"{Path.home()}/.local/bin:{os.environ['PATH']}"}
    queue = [c for c in cells(plan, args.stage, args.models, args.tasks, args.arms)
             if not is_done(runs / c["name"])]
    print(f"{study} stage {args.stage}: {len(queue)} cells to run", flush=True)
    if args.dry_run or not args.yes:
        for cell in queue:
            print(" ", cell["name"], "::", " ".join(command(plan, cell)))
        return 0
    runs.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    (args.plan.parent / "launcher.pid").write_text(str(os.getpid()))      # stop it with: kill $(cat launcher.pid)
    spend_file = runs / "launcher_spend.json"
    usage = key_usage(env["OPENROUTER_API_KEY"])
    if spend_file.is_file():
        start = json.loads(spend_file.read_text())["start_usage"]
    else:
        if usage is None:
            print("no spend reading at the first start: not launching", flush=True)
            return 1
        start = usage
        spend_file.write_text(json.dumps({"start_usage": start, "started": time.strftime("%F %T")}))
    live: dict[str, tuple[subprocess.Popen | None, dict]] = {}
    failed, done, skipped, last_launch, last_check = [], [], [], 0.0, 0.0
    stopped = False
    every = {c["name"]: c for c in cells(plan, [1, 2, 3], None, None, None)}
    for name in running_names():                      # runs an earlier launcher started
        if name in every:
            live[name] = (None, every[name])
            print(f"{time.strftime('%T')} ADOPT {name}", flush=True)
    queue = [c for c in queue if c["name"] not in live]
    while queue or live:
        limits = yaml.safe_load(args.plan.read_text())["limits"]     # read again: new limits apply at once
        running = running_names()
        for name, (process, cell) in list(live.items()):
            if process is None:
                if name not in running:
                    (done if is_done(runs / name) else failed).append(name)
                    print(f"{time.strftime('%T')} END {name} (adopted) {'ok' if is_done(runs / name) else 'no run'}",
                          flush=True)
                    del live[name]
            elif process.poll() is not None:
                (done if process.returncode == 0 else failed).append(name)
                print(f"{time.strftime('%T')} END {name} exit {process.returncode}", flush=True)
                del live[name]
                # The OpenRouter workspace's monthly budget ran out on 25 Nov and every later run died within a
                # minute, each one marked failed. If a run's console shows that refusal, launch nothing more.
                console = logs / f"{name}.log"
                if process.returncode != 0 and not stopped and console.is_file() and \
                        "openrouter_workspace_budget" in console.read_text(errors="replace"):
                    stopped = True
                    print("WORKSPACE BUDGET REFUSAL: launching nothing more; live runs will fail the same way. "
                          f"Move runs/{study}/<run> aside for each run that died, then start again.", flush=True)
        now = time.time()
        if now - last_check > 120:
            usage, last_check = key_usage(env["OPENROUTER_API_KEY"]) or usage, now
            spent = (usage or start) - start
            print(f"{time.strftime('%T')} SPEND ${spent:.2f} of ${plan['budget_usd']} | live {len(live)} "
                  f"queued {len(queue)} done {len(done)} failed {len(failed)}", flush=True)
            if spent >= plan["budget_usd"] and not stopped:
                stopped = True
                print("BUDGET REACHED: launching nothing more; live runs finish", flush=True)
        if stopped:
            queue = []
        if queue and len(live) < limits["max_runs"] and now - last_launch >= limits["spacing_s"]:
            for index, cell in enumerate(queue):
                key = plan["tasks"][cell["task"]]["key"]
                if sum(1 for _, c in live.values() if plan["tasks"][c["task"]]["key"] == key) >= \
                        limits.get("max_per_task", {}).get(key, limits["max_runs"]):
                    continue
                if sum(1 for _, c in live.values() if c["model"] == cell["model"]) >= \
                        plan["models"][cell["model"]]["at_once"]:
                    continue
                if cell["stage"] == 2 and not args.no_gate:
                    state = gate_open(plan, runs, cell)
                    if state is None:
                        continue                      # its gate runs are not all in yet
                    if state is False:
                        skipped.append(queue.pop(index)["name"])
                        print(f"{time.strftime('%T')} GATE CLOSED {skipped[-1]}", flush=True)
                        break
                if is_done(runs / cell["name"]):
                    queue.pop(index)
                    break
                log = (logs / f"{cell['name']}.log").open("w")
                process = subprocess.Popen(command(plan, cell), cwd=repo, env=env, stdout=log,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                live[cell["name"]] = (process, queue.pop(index))
                last_launch = now
                print(f"{time.strftime('%T')} START {cell['name']}", flush=True)
                break
        (runs / "launcher_status.json").write_text(json.dumps(
            {"time": time.strftime("%F %T"), "stage": args.stage, "live": sorted(live), "queued": len(queue),
             "done": done, "failed": failed, "gate_closed": skipped,
             "spent_usd": round((usage or start) - start, 2)}, indent=1))
        time.sleep(5)
    print(f"LAUNCHER DONE stage {args.stage}: done {len(done)}, failed {len(failed)}, gate closed {len(skipped)}",
          flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
