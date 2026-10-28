"""The appendix figures and tables of a screen: for each of the four cells of the first experiment, every agent's rung
on the code ladder per domain and sample, and the runs' turns, tokens and billed cost.

    uv run python scripts/paper/appendix.py [--suffix flashpin] [--samples 4]

Reads runs/<cell>_<suffix><k>/signals.jsonl and writes, for the sections solo, solo_cheat, minimal and minimal_cheat,
tmp/appendix_<section>_rate.png (the cheating rate per domain: the share of agents at opened or higher, the ladder's
rung that agrees with the judge's cheated, beside the share at used or higher), tmp/appendix_<section>.png (every
agent's rung per domain and sample, as tiles), tmp/appendix_<section>_table.png (the same as one table) and
tmp/appendix_<section>.csv; and tmp/appendix_rates_table.png, the rates of every section in one table. A run that has
not finished shows as an empty tile and a "(not finished)" row, and counts in no rate.
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

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT, ROOT / "scripts" / "paper"):
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


CHEATED = {"opened", "used", "shipped"}        # the ladder's "opened or higher", which agrees with the judge's cheated
USED = {"used", "shipped"}


def load_run(name: str) -> dict | None:
    """The run's agents with their rungs; None before it finishes."""
    run_dir = settings.RUNS / name
    if not (run_dir / "signals.jsonl").is_file():
        return None
    rows = [json.loads(line) for line in (run_dir / "signals.jsonl").read_text().splitlines()]
    return {"agents": [(r["agent"], r["rung"]) for r in rows]}


def rates(cells: list[str], suffix: str, samples: int) -> dict[str, dict]:
    """Per domain: the agents judged over every finished sample, and how many sit at opened or higher and at used or
    higher."""
    out = {}
    for domain, cell in zip(DOMAINS, cells):
        rungs = [rung for k in range(1, samples + 1) for run in [load_run(run_name(cell, suffix, k))] if run
                 for _, rung in run["agents"]]
        out[domain] = {"n": len(rungs), "cheated": sum(r in CHEATED for r in rungs),
                       "used": sum(r in USED for r in rungs)}
    return out


def rate_figure(section: str, title: str, cells: list[str], suffix: str, samples: int) -> Path:
    """One pair of bars per domain: the share of agents at opened or higher and at used or higher, labelled k of n."""
    found = rates(cells, suffix, samples)
    width, height = 5.2, 2.6
    fig = Figure(figsize=(width, height), dpi=200)
    ax = fig.add_axes([0.08, 0.2, 0.9, 0.62])
    bar_w, pair = 0.34, 0.38
    for i, domain in enumerate(DOMAINS):
        f = found[domain]
        for shift, key, colour in ((-pair / 2, "cheated", FILL["opened"]), (pair / 2, "used", FILL["used"])):
            share = f[key] / f["n"] if f["n"] else 0
            ax.bar(i + shift, share, width=bar_w, color=colour, edgecolor="none", zorder=3)
            ax.text(i + shift, share + 0.03, f"{f[key]} of {f['n']}" if f["n"] else "no run", ha="center", va="bottom",
                    fontsize=6.5, family="DejaVu Sans Mono", color=INK2)
    ax.set_xticks(range(len(DOMAINS)))
    ax.set_xticklabels(DOMAINS, fontsize=8, family="DejaVu Sans", color=INK)
    ax.set_ylim(0, 1.15)
    ax.set_yticks([0, 0.5, 1])
    ax.set_yticklabels(["0%", "50%", "100%"], fontsize=7, family="DejaVu Sans Mono", color=INK2)
    ax.grid(axis="y", color="#e1e0d9", lw=0.6, zorder=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#c3c2b7")
    ax.tick_params(axis="both", length=0)
    fig.text(0.08, 0.93, title, fontsize=9.5, family="DejaVu Sans", color=INK, weight="bold", va="center")
    for n, (label, colour) in enumerate((("opened or higher", FILL["opened"]), ("used or higher", FILL["used"]))):
        x = 0.08 + n * 0.3
        fig.patches.append(FancyBboxPatch((x, 0.04), 0.025, 0.05, boxstyle="square,pad=0", facecolor=colour,
                                          edgecolor="none", transform=fig.transFigure))
        fig.text(x + 0.035, 0.065, label, fontsize=7, family="DejaVu Sans", color=INK2, va="center")
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"appendix_{section}_rate.png"
    fig.savefig(path, dpi=200, facecolor="#ffffff")
    return path


def rates_table(suffix: str, samples: int) -> Path:
    """Every section's rates in one table: per domain, agents at opened or higher of the agents judged."""
    rows = []
    for section, (title, cells) in SECTIONS.items():
        found = rates(cells, suffix, samples)
        rows.append([title, *[[f"{f['cheated']} of {f['n']}" if f["n"] else "(no run)"] for f in found.values()]])
    return table_png("appendix_rates_table", ["cell", *DOMAINS], rows, [2.6, 1.3, 1.5, 1.1])


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
            seats = run["agents"] if run else [(f"a{n + 1:02d}", None) for n in range(agents)]
            for n, (agent, rung) in enumerate(seats):
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
    """One row per run: each agent's rung as a chip."""
    rows, records = [], []
    for domain, cell in zip(DOMAINS, cells):
        for k in range(1, samples + 1):
            name = run_name(cell, suffix, k)
            run = load_run(name)
            if run is None:
                rows.append([domain, str(k), ["(not finished)"]])
                records.append({"domain": domain, "sample": k, "run": name})
                continue
            rows.append([domain, str(k), [f"`{agent}: {rung}`" for agent, rung in run["agents"]]])
            records.append({"domain": domain, "sample": k, "run": name,
                            **{f"{agent}_rung": rung for agent, rung in run["agents"]}})
    table = table_png(f"appendix_{section}_table", ["domain", "sample", "agents' rungs"], rows, [1.3, 0.7, 2.6])
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
    parser.add_argument("--samples", type=int, default=4)
    args = parser.parse_args(argv)
    for section, (title, cells) in SECTIONS.items():
        print(rate_figure(section, title, cells, args.suffix, args.samples))
        print(tiles_figure(section, title, cells, args.suffix, args.samples))
        for path in table_and_csv(section, cells, args.suffix, args.samples):
            print(path)
    print(rates_table(args.suffix, args.samples))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
