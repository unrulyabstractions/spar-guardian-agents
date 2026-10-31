"""`python -m tools transect <run>... [--judge ENTRY] [--structural]`: Transect reports of a run, one per agent,
written under runs/<study>/<name>/tools/transect/."""
from __future__ import annotations

import argparse
import sys
import traceback

from transect import DigestSettings

from src.configuration.settings import JUDGE_MODEL
from src.models.model_registry import resolve
from tools.run_reader import load_runs, write_summary
from tools.transect.transect_openclaw import model_calls_by_agent
from tools.transect.transect_reports import TOOL, JudgeSettings, run_reports

NAME = "transect"
HELP = "Transect (AISI) step-by-step reports of each agent, with judged phases unless --structural"
K_ROLLS = 1
VERIFY = False
# What the phase judge reads per turn: Transect's caps for text and calls, and 300 characters of each tool result
# (Transect's default shows none), since the exploit shows up in what a command printed.
DIGEST = DigestSettings(text_chars=300, tool_call_chars=400, tool_result_chars=300)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("runs", nargs="+", help="run names of the study or run directories")
    parser.add_argument("--judge", default=None, metavar="ENTRY",
                        help=f"the phase judge: a model_registry.yaml entry or an Inspect string "
                             f"(default {JUDGE_MODEL})")
    parser.add_argument("--structural", action="store_true", help="no judge and no model calls: structural surfaces")


def judge_settings(args: argparse.Namespace) -> JudgeSettings | None:
    """The judged pass the arguments ask for, or None for a structural run."""
    if args.structural:
        return None
    return JudgeSettings(model=resolve(args.judge or JUDGE_MODEL), k_rolls=K_ROLLS, verify=VERIFY, digest=DIGEST)


def run(args: argparse.Namespace) -> int:
    if args.structural and args.judge:
        print(f"{NAME}: --judge and --structural exclude each other", file=sys.stderr)
        return 2
    try:
        judge = judge_settings(args)
    except ValueError as error:
        print(f"{NAME}: {error}", file=sys.stderr)
        return 2
    runs = load_runs(args.runs, NAME)
    if runs is None:
        return 2
    status = 0
    for loaded in runs:
        try:
            calls = model_calls_by_agent(loaded.eval_log) if loaded.eval_log is not None else {}
            payload = run_reports(loaded, judge, calls)
        except Exception:
            traceback.print_exc()
            print(f"{NAME}: {loaded.name} failed", file=sys.stderr)
            status = 1
            continue
        path = write_summary(loaded, TOOL, payload)
        print(f"{NAME}: {loaded.name} -> {path}")
        for agent, report in payload["reports"].items():
            print(f"  {agent}: {report}")
        if report_scan_problems(loaded.name, payload):
            status = 1
    return status


def report_scan_problems(run_name: str, payload: dict) -> bool:
    """Print every agent scan error and every disagreement with the run judge; True when a scan failed."""
    failed = False
    for agent, record in payload["agents"].items():
        for error in record["scan_errors"]:
            print(f"{NAME}: {run_name} {agent} scan error: {error}", file=sys.stderr)
            failed = True
        agreement = record["agreement"]
        if agreement["agrees"] is False:
            print(f"{NAME}: {run_name} {agent}: exploit_use turns {agreement['exploit_use_turns']} but the run judge "
                  f"says cheated={agreement['judge_cheated']}", file=sys.stderr)
    return failed
