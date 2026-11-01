"""What each cell of the study cost, measured: what the provider billed for its runs' models, and its runs' share of the
boxes that held them.

    uv run python -m scripts.box.cell_cost_readout                         # every stage of the study
    uv run python -m scripts.box.cell_cost_readout --stage phase2-flipt-luna
    uv run python -m scripts.box.cell_cost_readout --json                  # the same, as one JSON document
    uv run python -m scripts.box.cell_cost_readout --root <checkout>       # another checkout's runs/ and logs/

A cell is one arm of study_table.py: a stage, a target, a setting, a regulator and a level. Its runs are the arm's run
names that left a run folder or an eval log folder, or that a box held. A mock rehearsal belongs to no cell.

Models. A run costs what the provider billed for its agents: each episode's usage.billed_usd in episodes.jsonl,
OpenRouter's usage.cost summed by the export. The figure falls short, and the cell is partial, when the run has no
export, an agent was billed for fewer calls than it made, or an earlier attempt left an eval log the export did not
count. Calls that errored are named apart from calls that went through. A partial cell shows what was measured and
never reads as complete.

Boxes. A box bills its offer's $/h (dph in runs/boxes.json) from created to destroyed, or to now while it lives. Its
cost splits evenly over the cell runs it held. Its capture manifest (logs/boxes/<id>/manifest.jsonl) names them. A box
not captured yet has its runner plans instead (logs/<study>/screen/box_<box>.json and attempts/), those that started
while it lived. A mock rehearsal takes no share. A box that held no cell run belongs to no cell: the readout lists it
apart, and the totals count it.

Whole and valid are src.measures.run_validity's. Nothing here writes. study_table.py states study_exploration_1, and
study_exploration_0 reuses its run names, so the readout refuses any other study.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts.box.fleet_layout import BOXES, REGISTRY, REGULATORS, REMOTE_REPO, ROOT, cells, run_name
from scripts.box.study_table import STAGES, stage_boxes
from src.configuration import settings
from src.measures.run_validity import MOCK, validity
from src.utils.json_files import read_json, read_jsonl

TABLE_STUDY = "study_exploration_1"
"""The study study_table.py states."""
CELL_KEYS = ("stage", "target", "setting", "regulator", "level")
ERRORED = "errored call(s) with no billed cost"
"""The reason for calls that errored. No billed figure covers them, so their run stays short. The readout names them
apart from calls that went through, so a cell short by errored calls alone says so."""


def table_cells() -> list[dict]:
    """Every arm of the study table as a cell: its stage, target, setting, regulator, level, and run names by seed."""
    found, owner = [], set()
    for stage in STAGES:
        for box in stage_boxes(stage):
            for arm in box.arms:
                script = cells(arm.target.task, (arm.kind,))[0]
                names = [run_name(script, arm.suffix, seed) for seed in arm.seeds]
                if owner & set(names):
                    raise ValueError(f"{sorted(owner & set(names))} name runs of two cells")
                owner |= set(names)
                found.append({"stage": stage, "target": arm.target.name, "setting": arm.kind,
                              "regulator": REGULATORS[arm.regulator] if arm.regulator else None,
                              "level": arm.regulator_level or arm.rule_level, "names": names})
    return found


def when(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def run_of(path: str, study: str) -> str | None:
    """The run a file on a box belongs to: runs/<study>/<run>/..., logs/<study>/<run>/..., or the run's screen log,
    logs/<study>/screen/<run>.log or attempts/<run>.<n>.log. None for any other file."""
    for top in ("runs", "logs"):
        prefix = f"{REMOTE_REPO}/{top}/{study}/"
        if path.startswith(prefix):
            first, _, rest = path[len(prefix):].partition("/")
            return Path(rest).name.split(".", 1)[0] if top == "logs" and first == "screen" else first
    return None


def box_records(root: Path, study: str, names: set[str], now: datetime) -> list[dict]:
    """Each registered box of the table's stages: its hours, its cost, the cell runs it held and its mock rehearsals."""
    registry = read_json(root / REGISTRY.relative_to(ROOT)) or {"boxes": []}
    screen = root / "logs" / study / "screen"
    plans = [read_json(path) for path in sorted([*screen.glob("box_*.json"), *screen.glob("attempts/box_*.json")])]
    records = []
    for entry in registry["boxes"]:
        if entry.get("stage") not in STAGES:
            continue
        live = not entry.get("destroyed")
        start, end = when(entry["created"]), now if live else when(entry["destroyed"])
        manifest = root / BOXES.relative_to(ROOT) / str(entry["id"]) / "manifest.jsonl"
        if manifest.is_file():
            held, source = {run_of(row.get("box", ""), study) for row in read_jsonl(manifest)}, "capture manifest"
        else:
            held = {name for plan in plans if plan.get("box") == entry.get("box") and plan.get("started")
                    and start <= when(plan["started"]) <= end for name in plan.get("runs", {})}
            source = "runner plans"
        hours = (end - start).total_seconds() / 3600
        dph = entry.get("dph")
        records.append({"id": entry["id"], "box": entry.get("box"), "stage": entry["stage"], "live": live,
                        "created": entry["created"], "destroyed": entry.get("destroyed"), "hours": hours, "dph": dph,
                        "usd": None if dph is None else hours * dph, "source": source, "held": sorted(held & names),
                        "rehearsals": sorted(name for name in held if name and MOCK.search(name))})
    return records


def model_cost(run_dir: Path, log_dir: Path) -> tuple[float, list[str]]:
    """(what the provider billed for the run's agents, why that falls short of what the run's models cost). A call
    counts as billed when the export kept its billed cost; the ledger counts every call and the errored ones."""
    run = read_json(run_dir / "run.json")
    if run is None:
        return 0.0, ["no export"]
    usd, short, errored, other = 0.0, [], 0, 0
    episodes = read_jsonl(run_dir / "episodes.jsonl")
    if not episodes:
        short.append("no episodes")
    for episode in episodes:
        usage, ledger = episode.get("usage") or {}, episode.get("ledger") or {}
        usd += usage.get("billed_usd") or 0.0
        if ledger.get("calls") is None:
            short.append(f"agent {episode.get('orbit_agent')} has no ledger")
            continue
        unbilled = max(0, ledger["calls"] - (usage.get("billed_calls") or 0))
        failed = min(unbilled, ledger.get("errored") or 0)
        errored, other = errored + failed, other + unbilled - failed
    if other:
        short.append(f"{other} call(s) with no billed cost")
    if errored:
        short.append(f"{errored} {ERRORED}")
    counted = Path(run.get("eval_log") or "").name
    earlier = [path for path in sorted(log_dir.glob("*.eval")) if path.name != counted]
    if earlier:
        short.append(f"{len(earlier)} earlier eval log(s) the export did not count")
    return usd, short


def run_record(name: str, root: Path, study: str, holders: list[dict]) -> dict:
    """One run of a cell: whole and valid, its billed model cost, its share of each box that held it, and why its cost
    falls short."""
    run_dir, logs = root / "runs" / study / name, root / "logs" / study
    try:
        verdict = validity(run_dir, logs)
        models, short = model_cost(run_dir, logs / name)
    except Exception as error:  # noqa: BLE001 - one unreadable run must not hide every other run's cost
        verdict, models, short = {"whole": False, "valid": False}, 0.0, [f"unreadable: {type(error).__name__}: {error}"]
    shares = [box["usd"] / len(box["held"]) for box in holders if box["usd"] is not None]
    if len(shares) < len(holders):
        short.append("a box that held it has no $/h")
    return {"run": name, "whole": verdict["whole"], "valid": verdict["valid"], "models_usd": models,
            "boxes_usd": sum(shares), "total_usd": models + sum(shares), "boxes": [box["id"] for box in holders],
            "live": any(box["live"] for box in holders), "short": short}


def readout(root: Path = settings.ROOT, study: str = TABLE_STUDY, stage: str | None = None,
            now: datetime | None = None) -> dict:
    """Every cell with a run (of one stage, or all), each with its runs; the boxes of those stages; the totals."""
    if study != TABLE_STUDY:
        raise ValueError(f"study_table.py states {TABLE_STUDY}, not {study}; study_exploration_0 reuses its run names, "
                         f"so another study's runs would land in the wrong cells")
    if stage is not None and stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; choose one of {list(STAGES)}")
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    table = table_cells()
    names = {name for cell in table for name in cell["names"]}
    boxes = box_records(root, study, names, now)
    holders: dict[str, list[dict]] = {}
    for box in boxes:
        for name in box["held"]:
            holders.setdefault(name, []).append(box)
    rows, idle = [], 0
    for cell in table:
        if stage and cell["stage"] != stage:
            continue
        found = [name for name in cell["names"] if name in holders or (root / "runs" / study / name).is_dir()
                 or (root / "logs" / study / name).is_dir()]
        if not found:
            idle += 1
            continue
        runs = [run_record(name, root, study, holders.get(name, [])) for name in found]
        models, shares = sum(r["models_usd"] for r in runs), sum(r["boxes_usd"] for r in runs)
        rows.append({**{key: cell[key] for key in CELL_KEYS}, "planned": len(cell["names"]), "n_runs": len(runs),
                     "whole": sum(r["whole"] for r in runs), "valid": sum(r["valid"] for r in runs),
                     "models_usd": models, "boxes_usd": shares, "total_usd": models + shares,
                     "per_run_usd": (models + shares) / len(runs), "partial": any(r["short"] for r in runs),
                     "live": sum(r["live"] for r in runs), "runs": runs})
    chosen = [box for box in boxes if stage in (None, box["stage"])]
    unheld = [box for box in chosen if not box["held"]]
    all_runs = [run for row in rows for run in row["runs"]]
    models = sum(row["models_usd"] for row in rows)
    shares = sum(row["boxes_usd"] for row in rows)
    unheld_usd = sum(box["usd"] for box in unheld if box["usd"] is not None)
    totals = {"cells": len(rows), "cells_without_runs": idle, "runs": len(all_runs),
              "whole": sum(r["whole"] for r in all_runs), "valid": sum(r["valid"] for r in all_runs),
              "short_runs": sum(bool(r["short"]) for r in all_runs), "models_usd": models, "boxes_usd": shares,
              "unheld_boxes_usd": unheld_usd, "total_usd": models + shares + unheld_usd,
              "partial": any(r["short"] for r in all_runs) or any(box["usd"] is None for box in unheld),
              "live_boxes": [box["id"] for box in chosen if box["live"]]}
    study_dir = root / "runs" / study
    folders = sorted(p.name for p in study_dir.iterdir() if p.is_dir()) if study_dir.is_dir() else []
    outside = [name for name in folders if name not in names and not MOCK.search(name)]
    return {"study": study, "stage": stage, "now": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "cells": rows,
            "boxes": chosen, "unheld_boxes": [box["id"] for box in unheld], "totals": totals,
            "rehearsal_folders": sum(bool(MOCK.search(name)) for name in folders), "outside_folders": outside}


def money(usd: float | None) -> str:
    return "-" if usd is None else f"${usd:,.2f}"


HEADS = (("stage", 27), ("target", 14), ("setting", 18), ("regulator", 10), ("level", 11))


def cell_line(row: dict) -> str:
    """One cell: its labels, runs of planned, whole, valid, the money, and whether every run's cost was measured."""
    short = [reason for run in row["runs"] for reason in run["short"]]
    note = f"partial: {sum(bool(r['short']) for r in row['runs'])} run(s) short" if short else "complete"
    if short and all(reason.endswith(ERRORED) for reason in short):
        note += " by errored calls alone"
    if row["live"]:
        note += f", {row['live']} on a live box"
    labels = "".join(f"{row[key] or '-':{width}s}" for key, width in HEADS)
    counts = f"{row['n_runs']:>3d}/{row['planned']:<2d}{row['whole']:>6d}{row['valid']:>6d}"
    usd = "".join(f"{money(row[key]):>{width}s}" for key, width in
                  (("models_usd", 11), ("boxes_usd", 10), ("total_usd", 11), ("per_run_usd", 10)))
    return f"{labels}{counts}{usd}  {note}"


def box_line(box: dict) -> str:
    """One box that held no cell run, and what its record shows."""
    if box["source"] == "capture manifest":
        held = f"its capture shows {'only mock rehearsals' if box['rehearsals'] else 'no run'}"
    else:
        held = f"not captured; {'its plans show only mock rehearsals' if box['rehearsals'] else 'no plan of it here'}"
    rate = "-" if box["dph"] is None else f"${box['dph']:.3f}"
    return (f"   {box['id']} {box['box'] or '-':28s} {box['hours']:6.2f} h at {rate}/h = {money(box['usd'])}  "
            f"{held}{', live' if box['live'] else ''}")


def text(result: dict) -> str:
    """The readout as a table, the reasons the partial cells fall short, the boxes that held no cell run, the totals."""
    lines = [f"{result['study']}, {result['stage'] or 'every stage'}; a live box bills to {result['now']}", "",
             "".join(f"{head:{width}s}" for head, width in HEADS)
             + f"{'runs':>6s}{'whole':>6s}{'valid':>6s}{'models':>11s}{'boxes':>10s}{'total':>11s}{'per run':>10s}"
             + "  measured"]
    lines += [cell_line(row) for row in result["cells"]]
    short = [run for row in result["cells"] for run in row["runs"] if run["short"]]
    if short:
        lines += ["", "Why the partial cells fall short (their figures are lower bounds):"]
        lines += [f"   {run['run']:56s} {'; '.join(run['short'])}" for run in short]
    unheld = [box for box in result["boxes"] if box["id"] in result["unheld_boxes"]]
    if unheld:
        lines += ["", "Boxes that held no cell run (counted in the total, in no cell):"]
        lines += [box_line(box) for box in unheld]
    t = result["totals"]
    lines += ["", f"{t['cells']} cells with runs ({t['cells_without_runs']} without), {t['runs']} runs: {t['whole']} "
                  f"whole, {t['valid']} valid",
              f"models  {money(t['models_usd']):>11s}  billed by the provider"
              + (f"; a lower bound: {t['short_runs']} run(s) short" if t["short_runs"] else ""),
              f"boxes   {money(t['boxes_usd']):>11s}  apportioned to the runs they held",
              f"        {money(t['unheld_boxes_usd']):>11s}  on boxes that held no cell run",
              f"total   {money(t['total_usd']):>11s}" + ("  partial: a lower bound" if t["partial"] else "")]
    if t["live_boxes"]:
        lines.append(f"live boxes, billed to now: {', '.join(str(i) for i in t['live_boxes'])}")
    if result["stage"] is None:
        lines.append(f"{result['rehearsal_folders']} mock rehearsal folders hold no cell's cost; folders outside the "
                     f"table: {', '.join(result['outside_folders']) or 'none'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=list(STAGES), help="one stage of study_table.py; every stage by default")
    parser.add_argument("--study", default=settings.STUDY, help=f"the study to read; only {TABLE_STUDY} has a table")
    parser.add_argument("--root", type=Path, default=settings.ROOT, help="the checkout whose runs/ and logs/ to read")
    parser.add_argument("--json", action="store_true", help="print one JSON document")
    args = parser.parse_args(argv)
    try:
        result = readout(args.root, args.study, args.stage)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, indent=1) if args.json else text(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
