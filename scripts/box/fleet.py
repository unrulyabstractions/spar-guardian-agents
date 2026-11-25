"""The vast.ai fleet's command line. scripts/box/README.md gives the order of the steps.

    uv run python -m scripts.box.fleet plan <plan> [--gates <spec>] [--boxes N] [--max-agents M]
    uv run python -m scripts.box.fleet offers [--top 20] [--max-dph 1.0]
    uv run python -m scripts.box.fleet create --plan <plan> [--gates <spec>] --boxes N [--max-agents M]
                                              [--only <box> ...] [--label-prefix spar-screen] [--max-dph 1.0]
                                              [--skip-machine <machine> ...] [--max-fleet-dph 15] [--commit SHA]
                                              [--dry-run]
    uv run python -m scripts.box.fleet status
    uv run python -m scripts.box.fleet ids [--dph] [<id> ...]
    uv run python -m scripts.box.fleet entry <id> <field> ...
    uv run python -m scripts.box.fleet repin <id> ... --commit SHA
    uv run python -m scripts.box.fleet run-args <id> [--mock]
    uv run python -m scripts.box.fleet stop <id>
    uv run python -m scripts.box.fleet destroy <id> [--dry-run] [--accept-loss REASON]

plan prints a plan's assignment to boxes (fleet_plan.py): it needs no key and sends nothing. offers reads the
on-demand offers that can hold a box. create is fleet_create.py, status fleet_status.py and destroy fleet_destroy.py.
ids prints the registered boxes not yet destroyed. entry prints a box's fields for the shell scripts. repin moves boxes
to a commit origin/main holds, when the launch's package list is still the .venv's, and sets each box's runs and caps to
what that commit's plan gives its targets. run-args prints, one per line, what box_run.sh starts on a box after
`.venv/bin/python -m`: the launcher on the box's plan, its targets and its agents at once, as its pinned commit holds
them. It refuses a model that needs a key a box does not hold, a run of the box of which this checkout holds a trace
the box did not make (the watch would mix the two), and a launcher of the study live on this machine. stop sends
vast.ai's stop, which keeps the disk and bills storage alone, to a registered box of this repository.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

from scripts.box import fleet_layout, fleet_plan, fleet_registry
from scripts.box.fleet_create import MAX_FLEET_DPH, cmd_create, launcher_problems
from scripts.box.fleet_destroy import cmd_destroy, identity_refusals
from scripts.box.fleet_layout import LABEL_PREFIX, REMOTE_REPO, now_iso
from scripts.box.fleet_registry import entry_box, load_registry, open_entry, registered, update_box
from scripts.box.fleet_status import cmd_status
from scripts.box.vast_api import Call, Vast, instance
from scripts.box.vast_offers import MAX_DPH, OFFER_HEADER, candidates, offer_row, rejections, search_call
from scripts.study import run_study
from src.measures.run_directory import RunDir

ENTRY_FIELDS = ("study", "plan", "gates", "tasks", "box", "key", "label", "launch", "commit", "requirements",
                "requirements_sha256", "python_version", "uv_version", "cores", "ram_gb", "dph", "min_cores",
                "min_ram_gb", "min_disk_gb", "max_agents", "runs", "cap_usd")


def cmd_plan(args: argparse.Namespace) -> int:
    try:
        assignment = fleet_plan.assign(args.plan, args.gates, args.boxes, args.max_agents)
    except (OSError, ValueError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    print("\n".join(fleet_plan.lines(assignment)))
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
        value = values.get(name)
        print("" if value is None else ",".join(value) if isinstance(value, list) else value)
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
    try:
        moved = [(entry, entry_box(entry, pin["commit"])) for entry in entries]
    except ValueError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    for entry, box in moved:
        update_box(entry["id"], commit=pin["commit"], runs=len(box.runs), cap_usd=round(box.cap_usd, 2))
        print(f"{entry['id']}: {entry['commit']} to {pin['commit']}; runs {entry['runs']} to {len(box.runs)}, caps "
              f"${entry['cap_usd']:.2f} to ${box.cap_usd:.2f}; run box_setup.sh {entry['id']} before box_run.sh")
    return 0


def foreign_traces(study: str, name: str) -> list[str]:
    """The traces this checkout holds of a run of this name (fleet_plan.traces_here), unless they are a box's run the
    watch pulled: a run.json naming an eval log in a box's clone."""
    run = RunDir(run_study.RUNS / study / name)
    if run.is_run() and (run.run_json.get("eval_log") or "").startswith(REMOTE_REPO + "/"):
        return []
    return fleet_plan.traces_here(study, name)


def launcher_args(entry: dict, mock: bool) -> list[str]:
    """What the box starts after `.venv/bin/python -m`: the launcher on the plan, the spec, the box's targets and its
    agents at once, as the registry records them. The launcher decides no gates under --mock, so a mock launch drops
    the spec and names only the box's targets the pinned commit's plan holds. ValueError when it holds none of them."""
    gates, targets = ["--gates", entry["gates"]] if entry["gates"] else [], entry["targets"]
    if mock:
        pinned = fleet_layout.committed(entry["commit"], entry["plan"])
        in_plan = run_study.load_plan(Path(entry["plan"]), pinned)["targets"]
        gates, targets = [], [target for target in targets if target in in_plan]
        if not targets:
            raise ValueError(f"{entry['plan']} holds none of the box's targets yet: --mock runs only open targets")
    return ["scripts.study.run_study", entry["plan"], "--yes", *gates, "--only", *targets,
            "--limit", f"max_agents={entry['max_agents']}", *(["--mock"] if mock else [])]


def run_refusals(entry: dict, mock: bool) -> list[str]:
    """Why the box may not start its launcher: a model that needs another key, a run of the box of which this checkout
    holds a trace the box did not make, or a launcher of the study live on this machine. Its runs are those of its
    pinned commit's plan; a mock launch's carry run_study.MOCK_SUFFIX in their study and names."""
    box = entry_box(entry)
    problems = [] if mock else fleet_plan.key_problems(box)
    suffix = run_study.MOCK_SUFFIX if mock else ""
    for name in (replace(run, suffix=suffix).name for run in box.runs):
        if traces := foreign_traces(entry["study"] + suffix, name):
            problems.append(fleet_plan.trace_problem(name, traces))
    return problems + launcher_problems(entry["study"])


def cmd_run_args(args: argparse.Namespace) -> int:
    try:
        entry = open_entry(args.id)
        problems = run_refusals(entry, args.mock)
        launcher = [] if problems else launcher_args(entry, args.mock)
    except (RuntimeError, OSError, KeyError, ValueError) as error:
        problems = [str(error)]
    if problems:
        print("refused: " + "; ".join(problems), file=sys.stderr)
        return 1
    print("\n".join(launcher))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    """vast.ai's stop for a registered box of this repository: its disk stays, and it bills storage alone."""
    vast = Vast(writes=True)
    entry = registered(load_registry(), args.id)
    refusals = identity_refusals(args.id, entry, instance(vast, args.id) if entry else None)
    if refusals:
        print("refused:\n  " + "\n  ".join(refusals))
        return 1
    reply = vast.send(Call("PUT", f"/api/v0/instances/{args.id}/", body={"state": "stopped"}))
    if reply.get("success") is not True:
        print(f"!! vast.ai did not stop {args.id}: {str(reply)[:300]}")
        return 1
    update_box(args.id, stopped=now_iso())
    print(f"== stopped {args.id} {entry['label']}: its disk stays and bills storage; capture it before any destroy")
    return 0


def add_assignment_arguments(parser: argparse.ArgumentParser, plan_flag: bool) -> None:
    """The plan, its spec, the boxes and their agents at once, as plan and create take them."""
    if plan_flag:
        parser.add_argument("--plan", type=Path, required=True, help="a plan file, e.g. studies/plans/<study>.yaml")
    else:
        parser.add_argument("plan", type=Path, help="a plan file, e.g. studies/plans/<study>.yaml")
    parser.add_argument("--gates", type=Path, help="the plan's gated spec, e.g. studies/gated/<study>.yaml")
    parser.add_argument("--boxes", type=int, required=plan_flag, default=1, help="the boxes the plan runs on")
    parser.add_argument("--max-agents", type=int, help="each box's agents at once, in place of its estimate")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="a plan's assignment to boxes (reads this checkout, sends nothing)")
    add_assignment_arguments(plan, plan_flag=False)
    offers = commands.add_parser("offers", help="the offers that can hold a box, cheapest first (reads only)")
    offers.add_argument("--top", type=int, default=20)
    offers.add_argument("--max-dph", type=float, default=MAX_DPH, help="the dearest offer, $/h with the disk")
    create = commands.add_parser("create", help="rent a plan's boxes and register each")
    add_assignment_arguments(create, plan_flag=True)
    create.add_argument("--only", nargs="+", metavar="BOX", help="only these boxes of the assignment, for a "
                                                                 "replacement")
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
    run_args = commands.add_parser("run-args", help="the launcher's arguments for a box, one per line")
    run_args.add_argument("id", type=int)
    run_args.add_argument("--mock", action="store_true", help="every model of the box's runs is Inspect's mock model")
    stop = commands.add_parser("stop", help="stop one registered box: its disk stays, it bills storage alone")
    stop.add_argument("id", type=int)
    destroy = commands.add_parser("destroy", help="destroy one box once its capture holds")
    destroy.add_argument("id", type=int)
    destroy.add_argument("--dry-run", action="store_true", help="every gate, the capture check and the snapshot; no "
                                                                "destroy")
    destroy.add_argument("--accept-loss", metavar="REASON",
                         help="destroy a box that can never pass the capture, recording what is lost and why")
    args = parser.parse_args(argv)
    command = {"plan": cmd_plan, "offers": cmd_offers, "create": cmd_create, "status": cmd_status, "ids": cmd_ids,
               "entry": cmd_entry, "repin": cmd_repin, "run-args": cmd_run_args, "stop": cmd_stop,
               "destroy": cmd_destroy}[args.command]
    return command(args)


if __name__ == "__main__":
    sys.exit(main())
