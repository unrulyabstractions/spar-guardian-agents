"""`python -m tools hawk <run>...`: write each run's Hawk scan config and print `hawk local scan`; --run runs it."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
from pathlib import Path

from src.configuration.settings import JUDGE_MODEL, ROOT
from src.models.model_registry import resolve
from tools.hawk.hawk_scan_config import (
    HAWK_PYTHON,
    HAWK_SPEC,
    RUN_BOUND_SCANNERS,
    SCANNER_MODULE,
    SCANNER_PACKAGE_DIR,
    SOURCES,
    hawk_command,
    scan_config,
    transcript_source,
    write_scan_config,
)
from tools.run_reader import Run, load_runs, tools_dir, write_summary

NAME = "hawk"
HELP = "Hawk (METR) scan configs over the runs' transcripts with our Scout scanners; --run runs `hawk local scan`"
DEFAULT_TIMEOUT_S = 3600
TAIL_LINES = 40
LOCAL_NOTES = [
    "hawk local scan needs no HAWK_API_URL and no login: it reads no server unless HAWK_AI_GATEWAY_URL is set.",
    "The transcripts come from HAWK_RUNNER_EVALS_S3_URI/<eval_set_id>, set in runner.environment to a local directory.",
    "Hawk installs every scanner package with uv pip, so the scanners reach it through scanner_package/, "
    "a distribution with an inspect_ai entry point that imports SPAR_GUARDIAN_SCANNERS from SPAR_GUARDIAN_ROOT.",
    "The command runs hawk from uvx with the runner extra, so no hawk needs to be installed.",
    "Without --direct, hawk builds a fresh venv for the scan; with --direct it installs into the uvx environment.",
    "The model keys come from this environment (OPENAI_API_KEY and the others); a local scan uses no Hawk proxy.",
    "hawk scan run (a deployed Hawk) needs HAWK_API_URL, hawk login and eval sets in the warehouse; not used here.",
]
EVAL_LOG_NOTE = ("The eval log holds one sample for the whole swarm, so each scanner sees one transcript with no "
                 "agent; the run-bound scanners are written for one transcript per agent.")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The runs, whether to run hawk, the model, the scanners and where their transcripts come from."""
    parser.add_argument("runs", nargs="+", help="run names of the study or run directories")
    parser.add_argument("--run", action="store_true", help="run `hawk local scan` and record the outcome")
    parser.add_argument("--direct", action="store_true", help="pass --direct: install into the uvx environment")
    parser.add_argument("--model", default=JUDGE_MODEL, help="the scanners' model: registry entry or Inspect string")
    parser.add_argument("--scanner", action="append", default=None,
                        help=f"a scanner to run (repeatable); default: {', '.join(RUN_BOUND_SCANNERS)}")
    parser.add_argument("--source", choices=SOURCES, default=SOURCES[0],
                        help="scout: per-agent transcripts (the scout tool's database, else one built under "
                        "tools/hawk/transcripts); eval_log: the eval log's directory")
    parser.add_argument("--scanner-module", default=SCANNER_MODULE, help="the module that defines the scanners")
    parser.add_argument("--scanner-root", type=Path, default=ROOT, help="the directory the scanner module imports from")
    parser.add_argument("--hawk-spec", default=HAWK_SPEC, help="the hawk requirement uvx runs")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="seconds the scan may take")


def run(args: argparse.Namespace) -> int:
    """Write every run's scan config, and run hawk when asked; 2 on an unknown model or run, 1 when a scan failed."""
    scanners = args.scanner or list(RUN_BOUND_SCANNERS)
    try:
        model = resolve(args.model)
    except ValueError as error:
        print(f"{NAME}: {error}", file=sys.stderr)
        return 2
    runs = load_runs(args.runs, NAME)
    if runs is None:
        return 2
    failures = 0
    for run_data in runs:
        failures += scan_one_run(run_data, args, scanners, model)
    return 1 if failures else 0


def scan_one_run(run_data: Run, args: argparse.Namespace, scanners: list[str], model: str) -> int:
    """Write one run's scan.yaml, run it when asked, write summary.json; returns 1 on a failure, else 0."""
    source = transcript_source(run_data, args.source)
    if source is None:
        print(f"{NAME}: {run_data.name} has no transcripts for --source {args.source}", file=sys.stderr)
        return 1
    out = tools_dir(run_data, NAME)
    config = scan_config(
        name=f"{run_data.name.replace('/', '-')}-scan",
        source=source,
        scanners=scanners,
        run_directory=run_data.directory,
        model=model,
        scanner_module=args.scanner_module,
        package_dir=SCANNER_PACKAGE_DIR,
        repository_root=args.scanner_root.resolve(),
    )
    config_path = write_scan_config(out / "scan.yaml", config)
    command = hawk_command(config_path, args.hawk_spec, HAWK_PYTHON, args.direct)
    print(f"cd {shlex.quote(str(out))} && {shlex.join(command)}", flush=True)
    notes = list(LOCAL_NOTES)
    if source.kind == "eval_log":
        notes.append(EVAL_LOG_NOTE)
        if len(list(source.path.glob("*.eval"))) > 1:
            notes.append(f"{source.path} holds more than one .eval; the scan reads all of them.")
    returncode, tail = None, []
    before = scan_directories(out)
    if args.run:
        returncode, tail = run_hawk(command, out, args.timeout)
        print(f"{NAME}: {run_data.name}: hawk exited {returncode}", file=sys.stderr)
    results = [path for path in scan_directories(out) if path not in before]
    statuses = [scan_status(path) for path in results]
    complete = all(status["complete"] for status in statuses) if args.run else None
    scan_errors = sum(status["errors"] for status in statuses)
    if args.run and (not results or not complete or scan_errors):
        print(f"{NAME}: {run_data.name}: {len(results)} scan directories, complete {complete}, "
              f"{scan_errors} scanner errors", file=sys.stderr)
    write_summary(run_data, NAME, {
        "config": str(config_path),
        "command": shlex.join(command),
        "cwd": str(out),
        "ran": args.run,
        "returncode": returncode,
        "stdout_tail": tail,
        "needs_server": False,
        "notes": notes,
        "source": {"kind": source.kind, "path": str(source.path)},
        "scanners": scanners,
        "model": model,
        "results": [str(path) for path in results],
        "complete": complete,
        "scan_errors": scan_errors,
    })
    if not args.run:
        return 0
    return 0 if returncode == 0 and results and complete and not scan_errors else 1


def scan_directories(out: Path) -> list[Path]:
    """The scan result directories under out/results (results/local-scan-<job>/scan_id=<id>)."""
    return sorted(path for path in (out / "results").glob("*/scan_id=*") if path.is_dir())


def scan_status(result_dir: Path) -> dict:
    """Whether a scan directory's _summary.json says complete, and its scanner errors summed over scanners."""
    summary_path = result_dir / "_summary.json"
    if not summary_path.is_file():
        return {"complete": False, "errors": 0}
    summary = json.loads(summary_path.read_text())
    errors = sum(int(entry.get("errors") or 0) for entry in (summary.get("scanners") or {}).values())
    return {"complete": bool(summary.get("complete")), "errors": errors}


def run_hawk(command: list[str], cwd: Path, timeout: int) -> tuple[int, list[str]]:
    """Run hawk in the run's tool directory, where results/ lands; returns the exit code and the output's tail.
    On a timeout the whole process group is killed, so hawk's runner does not keep calling the model."""
    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               start_new_session=True)
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        output, _ = process.communicate()
        return 124, (output or "").splitlines()[-TAIL_LINES:] + [f"timed out after {timeout} s"]
    return process.returncode, output.splitlines()[-TAIL_LINES:]
