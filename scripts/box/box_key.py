"""The key a box gets must be capped at OpenRouter, so OpenRouter itself stops the spending whatever runs on the box.

    uv run python -m scripts.box.box_key check OPENROUTER_API_KEY --box-id 58710271 [--main-key]

box_run.sh sends a box one key: with --main-key OPENROUTER_API_KEY itself, which every study's boxes take by the
user's word (CLAUDE.md §3a), else BOX_OPENROUTER_API_KEY, a key made for one launch with a credit limit set in
OpenRouter's dashboard. This check reads that key from the environment, never from the command line, never printing it,
and asks OpenRouter's GET /api/v1/key, which costs nothing. It refuses:
- no key;
- without --main-key, the main key itself under the box key's name (its limit covers everything else on the account);
- a key with no credit limit;
- a key with less left than the runs of every open box that holds it may spend at their caps: this box and each other
  registered box not destroyed whose entry records that it got the key of this variable (key_sent). A box's need is the
  sum of each run's cost_limit_usd (the plan's or the target's or the default in src/models/cost_cap.py) over every run
  its targets may still make, the gated arms too, as its pinned commit's plan and spec give them
  (fleet_registry.entry_box). A run whose result is stored on this machine has ended and spends nothing more, so it is
  not counted (stored_here).
Beside the need it prints what this machine's own live runs may spend at their caps, which the key may share. A pass
records key_sent in the box's entry, since box_run.sh sends the key next. Exit 0 when the key passes, 1 with every
reason when it does not.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from typing import Callable

from scripts.box import fleet_registry
from scripts.study import run_study
from src.measures.run_directory import RunDir

KEY_URL = "https://openrouter.ai/api/v1/key"


def key_info(key: str, opener: Callable = urllib.request.urlopen) -> dict:
    """OpenRouter's record of the key (label, usage, limit, limit_remaining)."""
    request = urllib.request.Request(KEY_URL, headers={"Authorization": f"Bearer {key}"})
    with opener(request, timeout=30) as response:
        return json.load(response)["data"]


def stored_here(study: str, name: str) -> bool:
    """Whether this run's result is stored on this machine. A box's run is copied here only after it has ended, and the
    box's launcher never starts a stored run again, so the run can spend nothing more."""
    return RunDir(run_study.RUNS / study / name).is_run()


def holders_need(instance_id: int, variable: str) -> list[tuple[int, int, float]]:
    """(id, runs, what they may spend at their caps) of this box, then of every other open box whose entry records the
    key of this variable, each from its targets on its pinned commit, counting only the runs whose result is not stored
    here. ValueError for a box not registered or destroyed, or a plan that no longer holds a box's targets."""
    registry = fleet_registry.load_registry()
    entry = fleet_registry.registered(registry, instance_id)
    if entry is None or entry["destroyed"]:
        raise ValueError(f"box {instance_id} is not a registered box that is not destroyed")
    others = [box for box in registry["boxes"]
              if box["id"] != instance_id and not box["destroyed"] and box.get("key_sent") == variable]
    out = []
    for box in [entry, *others]:
        plan = fleet_registry.entry_box(box)
        runs = [run for run in plan.runs if not stored_here(box["study"], run.name)]
        out.append((box["id"], len(runs), sum(run.cap_usd(list(plan.settings)) for run in runs)))
    return out


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
        reasons.append(f"the key has ${remaining:.2f} left, less than the ${worst_case_usd:.2f} the runs of the boxes "
                       "that hold it may spend at their caps")
    return reasons


def need_lines(need: list[tuple[int, int, float]], here: dict[str, float]) -> list[str]:
    """The need, box by box, then this machine's live runs beside it."""
    out = [f"   box {box_id}: {runs} runs may spend ${cap:.2f} at their caps" for box_id, runs, cap in need]
    return out + [f"   beside them, this machine's {len(here)} live runs may spend ${sum(here.values()):.2f} at their "
                  f"caps"]


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
    try:
        need = holders_need(args.box_id, variable)
    except ValueError as error:
        print(f"refused: {error}")
        return 1
    worst, runs = sum(cap for _, _, cap in need), sum(n for _, n, _ in need)
    info = None
    if box_key:
        try:
            info = key_info(box_key, opener)
        except Exception as error:  # noqa: BLE001 - any failure to read the key refuses it
            print(f"refused: OpenRouter's key record could not be read ({type(error).__name__})")
            return 1
    reasons = refusals(box_key, main_key, info, worst)
    lines = need_lines(need, run_study.live_caps())
    if reasons:
        print("refused: " + "; ".join(reasons) + "\n" + "\n".join(lines))
        return 1
    print(f"== {variable}: limit ${info['limit']:.2f}, ${info.get('limit_remaining') or 0:.2f} left; "
          f"{len(need)} boxes hold it, their {runs} runs may spend ${worst:.2f} at their caps\n" + "\n".join(lines))
    fleet_registry.update_box(args.box_id, key_sent=variable)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
