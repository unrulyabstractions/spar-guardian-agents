"""The vast.ai fleet's command line. scripts/box/README.md gives the order of the steps.

    uv run python -m scripts.box.fleet jobs [<stage>]
    uv run python -m scripts.box.fleet offers [--top 20] [--max-dph 1.0]
    uv run python -m scripts.box.fleet create --stage <stage> --count N [--only <box> ...]
                                              [--label-prefix spar-screen] [--max-dph 1.0] [--max-fleet-dph 15]
                                              [--commit SHA] [--dry-run]
    uv run python -m scripts.box.fleet status
    uv run python -m scripts.box.fleet ids [--dph] [<id> ...]
    uv run python -m scripts.box.fleet entry <id> <field> ...
    uv run python -m scripts.box.fleet repin <id> ... --commit SHA
    uv run python -m scripts.box.fleet run-args <id> [--mock]
    uv run python -m scripts.box.fleet destroy <id> [--dry-run] [--accept-loss REASON]

jobs prints a stage's boxes and every run with its flags, from study_table.py alone: it needs no key and sends nothing.
offers reads the on-demand offers that can hold a box. create is fleet_create.py, status fleet_status.py and destroy
fleet_destroy.py. ids prints the registered boxes not yet destroyed. entry prints a box's fields for the shell scripts.
repin moves boxes to a commit origin/main holds, when the launch's package list is still the .venv's. run-args prints
box_runner.py run's arguments for a box. It refuses a model that needs a key a box does not hold, and a run name this
machine holds from a run made elsewhere, which the watch would overwrite.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from scripts.box import fleet_registry
from scripts.box.fleet_create import MAX_FLEET_DPH, cmd_create
from scripts.box.fleet_destroy import cmd_destroy
from scripts.box.fleet_layout import LABEL_PREFIX, REMOTE_REPO, capacity_of, floor_of
from scripts.box.fleet_registry import load_registry, open_entry, update_box
from scripts.box.fleet_status import cmd_status
from scripts.box.study_table import STAGES, box_jobs, find_box, jobs_sha256, key_problems, stage_boxes
from scripts.box.vast_api import Vast
from scripts.box.vast_offers import MAX_DPH, OFFER_HEADER, candidates, offer_row, rejections, search_call
from src.configuration import settings

ENTRY_FIELDS = ("task", "stage", "box", "key", "label", "launch", "commit", "requirements", "requirements_sha256",
                "python_version", "uv_version", "cores", "ram_gb", "dph", "min_cores", "min_ram_gb", "min_disk_gb")


def cmd_jobs(args: argparse.Namespace) -> int:
    for stage in [args.stage] if args.stage else STAGES:
        boxes = stage_boxes(stage)
        print(f"== {stage}: {len(boxes)} boxes, {sum(len(box_jobs(box)) for box in boxes)} runs, "
              f"{sum(box.agents for box in boxes)} agents")
        for box in boxes:
            cores, ram_gb = floor_of(box.task)
            print(f"{box.name}: {box.task}, at least {cores} cores and {ram_gb} GB of RAM, {box.agents} agents at once "
                  f"of the {capacity_of(box.task)} it holds")
            for job in box_jobs(box):
                print(f"   {job.name:52s} examples.{job.cell} --model {job.model} --set seed={job.seed} "
                      + " ".join(job.flags))
    return 0


def cmd_offers(args: argparse.Namespace) -> int:
    offers = Vast(writes=False).send(search_call())["offers"]
    kept = candidates(offers, args.max_dph)
    reasons = Counter(reason for offer in offers for reason in rejections(offer, args.max_dph))
    print(f"== {len(offers)} offers searched, {len(kept)} can hold a box; "
          f"{len({o.get('machine_id') for o in kept})} machines")
    for reason, n in reasons.most_common():
        print(f"   {n:4d} rejected: {reason}")
    print(OFFER_HEADER)
    for offer in kept[:args.top]:
        print(offer_row(offer))
    return 0


def cmd_ids(args: argparse.Namespace) -> int:
    open_boxes = [box for box in load_registry()["boxes"] if not box["destroyed"]]
    if args.ids:
        unknown = sorted(set(args.ids) - {box["id"] for box in open_boxes})
        if unknown:
            print(f"refused: not registered boxes that are not destroyed: {unknown}", file=sys.stderr)
            return 1
        open_boxes = [box for box in open_boxes if box["id"] in args.ids]
    for box in open_boxes:
        print(f"{box['id']} {box['dph'] or 0:.3f}" if args.dph else box["id"])
    if args.dph:
        print(f"total {sum(box['dph'] or 0 for box in open_boxes):.3f}")
    return 0


def cmd_entry(args: argparse.Namespace) -> int:
    try:
        values = open_entry(args.id)
    except (RuntimeError, OSError, KeyError, ValueError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    for name in args.fields:
        print("" if values.get(name) is None else values[name])
    return 0


def cmd_repin(args: argparse.Namespace) -> int:
    try:
        pin = fleet_registry.fleet_pin(args.commit)
        entries = [open_entry(instance_id) for instance_id in args.ids]
    except (RuntimeError, OSError, KeyError, ValueError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    other = [entry["id"] for entry in entries if entry["requirements_sha256"] != pin["requirements_sha256"]]
    if other:
        print(f"refused: the .venv's package list is not the one {other} pinned; a new list takes a new launch",
              file=sys.stderr)
        return 1
    for entry in entries:
        update_box(entry["id"], commit=pin["commit"])
        print(f"{entry['id']}: {entry['commit']} to {pin['commit']}; run box_setup.sh {entry['id']} before box_run.sh")
    return 0


def made_elsewhere(name: str) -> str | None:
    """What this machine holds of a run of this name that no box made: a run.json whose eval log is not under the
    box's clone, or a screen log whose command is not the box's."""
    run = settings.RUNS / name / "run.json"
    if run.exists():
        eval_log = json.loads(run.read_text()).get("eval_log") or ""
        if not eval_log.startswith(REMOTE_REPO + "/"):
            return f"{run.parent} (its eval log {eval_log or 'unknown'})"
    log = settings.LOGS / "screen" / f"{name}.log"
    if log.exists():
        with open(log) as handle:
            if f"{REMOTE_REPO}/" not in handle.readline():
                return str(log)
    return None


def cmd_run_args(args: argparse.Namespace) -> int:
    try:
        values = open_entry(args.id)
        jobs = box_jobs(find_box(values["box"]), args.mock)
    except (RuntimeError, OSError, KeyError, ValueError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    problems = key_problems(jobs)
    if problems:
        print("refused: " + "; ".join(problems), file=sys.stderr)
        return 1
    clashes = list(filter(None, (made_elsewhere(job.name) for job in jobs)))
    if clashes:
        print("refused: this machine holds runs of these names made elsewhere, which the watch would overwrite: "
              + ", ".join(clashes), file=sys.stderr)
        return 1
    print("\n".join(["--box", values["box"], "--commit", values["commit"], "--jobs-sha256", jobs_sha256(jobs),
                     *(["--mock"] if args.mock else [])]))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    jobs = commands.add_parser("jobs", help="every box, run and flag of a stage or of the study (reads the table)")
    jobs.add_argument("stage", nargs="?", choices=list(STAGES))
    offers = commands.add_parser("offers", help="the offers that can hold a box, cheapest first (reads only)")
    offers.add_argument("--top", type=int, default=20)
    offers.add_argument("--max-dph", type=float, default=MAX_DPH, help="the dearest offer, $/h with the disk")
    create = commands.add_parser("create", help="rent one stage's boxes and register each")
    create.add_argument("--stage", required=True, choices=list(STAGES))
    create.add_argument("--count", type=int, required=True, help="the number of boxes, which must match")
    create.add_argument("--only", nargs="+", metavar="BOX", help="only these boxes of the stage, for a replacement")
    create.add_argument("--label-prefix", default=LABEL_PREFIX)
    create.add_argument("--skip-machine", nargs="+", type=int, default=[], metavar="MACHINE",
                        help="machines to take no offer from: a VM there came up smaller than its offer")
    create.add_argument("--max-dph", type=float, default=MAX_DPH, help="the dearest offer, $/h with the disk")
    create.add_argument("--max-fleet-dph", type=float, default=MAX_FLEET_DPH,
                        help="what the fleet may bill per hour in all, its registered boxes included")
    create.add_argument("--commit", default="HEAD", help="what the boxes run, a commit origin/main holds")
    create.add_argument("--dry-run", action="store_true", help="search and print every request; send none")
    commands.add_parser("status", help="the account's instances with the registry; writes each box's ssh_config")
    ids = commands.add_parser("ids", help="the registered boxes not yet destroyed")
    ids.add_argument("ids", type=int, nargs="*", help="only these, each of which must be one")
    ids.add_argument("--dph", action="store_true", help="each box's $/h, and the total last")
    entry = commands.add_parser("entry", help="fields of a registered box and its launch, one per line")
    entry.add_argument("id", type=int)
    entry.add_argument("fields", nargs="+", choices=ENTRY_FIELDS)
    repin = commands.add_parser("repin", help="move boxes to a commit origin/main holds; box_setup.sh checks it out")
    repin.add_argument("ids", type=int, nargs="+")
    repin.add_argument("--commit", required=True, help="what the boxes run from now on")
    run_args = commands.add_parser("run-args", help="box_runner.py run's arguments for a box, one per line")
    run_args.add_argument("id", type=int)
    run_args.add_argument("--mock", action="store_true", help="the box's arms on the mock model, one seed each")
    destroy = commands.add_parser("destroy", help="destroy one box once its capture holds")
    destroy.add_argument("id", type=int)
    destroy.add_argument("--dry-run", action="store_true", help="every gate, the capture check and the snapshot; no "
                                                                "destroy")
    destroy.add_argument("--accept-loss", metavar="REASON",
                         help="destroy a box that can never pass the capture, recording what is lost and why")
    args = parser.parse_args(argv)
    command = {"jobs": cmd_jobs, "offers": cmd_offers, "create": cmd_create, "status": cmd_status, "ids": cmd_ids,
               "entry": cmd_entry, "repin": cmd_repin, "run-args": cmd_run_args,
               "destroy": cmd_destroy}[args.command]
    return command(args)


if __name__ == "__main__":
    sys.exit(main())
