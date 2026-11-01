"""fleet.py create: rent the boxes of one stage and register each.

A person names the stage, and create rents that stage's boxes alone. It refuses a task without every cell or with a cell
stem another task has, a model that needs a key a box does not hold, a count that does not match the boxes asked for,
and a box the account holds already. It pins the launch before the first rent, and it
rents one offer per box in the same step as the search, since offer ids expire within minutes. It takes no offer above
--max-dph and stops before the fleet's $/h (the registered boxes not destroyed, and this launch) would pass
--max-fleet-dph. Each instance enters the registry the moment vast.ai returns its id. A rejected offer moves on to the
next one. Any other outcome (an error, a reply it cannot read, an interrupt) stops the launch, since the instance may
exist. However the launch ends, once a rent was sent an audit of the account runs: an instance with this launch's label
that the registry lacks is registered then, and any other spar-screen instance outside the registry fails the launch
and stays unregistered. --dry-run does the search and prints every request it would send, and sends none: its client
refuses anything but a read.
"""
from __future__ import annotations

import argparse
import urllib.error
from dataclasses import dataclass

from scripts.box import fleet_registry
from scripts.box.fleet_layout import (
    LABEL_PREFIX,
    box_label,
    floor_of,
    is_fleet_label,
    label_base,
    layout_problems,
    new_launch,
)
from scripts.box.fleet_registry import box_entry, load_registry, open_dph, register, write_launch
from scripts.box.study_table import Box, box_jobs, key_problems, stage_boxes
from scripts.box.vast_api import Call, Vast, credit, list_instances
from scripts.box.vast_offers import candidates, create_call, offer_row, rejections, search_call

MAX_FLEET_DPH = 15.00
"""What the fleet may bill per hour in all (its boxes not destroyed), unless --max-fleet-dph says otherwise."""
MAX_REJECTIONS = 10
"""Offers vast.ai may refuse in one launch (taken since the search) before the launch stops."""


@dataclass
class Rental:
    """One launch's rents: what it asks for, its limits and how many it has sent."""

    launch: str
    asked: dict[str, Box]
    held: float
    max_dph: float
    max_fleet_dph: float
    dry_run: bool
    sent: int = 0


def try_create(vast: Vast, call: Call) -> tuple[str, int | None, str]:
    """("created", id, ""), ("rejected", None, why) when vast.ai refused the offer and made nothing, or ("unknown",
    None, why) when the instance may exist: any other error, or a reply without an id."""
    try:
        reply = vast.send(call)
    except urllib.error.HTTPError as error:
        try:
            text = error.read().decode(errors="replace")[:300]
        except Exception as unread:  # noqa: BLE001 - the status alone decides
            text = f"(the body could not be read: {type(unread).__name__})"
        if 400 <= error.code < 500 and error.code not in (401, 403, 408, 429):
            return "rejected", None, f"HTTP {error.code}: {text}"
        return "unknown", None, f"HTTP {error.code}: {text}"
    except Exception as error:  # noqa: BLE001 - a broken read, a reply that is not JSON: the instance may exist
        return "unknown", None, f"{type(error).__name__}: {error}"
    if not isinstance(reply, dict):
        return "unknown", None, f"a reply that is not an object ({type(reply).__name__})"
    if reply.get("success") and reply.get("new_contract"):
        return "created", int(reply["new_contract"]), ""
    if reply.get("success") is False:
        return "rejected", None, str(reply.get("msg") or reply.get("error"))[:300]
    return "unknown", None, f"a reply without an instance id (keys {sorted(reply)})"


def audit(vast: Vast, asked: dict[str, Box]) -> int:
    """After a launch: register any instance with one of this launch's labels that the registry lacks, then fail on
    any other spar-screen instance outside the registry, which stays unregistered (another launch's, or a stranger)."""
    live = list_instances(vast)
    known = {box["id"] for box in load_registry()["boxes"]}
    failures = 0
    for row in live:
        if row["id"] in known or not is_fleet_label(row.get("label")):
            continue
        if row["label"] in asked:
            launch = row["label"].rsplit("-", 1)[1]
            entry = box_entry(row["id"], row["label"], asked[row["label"]], None, launch)
            if register(entry | {"dph": row.get("dph_total"), "note": "registered by the audit"}, exist_ok=True):
                print(f"   registered {row['id']} {row['label']}: the launch asked for it and did not hear back")
        else:
            print(f"!! UNREGISTERED {row['id']} {row['label']}: a spar-screen instance outside the registry, not of "
                  f"this launch")
            failures += 1
    mine = [box for box in load_registry()["boxes"] if box["label"] in asked and not box["destroyed"]]
    print(f"== audit: {len(live)} instances on the account, {len(mine)} of this launch registered")
    for box in mine:
        print(f"   {box['id']} {box['label']} offer {box['offer']} ${box['dph'] or 0:.3f}/h")
    return 1 if failures else 0


def rent(vast: Vast, rental: Rental, offers: list[dict]) -> int:
    """One offer per label, the cheapest that meets its task's floor, never past the fleet's cap."""
    remaining, rejected, spent, placed = list(offers), 0, 0.0, []
    for label, box in rental.asked.items():
        over_cap = False
        for offer in [o for o in remaining if not rejections(o, rental.max_dph, floor_of(box.task))]:
            if rental.held + spent + offer["dph_total"] > rental.max_fleet_dph:
                over_cap = True
                print(f"== stopped: offer {offer['id']} at ${offer['dph_total']:.3f}/h takes the fleet to "
                      f"${rental.held + spent + offer['dph_total']:.3f}/h, past its cap "
                      f"${rental.max_fleet_dph:.2f}/h")
                break
            remaining.remove(offer)
            call = create_call(offer["id"], label)
            if rental.dry_run:
                print(f"   would send: {call.show()}\n      offer {offer_row(offer)}")
                placed.append(label)
                spent += offer["dph_total"]
                break
            rental.sent += 1
            outcome, instance_id, why = try_create(vast, call)
            if outcome == "created":
                register(box_entry(instance_id, label, box, offer, rental.launch))
                print(f"   created {instance_id} {label} on offer {offer['id']} ${offer['dph_total']:.3f}/h")
                placed.append(label)
                spent += offer["dph_total"]
                break
            print(f"   {outcome}: offer {offer['id']} for {label}: {why}")
            if outcome == "unknown":
                return 1
            rejected += 1
            if rejected > MAX_REJECTIONS:
                print(f"== stopped: {rejected} offers refused")
                return 1
        if label not in placed and (over_cap or not rental.dry_run):
            break
    unfilled = [label for label in rental.asked if label not in placed]
    if unfilled:
        print(f"== no offer left for: {', '.join(unfilled)}")
    if rental.dry_run:
        print(f"== dry run: {len(placed)} of {len(rental.asked)} requests printed, ${spent:.3f}/h, none sent")
    return 1 if unfilled else 0


def chosen_boxes(stage: str, only: list[str] | None) -> list[Box]:
    """The stage's boxes, or those of them named, the heaviest floor first."""
    boxes = stage_boxes(stage)
    unknown = sorted(set(only or []) - {box.name for box in boxes})
    if unknown:
        raise ValueError(f"not boxes of {stage}: {', '.join(unknown)}")
    return sorted([box for box in boxes if not only or box.name in only], key=lambda box: floor_of(box.task),
                  reverse=True)


def box_problems(boxes: list[Box]) -> list[str]:
    """Why these boxes could not run their jobs: a task's cells, or a model's key."""
    problems = layout_problems(sorted({box.task for box in boxes}))
    return problems or key_problems([job for box in boxes for job in box_jobs(box)])


def cmd_create(args: argparse.Namespace) -> int:
    try:
        boxes = chosen_boxes(args.stage, args.only)
    except ValueError as error:
        print(f"refused: {error}")
        return 2
    if len(boxes) != args.count:
        print(f"--count {args.count} but {args.stage} rents {len(boxes)} boxes: say the count you mean")
        return 2
    if not args.label_prefix.startswith(LABEL_PREFIX):
        print(f"--label-prefix must start with {LABEL_PREFIX}: destroy acts on nothing else")
        return 2
    refused = False
    for problem in box_problems(boxes):
        print(f"!! {problem}")
        refused = True
    if refused and not args.dry_run:
        print("refused: a box rented for these jobs could not run them")
        return 1
    vast = Vast(writes=not args.dry_run)
    launch = new_launch()
    asked = {box_label(args.label_prefix, box.name, launch): box for box in boxes}
    bases = {label_base(label) for label in asked}
    clash = [row for row in list_instances(vast) if label_base(row.get("label") or "") in bases]
    if clash:
        print("refused: the account holds these boxes already: "
              + ", ".join(f"{row['id']} {row['label']}" for row in clash))
        return 1
    try:
        pin = fleet_registry.fleet_pin(args.commit)
    except (RuntimeError, OSError) as error:
        print(f"refused: {error}")
        return 1
    print(f"== launch {launch}: commit {pin['commit']}, package list sha256 {pin['requirements_sha256'][:16]}, "
          f"Python {pin['python_version']}, uv {pin['uv_version']}")
    print(f"== credit ${credit(vast) or 0:.2f}; {args.count} boxes; search: {search_call().show()}")
    registry = load_registry()
    dead = {box["machine_id"] for box in registry["boxes"] if box.get("start_error")}
    offers = [offer for offer in candidates(vast.send(search_call())["offers"], args.max_dph)
              if offer.get("machine_id") not in dead]
    held = open_dph(registry)
    planned = sum(offer["dph_total"] for offer in offers[:args.count])
    print(f"== {len(offers)} offers can hold a box at ${args.max_dph:.2f}/h or less; the cheapest "
          f"{min(args.count, len(offers))} bill ${planned:.3f}/h, the fleet with them ${held + planned:.3f}/h "
          f"(cap ${args.max_fleet_dph:.2f}/h)")
    if len(offers) < args.count:
        print(f"{'refused' if not args.dry_run else '!!'}: {len(offers)} offers for {args.count} boxes")
        refused = True
    if held + planned > args.max_fleet_dph:
        print(f"{'refused' if not args.dry_run else '!!'}: the fleet would bill ${held + planned:.3f}/h, past its cap "
              f"${args.max_fleet_dph:.2f}/h")
        refused = True
    if refused and not args.dry_run:
        return 1
    if not args.dry_run:
        print(f"== pinned in {write_launch(launch, pin)}")
    rental = Rental(launch, asked, held, args.max_dph, args.max_fleet_dph, args.dry_run)
    audit_code = 0
    try:
        code = rent(vast, rental, offers)
    finally:
        if rental.sent:
            audit_code = audit(vast, asked)
    if args.dry_run:
        return 1 if code or refused else 0
    return code or audit_code
