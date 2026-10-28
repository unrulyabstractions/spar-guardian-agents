"""The appendix figures and tables of a screen: for each of the four cells of the first experiment, every agent's rung
on the code ladder per domain and sample, and the runs' turns, tokens and billed cost.

    uv run python scripts/appendix.py [--suffix flashpin] [--samples 3]

Reads runs/<cell>_<suffix><k>/ (signals.jsonl, episodes.jsonl) and writes tmp/appendix_<section>.png (the rung tiles),
tmp/appendix_<section>_table.png (one table) and tmp/appendix_<section>.csv for the sections solo, solo_cheat, minimal
and minimal_cheat. A run that has not finished shows as an empty tile and a "(not finished)" row.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import matplotlib
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from tables import INK, INK2, table_png  # noqa: E402

from src.configuration import settings  # noqa: E402

matplotlib.use("Agg")
OUT = ROOT / "tmp"
SECTIONS = {
    "solo": ("Solo agent", ["riemann_solo", "worktime_solo", "flipt_solo"]),
    "solo_cheat": ("Solo agent instructed to cheat", ["riemann_solo_cheat", "worktime_solo_cheat", "flipt_solo_cheat"]),
    "minimal": ("Minimal swarm", ["riemann_minimal", "worktime_minimal", "flipt_minimal"]),
    "minimal_cheat": ("Minimal swarm instructed to cheat",
                      ["riemann_minimal_cheat", "worktime_minimal_cheat", "flipt_minimal_cheat"]),
}
DOMAINS = ["openmath", "knowledge work", "SWE"]
# The ladder's rungs as one sequential ramp, light to dark, with "none" neutral: the word sits on every tile.
RUNGS = ["none", "exposed", "tried", "opened", "used", "shipped"]
FILL = {"none": "#eeede8", "exposed": "#fde4c8", "tried": "#f9c396", "opened": "#f29a6a", "used": "#e3603f",
        "shipped": "#b8261f"}
TEXT = {"used": "#ffffff", "shipped": "#ffffff"}
TILE_W, TILE_H, GAP = 0.78, 0.46, 0.08


def run_name(cell: str, suffix: str, k: int) -> str:
    return f"{cell}_{suffix}{k}"


def load_run(name: str) -> dict | None:
    """The run's agents with their rungs and calls, and its tokens and billed cost; None before it finishes."""
    run_dir = settings.RUNS / name
    if not (run_dir / "signals.jsonl").is_file():
        return None
    rows = [json.loads(line) for line in (run_dir / "signals.jsonl").read_text().splitlines()]
    usage = {"prompt": 0, "output": 0, "billed": 0.0}
    for line in (run_dir / "episodes.jsonl").read_text().splitlines():
        u = json.loads(line).get("usage") or {}
        usage["prompt"] += u.get("input", 0)
        usage["output"] += u.get("output", 0)
        usage["billed"] += u.get("billed_usd", 0.0)
    return {"agents": [(r["agent"], r["rung"], r.get("calls", 0)) for r in rows], **usage}


def tiles_figure(section: str, title: str, cells: list[str], suffix: str, samples: int) -> Path:
    """Rows are the domains, columns the samples, one tile per agent coloured by its rung."""
    runs = {(d, k): load_run(run_name(cell, suffix, k))
            for d, cell in zip(DOMAINS, cells) for k in range(1, samples + 1)}
    agents = max((len(r["agents"]) for r in runs.values() if r), default=1)
    cell_w = agents * TILE_W + (agents - 1) * GAP
    left, top = 1.25, 0.75
    width = max(left + samples * (cell_w + 0.3) + 0.2, 0.75 + len(RUNGS) * 0.72 + 0.2)   # room for the legend
    height = top + len(DOMAINS) * (TILE_H + 0.42) + 0.55
    fig = Figure(figsize=(width, height), dpi=200)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.axis("off")
    ax.text(0.15, 0.3, title, fontsize=9.5, family="DejaVu Sans", color=INK, va="center", weight="bold")
    for j in range(samples):
        x = left + j * (cell_w + 0.3)
        ax.text(x + cell_w / 2, top - 0.08, f"sample {j + 1}", fontsize=7, family="DejaVu Sans Mono", color=INK2,
                ha="center", va="bottom")
    for i, domain in enumerate(DOMAINS):
        y = top + i * (TILE_H + 0.42) + 0.14
        ax.text(0.15, y + TILE_H / 2, domain, fontsize=8, family="DejaVu Sans", color=INK, va="center")
        for j in range(samples):
            x0 = left + j * (cell_w + 0.3)
            run = runs[(domain, j + 1)]
            seats = run["agents"] if run else [(f"a{n + 1:02d}", None, 0) for n in range(agents)]
            for n, (agent, rung, _) in enumerate(seats):
                x = x0 + n * (TILE_W + GAP)
                fill = FILL.get(rung, "#ffffff")
                ax.add_patch(FancyBboxPatch((x, y), TILE_W, TILE_H, boxstyle="round,pad=0,rounding_size=0.05",
                                            facecolor=fill, edgecolor="#c3c2b7" if rung else "#898781",
                                            linestyle="-" if rung else (0, (2, 2)), linewidth=0.6))
                ax.text(x + TILE_W / 2, y + TILE_H / 2, rung or "no run", fontsize=7, family="DejaVu Sans",
                        color=TEXT.get(rung, INK if rung else "#898781"), ha="center", va="center")
                if agents > 1:
                    ax.text(x + TILE_W / 2, y - 0.03, agent, fontsize=6, family="DejaVu Sans Mono", color=INK2,
                            ha="center", va="bottom")
    # the legend: the ramp in rung order
    y = height - 0.42
    ax.text(0.15, y + 0.11, "rung", fontsize=6.5, family="DejaVu Sans Mono", color=INK2, va="center")
    for n, rung in enumerate(RUNGS):
        x = 0.75 + n * 0.72
        ax.add_patch(FancyBboxPatch((x, y), 0.64, 0.22, boxstyle="round,pad=0,rounding_size=0.03",
                                    facecolor=FILL[rung], edgecolor="#c3c2b7", linewidth=0.5))
        ax.text(x + 0.32, y + 0.11, rung, fontsize=6, family="DejaVu Sans", color=TEXT.get(rung, INK),
                ha="center", va="center")
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"appendix_{section}.png"
    fig.savefig(path, dpi=200, facecolor="#ffffff")
    return path


def table_and_csv(section: str, cells: list[str], suffix: str, samples: int) -> tuple[Path, Path]:
    """One row per run: the agents' rungs as chips, the calls, the tokens and the billed cost."""
    rows, records = [], []
    for domain, cell in zip(DOMAINS, cells):
        for k in range(1, samples + 1):
            name = run_name(cell, suffix, k)
            run = load_run(name)
            if run is None:
                rows.append([domain, str(k), ["(not finished)"], [""], [""], [""], [""]])
                records.append({"domain": domain, "sample": k, "run": name})
                continue
            rungs = [f"`{agent}: {rung}`" for agent, rung, _ in run["agents"]]
            calls = "/".join(str(c) for _, _, c in run["agents"])
            rows.append([domain, str(k), rungs, [calls], [f"{run['prompt'] / 1e6:.1f} M"],
                         [f"{run['output'] / 1e3:.0f} k"], [f"{run['billed']:.3f}"]])
            records.append({"domain": domain, "sample": k, "run": name, "calls": calls,
                            "prompt_tokens": run["prompt"], "output_tokens": run["output"],
                            "billed_usd": round(run["billed"], 4),
                            **{f"{agent}_rung": rung for agent, rung, _ in run["agents"]}})
    header = ["domain", "sample", "rungs", "calls", "prompt", "output", "billed $"]
    table = table_png(f"appendix_{section}_table", header, rows, [1.3, 0.7, 2.6, 0.9, 0.8, 0.8, 0.8])
    csv_path = OUT / f"appendix_{section}.csv"
    fields = sorted({key for r in records for key in r}, key=lambda key: (key not in ("domain", "sample", "run"), key))
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)
    return table, csv_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suffix", default="flashpin")
    parser.add_argument("--samples", type=int, default=3)
    args = parser.parse_args(argv)
    for section, (title, cells) in SECTIONS.items():
        print(tiles_figure(section, title, cells, args.suffix, args.samples))
        for path in table_and_csv(section, cells, args.suffix, args.samples):
            print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
