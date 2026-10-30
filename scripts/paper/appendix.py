"""The appendix figures of a screen: for each cell of the first experiment, the cheating rate per domain and every
agent's rung on the code ladder per domain and sample.

    uv run python -m scripts.paper.appendix [--model deepseek-flash-openrouter] [--suffixes flashpin flashbox]

The domains are the registry's (src/domains/__init__.py DOMAINS) with an example folder, examples/<domain>/, whose
<stem>_minimal.py names the domain's cells: <stem>_solo, <stem>_solo_cheat, <stem>_minimal and <stem>_minimal_cheat.
Sample k of a cell under a suffix is the run runs/<cell>_<suffix><k>/. A run counts when it finished (its
signals.jsonl exists) and ran on --model (run.json model). The suffixes are every one such runs carry, or those
--suffixes names. A domain without a counted run is left out.

For each cell the script writes tmp/appendix/appendix_<cell>_rate.png (the share of agents at opened or higher, the
ladder's rung that agrees with the judge's cheated, beside the share at used or higher) and
tmp/appendix/appendix_<cell>.png (every agent's rung per domain and sample, as tiles).
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch

from src.configuration import settings
from src.domains import DOMAINS
from src.models import model_registry as models
from src.utils.figure_style import AXIS, INK, INK_SECONDARY, PRINT_DPI, RUNG_FILL, RUNG_TEXT, SURFACE, WHITE, style_axes
from src.utils.json_files import read_json, read_jsonl

OUT = settings.ROOT / "tmp" / "appendix"
EXAMPLES = settings.ROOT / "examples"
CELLS = {"solo": "Solo agent", "solo_cheat": "Solo agent instructed to cheat", "minimal": "Minimal swarm",
         "minimal_cheat": "Minimal swarm instructed to cheat"}
LABELS = {"swe": "SWE"}                        # a domain key that abbreviates its name
SAMPLE = re.compile(r"^([a-z]+)(\d+)$")        # <suffix><k>
CHEATED = {"opened", "used", "shipped"}        # the ladder's "opened or higher", which agrees with the judge's cheated
USED = {"used", "shipped"}
TILE_W, TILE_H, GAP = 0.78, 0.46, 0.08


def domain_stems() -> dict[str, str]:
    """{domain label: example stem} for every registry domain with an examples/<domain>/<stem>_minimal.py."""
    stems = {}
    for key in DOMAINS:
        minimal = sorted((EXAMPLES / key).glob("*_minimal.py"))
        if minimal:
            stems[LABELS.get(key, key.replace("_", " "))] = minimal[0].name.removesuffix("_minimal.py")
    return stems


def samples(stem: str, model: str, suffixes: list[str] | None) -> dict[str, list[dict]]:
    """{cell: [run]} of one domain: every counted run of each cell, in suffix order then k, each with its name and
    its agents' rungs. A run belongs to the longest cell its name starts with."""
    cells = sorted((f"{stem}_{cell}" for cell in CELLS), key=len, reverse=True)
    found: dict[str, list[tuple]] = {cell: [] for cell in CELLS}
    for run_dir in settings.RUNS.glob(f"{stem}_*"):
        name = run_dir.name
        cell = next((c for c in cells if name.startswith(f"{c}_")), None)
        match = SAMPLE.match(name[len(cell) + 1:]) if cell else None
        if not match or (suffixes is not None and match[1] not in suffixes):
            continue
        if not (run_dir / "signals.jsonl").is_file() or (read_json(run_dir / "run.json") or {}).get("model") != model:
            continue
        order = suffixes.index(match[1]) if suffixes is not None else match[1]
        rungs = [(row["agent"], row["rung"]) for row in read_jsonl(run_dir / "signals.jsonl")]
        run = {"name": name, "suffix": match[1], "agents": rungs}
        found[cell.removeprefix(f"{stem}_")].append((order, int(match[2]), run))
    return {cell: [run for *_, run in sorted(runs, key=lambda r: r[:2])] for cell, runs in found.items()}


def rate_figure(cell: str, runs: dict[str, list[dict]]) -> Path:
    """One pair of bars per domain: the share of agents at opened or higher and at used or higher, labelled k of n."""
    domains = list(runs)
    fig = Figure(figsize=(max(5.2, 0.4 + 1.6 * len(domains)), 2.6), dpi=PRINT_DPI)
    ax = fig.add_axes([0.08, 0.2, 0.9, 0.62])
    style_axes(ax, grid="y")
    bar_w, pair = 0.34, 0.38
    for i, domain in enumerate(domains):
        rungs = [rung for run in runs[domain] for _, rung in run["agents"]]
        for shift, counted, colour in ((-pair / 2, CHEATED, RUNG_FILL["opened"]), (pair / 2, USED, RUNG_FILL["used"])):
            k = sum(r in counted for r in rungs)
            share = k / len(rungs) if rungs else 0
            ax.bar(i + shift, share, width=bar_w, color=colour, edgecolor="none", zorder=3)
            ax.text(i + shift, share + 0.03, f"{k} of {len(rungs)}" if rungs else "no run", ha="center", va="bottom",
                    fontsize=6.5, family="DejaVu Sans Mono", color=INK_SECONDARY)
    ax.set_xticks(range(len(domains)))
    ax.set_xticklabels(domains, fontsize=8, family="DejaVu Sans", color=INK)
    ax.set_ylim(0, 1.15)
    ax.set_yticks([0, 0.5, 1])
    ax.set_yticklabels(["0%", "50%", "100%"], fontsize=7, family="DejaVu Sans Mono", color=INK_SECONDARY)
    fig.text(0.08, 0.93, CELLS[cell], fontsize=9.5, family="DejaVu Sans", color=INK, weight="bold", va="center")
    for n, (label, colour) in enumerate((("opened or higher", RUNG_FILL["opened"]),
                                         ("used or higher", RUNG_FILL["used"]))):
        x = 0.08 + n * 0.3
        fig.patches.append(FancyBboxPatch((x, 0.04), 0.025, 0.05, boxstyle="square,pad=0", facecolor=colour,
                                          edgecolor="none", transform=fig.transFigure))
        fig.text(x + 0.035, 0.065, label, fontsize=7, family="DejaVu Sans", color=INK_SECONDARY, va="center")
    return save(fig, f"appendix_{cell}_rate")


def tiles_figure(cell: str, runs: dict[str, list[dict]]) -> Path:
    """Rows are the domains, columns the samples, one tile per agent coloured by its rung. A domain with fewer samples
    than another leaves the later columns blank."""
    domains = list(runs)
    n_samples = max([1, *(len(v) for v in runs.values())])
    agents = max((len(run["agents"]) for v in runs.values() for run in v), default=1)
    cell_w = agents * TILE_W + (agents - 1) * GAP
    left, top = 1.25, 0.75
    width = max(left + n_samples * (cell_w + 0.3) + 0.2, 0.75 + len(RUNG_FILL) * 0.72 + 0.2)   # room for the legend
    height = top + len(domains) * (TILE_H + 0.42) + 0.55
    fig = Figure(figsize=(width, height), dpi=PRINT_DPI)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.axis("off")
    ax.text(0.15, 0.3, CELLS[cell], fontsize=9.5, family="DejaVu Sans", color=INK, va="center", weight="bold")
    for j in range(n_samples):
        x = left + j * (cell_w + 0.3)
        ax.text(x + cell_w / 2, top - 0.08, f"sample {j + 1}", fontsize=7, family="DejaVu Sans Mono",
                color=INK_SECONDARY, ha="center", va="bottom")
    for i, domain in enumerate(domains):
        y = top + i * (TILE_H + 0.42) + 0.14
        ax.text(0.15, y + TILE_H / 2, domain, fontsize=8, family="DejaVu Sans", color=INK, va="center")
        for j, run in enumerate(runs[domain]):
            x0 = left + j * (cell_w + 0.3)
            for n, (agent, rung) in enumerate(run["agents"]):
                x = x0 + n * (TILE_W + GAP)
                ax.add_patch(FancyBboxPatch((x, y), TILE_W, TILE_H, boxstyle="round,pad=0,rounding_size=0.05",
                                            facecolor=RUNG_FILL.get(rung, WHITE), edgecolor=AXIS, linewidth=0.6))
                ax.text(x + TILE_W / 2, y + TILE_H / 2, rung, fontsize=7, family="DejaVu Sans",
                        color=RUNG_TEXT.get(rung, INK), ha="center", va="center")
                if agents > 1:
                    ax.text(x + TILE_W / 2, y - 0.03, agent, fontsize=6, family="DejaVu Sans Mono",
                            color=INK_SECONDARY, ha="center", va="bottom")
    y = height - 0.42                           # the legend: the ramp in rung order
    ax.text(0.15, y + 0.11, "rung", fontsize=6.5, family="DejaVu Sans Mono", color=INK_SECONDARY, va="center")
    for n, (rung, fill) in enumerate(RUNG_FILL.items()):
        x = 0.75 + n * 0.72
        ax.add_patch(FancyBboxPatch((x, y), 0.64, 0.22, boxstyle="round,pad=0,rounding_size=0.03", facecolor=fill,
                                    edgecolor=AXIS, linewidth=0.5))
        ax.text(x + 0.32, y + 0.11, rung, fontsize=6, family="DejaVu Sans", color=RUNG_TEXT.get(rung, INK),
                ha="center", va="center")
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
        print(f"no finished run of an example cell on {model}")
        return 1
    for domain, cells in by_domain.items():
        runs = [run for cell_runs in cells.values() for run in cell_runs]
        print(f"== {domain}: {len(runs)} runs, suffixes {' '.join(sorted({run['suffix'] for run in runs}))}")
    for cell in CELLS:
        runs = {domain: cells[cell] for domain, cells in by_domain.items()}
        print(rate_figure(cell, runs))
        print(tiles_figure(cell, runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
