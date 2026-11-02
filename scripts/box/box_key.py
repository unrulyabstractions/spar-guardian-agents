"""The key a box gets must be capped at OpenRouter, so OpenRouter itself stops the spending whatever runs on the box.

    uv run python -m scripts.box.box_key check OPENROUTER_API_KEY --box-id 58710271

box_run.sh sends a box only BOX_<name> (BOX_OPENROUTER_API_KEY): a key made for one launch, with a credit limit set
in OpenRouter's dashboard (Keys, then a limit in dollars). This check reads that key from the environment, never from
the command line, never printing it, and asks OpenRouter's GET /api/v1/key, which costs nothing. It refuses:
- no box key;
- the main key itself (its limit covers everything else on the account);
- a key with no credit limit;
- a key with less left than the box's runs could spend at their caps (each run's cost_limit_usd, its --set or the
  default in src/models/cost_cap.py, summed over the jobs study_table.py gives the box).
Exit 0 when the key passes, 1 with every reason when it does not. On 2026-11-01 phase two ran on the main key with
--allow-main-key; that flag is gone.

--main-key checks the main key itself, for box_run.sh --main-key. The user ordered it for study_exploration_1 on
2026-11-02 ("Capped key NO, JUST USE THE KEY WE HAVE"). It lifts the refusal of the main key alone: OpenRouter must
still cap the key, and enough must be left for the box's runs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from typing import Callable

from scripts.box import fleet_registry
from scripts.box.study_table import box_jobs, find_box
from src.models.cost_cap import DEFAULT_CAP_USD

KEY_URL = "https://openrouter.ai/api/v1/key"


def key_info(key: str, opener: Callable = urllib.request.urlopen) -> dict:
    """OpenRouter's record of the key (label, usage, limit, limit_remaining)."""
    request = urllib.request.Request(KEY_URL, headers={"Authorization": f"Bearer {key}"})
    with opener(request, timeout=30) as response:
        return json.load(response)["data"]


def run_cap(arguments: list[str]) -> float:
    """A job's dollar cap: its --set cost_limit_usd=X, else the default every run gets."""
    pairs = [arguments[i + 1] for i, arg in enumerate(arguments[:-1]) if arg == "--set"]
    caps = [float(pair.split("=", 1)[1]) for pair in pairs if pair.startswith("cost_limit_usd=")]
    return caps[-1] if caps else DEFAULT_CAP_USD


def box_worst_case(instance_id: int) -> tuple[int, float]:
    """(the box's runs, what they may spend at their caps), from the registry and the study table."""
    entry = fleet_registry.registered(fleet_registry.load_registry(), instance_id)
    if entry is None:
        raise ValueError(f"box {instance_id} is not in the registry")
    jobs = box_jobs(find_box(entry["box"]))
    return len(jobs), sum(run_cap(job.arguments) for job in jobs)


def refusals(box_key: str | None, main_key: str | None, info: dict | None, worst_case_usd: float) -> list[str]:
    """Every reason this key may not go to a box; empty when it may."""
    if not box_key:
        return ["no box key: make a key with a credit limit in OpenRouter's dashboard and export BOX_<name>"]
    reasons = []
    if main_key and box_key == main_key:
        reasons.append("the box key is the main key; make a separate key with a credit limit for this launch")
    if info is None:
        return reasons + ["OpenRouter did not answer for the key"]
    limit, remaining = info.get("limit"), info.get("limit_remaining")
    if limit is None:
        reasons.append("the key has no credit limit; set one in OpenRouter's dashboard")
    elif remaining is not None and remaining < worst_case_usd:
        reasons.append(f"the key has ${remaining:.2f} left, less than the ${worst_case_usd:.2f} these runs may "
                       "spend at their cap")
    return reasons


def main(argv: list[str], opener: Callable = urllib.request.urlopen) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="refuse a box key that OpenRouter does not cap")
    check.add_argument("name", help="the key's variable, e.g. OPENROUTER_API_KEY; the box key is BOX_<name>")
    check.add_argument("--box-id", type=int, required=True, help="the vast.ai instance the key goes to")
    check.add_argument("--main-key", action="store_true",
                       help="the key is <name> itself, by the user's order; its limit and what is left still count")
    args = parser.parse_args(argv)
    variable = args.name if args.main_key else f"BOX_{args.name}"
    box_key, main_key = os.environ.get(variable), None if args.main_key else os.environ.get(args.name)
    runs, worst = box_worst_case(args.box_id)
    info = None
    if box_key:
        try:
            info = key_info(box_key, opener)
        except Exception as error:  # noqa: BLE001 - any failure to read the key refuses it
            print(f"refused: OpenRouter's key record could not be read ({type(error).__name__})")
            return 1
    reasons = refusals(box_key, main_key, info, worst)
    if reasons:
        print("refused: " + "; ".join(reasons))
        return 1
    print(f"== {variable}: limit ${info['limit']:.2f}, ${info.get('limit_remaining') or 0:.2f} left, "
          f"the box's {runs} runs may spend ${worst:.2f} at their caps")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
