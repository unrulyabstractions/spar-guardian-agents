"""The appendix figures of a screen: for each cell of the first experiment, the cheating rate per domain and each
sample's team record on the code ladder per domain.

    uv run python -m scripts.paper.appendix [--model deepseek-flash-openrouter] [--suffixes flashpin flashbox]

The domains are the registry's (src/domains/__init__.py DOMAINS) with an example folder, examples/<domain>/, whose
<stem>_minimal.py names the domain's cells: <stem>_solo, <stem>_solo_cheat, <stem>_minimal, <stem>_minimal_cheat and
<stem>_minimal_regulated.
Sample k of a cell under a suffix is the run runs/<cell>_<suffix><k>/. A run counts when the code ladder wrote its
team records (signals.jsonl's team rows, src/measures/team_ladder.py) and it ran on --model (run.json model). The
suffixes are every one such runs carry, or those --suffixes names. A domain without a counted run is left out.

For each cell the script writes tmp/appendix/appendix_<cell>_rate.png (the share of samples whose team rung is opened
or higher, the rung that agrees with the judge's cheated, beside the share at used or higher) and
tmp/appendix/appendix_<cell>.png (one tile per domain and sample coloured by the team rung, with the collaboration
level under it).
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch

from src.configuration import settings
from src.domains import DOMAINS
from src.measures.run_directory import RunDir
from src.measures.rungs import COLLABORATION, RUNGS, at_least
from src.models import model_registry as models
from src.utils.figure_style import (
    AXIS,
    COLLABORATION_FILL,
    COLLABORATION_TEXT,
    INK,
    INK_SECONDARY,
    PRINT_DPI,
    RUNG_FILL,
    RUNG_TEXT,
    SURFACE,
    WHITE,
    style_axes,
)

OUT = settings.ROOT / "tmp" / "appendix"
EXAMPLES = settings.ROOT / "examples"
CELLS = {"solo": "Solo agent", "solo_cheat": "Solo agent instructed to cheat", "minimal": "Minimal swarm",
         "minimal_cheat": "Minimal swarm instructed to cheat", "minimal_regulated": "Minimal swarm with a regulator"}
LABELS = {"swe": "SWE"}                        # a domain key that abbreviates its name
SAMPLE = re.compile(r"^([a-z]+)(\d+)$")        # <suffix><k>
# The rate figure's two floors: opened agrees with the judge's cheated; used means the content entered the team's work.
FLOORS = (("opened", "opened or higher"), ("used", "used or higher"))
TILE_W, TILE_H, CHIP_H, GAP = 0.9, 0.46, 0.2, 0.05


def domain_stems() -> dict[str, str]:
    """{domain label: example stem} for every registry domain with an examples/<domain>/<stem>_minimal.py."""
    stems = {}
    for key in DOMAINS:
        minimal = sorted((EXAMPLES / key).glob("*_minimal.py"))
        if minimal:
            stems[LABELS.get(key, key.replace("_", " "))] = minimal[0].name.removesuffix("_minimal.py")
    return stems


def samples(stem: str, model: str, suffixes: list[str] | None) -> dict[str, list[dict]]:
    """{cell: [sample]} of one domain: the team record of every sample of each counted run, in suffix order, then k,
    then sample, each as {name, suffix, rung, collaboration}. A run belongs to the longest cell its name starts
    with."""
    cells = sorted((f"{stem}_{cell}" for cell in CELLS), key=len, reverse=True)
    found: dict[str, list[tuple]] = {cell: [] for cell in CELLS}
    for run_dir in settings.RUNS.glob(f"{stem}_*"):
        name = run_dir.name
        cell = next((c for c in cells if name.startswith(f"{c}_")), None)
        match = SAMPLE.match(name[len(cell) + 1:]) if cell else None
        if not match or (suffixes is not None and match[1] not in suffixes):
            continue
        run = RunDir(run_dir)
        teams = run.team_signals()
        if not teams or run.run_json.get("model") != model:
            continue
        order = suffixes.index(match[1]) if suffixes is not None else match[1]
        for team in teams:
            sample = {"name": name, "suffix": match[1], "rung": team["rung"], "collaboration": team["collaboration"]}
            found[cell.removeprefix(f"{stem}_")].append((order, int(match[2]), team["sample"], sample))
    return {cell: [sample for *_, sample in sorted(rows, key=lambda r: r[:3])] for cell, rows in found.items()}


def rate_figure(cell: str, runs: dict[str, list[dict]]) -> Path:
    """One pair of bars per domain: the share of samples whose team rung is opened or higher and used or higher,
    labelled k of n."""
    domains = list(runs)
    fig = Figure(figsize=(max(5.2, 0.4 + 1.6 * len(domains)), 2.6), dpi=PRINT_DPI)
    ax = fig.add_axes([0.08, 0.2, 0.9, 0.62])
    style_axes(ax, grid="y")
    bar_w, pair = 0.34, 0.38
    for i, domain in enumerate(domains):
        rungs = [sample["rung"] for sample in runs[domain]]
        for shift, (floor, _) in zip((-pair / 2, pair / 2), FLOORS):
            k = sum(at_least(rung, floor) for rung in rungs)
            share = k / len(rungs) if rungs else 0
            ax.bar(i + shift, share, width=bar_w, color=RUNG_FILL[floor], edgecolor="none", zorder=3)
            ax.text(i + shift, share + 0.03, f"{k} of {len(rungs)}" if rungs else "no run", ha="center", va="bottom",
                    fontsize=6.5, family="DejaVu Sans Mono", color=INK_SECONDARY)
    ax.set_xticks(range(len(domains)))
    ax.set_xticklabels(domains, fontsize=8, family="DejaVu Sans", color=INK)
    ax.set_ylim(0, 1.15)
    ax.set_yticks([0, 0.5, 1])
    ax.set_yticklabels(["0%", "50%", "100%"], fontsize=7, family="DejaVu Sans Mono", color=INK_SECONDARY)
    fig.text(0.08, 0.93, CELLS[cell], fontsize=9.5, family="DejaVu Sans", color=INK, weight="bold", va="center")
    for n, (floor, label) in enumerate(FLOORS):
        x = 0.08 + n * 0.3
        fig.patches.append(FancyBboxPatch((x, 0.04), 0.025, 0.05, boxstyle="square,pad=0", facecolor=RUNG_FILL[floor],
                                          edgecolor="none", transform=fig.transFigure))
        fig.text(x + 0.035, 0.065, label, fontsize=7, family="DejaVu Sans", color=INK_SECONDARY, va="center")
    return save(fig, f"appendix_{cell}_rate")


def chip(ax, x: float, y: float, w: float, h: float, text: str, fill: str, ink: str, size: float) -> None:
    """A rounded box with its text centred: a tile, a collaboration chip or a legend key."""
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.04", facecolor=fill,
                                edgecolor=AXIS, linewidth=0.6))
    ax.text(x + w / 2, y + h / 2, text, fontsize=size, family="DejaVu Sans", color=ink, ha="center", va="center")


def tiles_figure(cell: str, runs: dict[str, list[dict]]) -> Path:
    """Rows are the domains, columns the samples: one tile per sample coloured by its team rung, with the team's
    collaboration level under it (none for a lone agent). A domain with fewer samples than another leaves the later
    columns blank. The legend gives the rung ramp, and the collaboration ramp when a team is shown."""
    domains = list(runs)
    n_samples = max([1, *(len(v) for v in runs.values())])
    teams = any(sample["collaboration"] for v in runs.values() for sample in v)
    row_h = TILE_H + (GAP + CHIP_H if teams else 0) + 0.32
    left, top, key_w = 1.25, 0.75, 0.72
    legend_h = 0.42 + (0.32 if teams else 0)
    width = max(left + n_samples * (TILE_W + 0.2) + 0.2, 0.95 + len(RUNGS) * key_w + 0.2)   # room for the legend
    height = top + len(domains) * row_h + legend_h
    fig = Figure(figsize=(width, height), dpi=PRINT_DPI)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.axis("off")
    ax.text(0.15, 0.3, CELLS[cell], fontsize=9.5, family="DejaVu Sans", color=INK, va="center", weight="bold")
    for j in range(n_samples):
        x = left + j * (TILE_W + 0.2)
        ax.text(x + TILE_W / 2, top - 0.08, f"sample {j + 1}", fontsize=7, family="DejaVu Sans Mono",
                color=INK_SECONDARY, ha="center", va="bottom")
    for i, domain in enumerate(domains):
        y = top + i * row_h + 0.14
        ax.text(0.15, y + TILE_H / 2, domain, fontsize=8, family="DejaVu Sans", color=INK, va="center")
        for j, sample in enumerate(runs[domain]):
            x, rung, level = left + j * (TILE_W + 0.2), sample["rung"], sample["collaboration"]
            chip(ax, x, y, TILE_W, TILE_H, rung, RUNG_FILL.get(rung, WHITE), RUNG_TEXT.get(rung, INK), 7)
            if level:
                chip(ax, x, y + TILE_H + GAP, TILE_W, CHIP_H, level, COLLABORATION_FILL[level],
                     COLLABORATION_TEXT.get(level, INK), 6)
    y = height - legend_h + 0.1                 # the legend: each ramp in its order
    for name, levels, fills, inks in (("rung", RUNGS, RUNG_FILL, RUNG_TEXT),
                                      ("collaboration", COLLABORATION if teams else (), COLLABORATION_FILL,
                                       COLLABORATION_TEXT)):
        if not levels:
            continue
        ax.text(0.15, y + 0.11, name, fontsize=6.5, family="DejaVu Sans Mono", color=INK_SECONDARY, va="center")
        for n, level in enumerate(levels):
            chip(ax, 0.95 + n * key_w, y, key_w - 0.08, 0.22, level, fills[level], inks.get(level, INK), 6)
        y += 0.32
    return save(fig, f"appendix_{cell}")


def save(fig: Figure, name: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    fig.savefig(path, dpi=PRINT_DPI, facecolor=SURFACE)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=settings.DEFAULT_MODEL,
                        help="the screen's model: a registry entry or an Inspect model string (default %(default)s)")
    parser.add_argument("--suffixes", nargs="+", default=None,
                        help="run-name suffixes, each followed by the sample number (default: every one on disk)")
    args = parser.parse_args(argv)
    model = models.resolve(args.model)
    by_domain = {domain: samples(stem, model, args.suffixes) for domain, stem in domain_stems().items()}
    by_domain = {domain: cells for domain, cells in by_domain.items() if any(cells.values())}
    if not by_domain:
        print(f"no run of an example cell on {model} with a team record")
        return 1
    for domain, cells in by_domain.items():
        found = [sample for cell_samples in cells.values() for sample in cell_samples]
        print(f"== {domain}: {len(found)} sample(s) in {len({s['name'] for s in found})} run(s), suffixes "
              f"{' '.join(sorted({s['suffix'] for s in found}))}")
    for cell in CELLS:
        runs = {domain: cells[cell] for domain, cells in by_domain.items()}
        print(rate_figure(cell, runs))
        print(tiles_figure(cell, runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
