"""`python -m tools failures <run>... [--model ENTRY] [--methods ...] [--max-steps N] [--no-mast] [--no-who-when]`."""
from __future__ import annotations

import argparse
import json
import sys

from inspect_ai.model import GenerateConfig

from tools import DEFAULT_MODEL
from tools.failures.fetch_mast_taxonomy import FETCH_COMMAND, taxonomy_present
from tools.failures.mast import annotate_run
from tools.failures.who_when import METHODS, attribute_run
from tools.run_reader import load_run, model_string, tools_dir, write_summary

NAME = "failures"
HELP = "MAST failure modes and Who&When attribution of the run's merged trace (LLM calls)"
DEFAULT_MAX_STEPS = 60
DEFAULT_SEED = 0
# Who&When's inference.py sends max_tokens 1024, sized for models without reasoning. A reasoning model such as
# gpt-6-luna spends that on reasoning and returns no text on the longer runs, so the default here is larger.
# The MAST notebook sets no limit, and neither does MAST here.
DEFAULT_WHO_WHEN_MAX_TOKENS = 16384


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("runs", nargs="+", help="run names under runs/ or run directories")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"registry entry or Inspect model string "
                        f"(default {DEFAULT_MODEL})")
    parser.add_argument("--methods", default=",".join(METHODS),
                        help=f"comma-separated Who&When methods (default {','.join(METHODS)})")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS,
                        help=f"cap on step_by_step's calls per run (default {DEFAULT_MAX_STEPS})")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_WHO_WHEN_MAX_TOKENS,
                        help=f"max_tokens of each Who&When call (default {DEFAULT_WHO_WHEN_MAX_TOKENS}; "
                        "Who&When used 1024)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help=f"seed of binary_search's random half on an ambiguous reply (default {DEFAULT_SEED})")
    parser.add_argument("--no-mast", action="store_true", help="skip the MAST annotation")
    parser.add_argument("--no-who-when", action="store_true", help="skip the Who&When attribution")


def run(args: argparse.Namespace) -> int:
    methods = [method.strip() for method in args.methods.split(",") if method.strip()]
    unknown = [method for method in methods if method not in METHODS]
    if unknown:
        print(f"failures: unknown methods {unknown}; choose from {list(METHODS)}", file=sys.stderr)
        return 2
    if args.max_steps < 1:
        print("failures: --max-steps must be at least 1", file=sys.stderr)
        return 2
    if args.no_mast and args.no_who_when:
        print("failures: --no-mast and --no-who-when leave nothing to do", file=sys.stderr)
        return 2
    if not args.no_mast and not taxonomy_present():
        print(f"failures: the MAST taxonomy files are missing or altered; fetch them with `{FETCH_COMMAND}`",
              file=sys.stderr)
        return 2
    if args.max_tokens < 1:
        print("failures: --max-tokens must be at least 1", file=sys.stderr)
        return 2
    try:
        model = model_string(args.model)
    except ValueError as error:
        print(f"failures: {error}", file=sys.stderr)
        return 2
    status = 0
    for name in args.runs:
        status = max(status, label_run(name, args, model, methods))
    return status


def label_run(name: str, args: argparse.Namespace, model: str, methods: list[str]) -> int:
    """MAST and Who&When on one run; writes mast.json, who_when.json and summary.json. Returns 0 or 1."""
    try:
        run = load_run(name)
    except (FileNotFoundError, ValueError) as error:
        print(f"failures: {error}", file=sys.stderr)
        return 1
    directory = tools_dir(run, NAME)
    payload = {"model": model, "who_when_max_tokens": None if args.no_who_when else args.max_tokens, "mast": None,
               "who_when": None}
    failed = False
    if not args.no_mast:
        mast = annotate_run(run, model, GenerateConfig())
        (directory / "mast.json").write_text(json.dumps(mast, indent=2, ensure_ascii=False) + "\n")
        failed = failed or mast["raw"] is None
        payload["mast"] = {key: mast[key] for key in ("modes", "summary", "task_completed")}
        payload["mast"]["yes"] = [mode for mode, label in mast["modes"].items() if label == "yes"]
        payload["mast"]["modes_strict"] = mast.get("modes_strict", {})
        payload["mast"]["yes_strict"] = [mode for mode, label in mast.get("modes_strict", {}).items() if label == "yes"]
        payload["mast"]["disagreements"] = mast.get("disagreements", [])
        payload["mast"]["renamed"] = mast.get("renamed", {})
    if not args.no_who_when:
        config = GenerateConfig(max_tokens=args.max_tokens)
        who_when = attribute_run(run, methods, model, config, args.max_steps, args.seed)
        (directory / "who_when.json").write_text(json.dumps(who_when, indent=2, ensure_ascii=False) + "\n")
        failed = failed or any(result.get("error") for result in who_when.values())
        keys = ("agent", "step", "step_agent", "round", "kind", "calls", "capped")
        payload["who_when"] = {method: {key: result.get(key) for key in keys} for method, result in who_when.items()}
    path = write_summary(run, NAME, payload)
    print(f"failures: {run.name}: wrote {path}")
    return 1 if failed else 0
