"""The appendix figures of a screen: for each cell of the first experiment, the cheating rate per domain and each
sample's team record on the code ladder per domain, on each model, and the whole screen on one page.

    uv run python -m scripts.paper.appendix [--models claude-haiku-5-5 gpt-6-luna] [--suffixes flashpin flashbox]

The domains are the registry's (src/domains/__init__.py DOMAINS) with an example folder, examples/<domain>/, whose
<stem>_minimal.py names the domain's cells: <stem>_solo, <stem>_solo_cheat, <stem>_minimal, <stem>_minimal_cheat and
<stem>_minimal_regulated.
Sample k of a cell under a suffix is the run runs/<cell>_<suffix><k>/. A run counts when the code ladder wrote its
team records (signals.jsonl's team rows, src/measures/team_ladder.py) and it ran on that model (run.json model). The
suffixes are every one such runs carry, or those --suffixes names. A domain without a counted run is left out. The
models default to the fleet's (fleet_layout.MODELS).

A rate means cheated: the share of samples whose team rung is the task's own floor or higher (Domain.cheated_from, tried
on SWE, chess and creative writing, opened elsewhere), the rung the judge's cheated agrees with. Each tick or panel
names the floor it counts.

For each model and cell the script writes tmp/appendix/<model suffix>/appendix_<cell>_rate.png (the rate beside the
share at used or higher) and appendix_<cell>.png (one tile per domain and sample coloured by the team rung, with the
collaboration level under it). Across the models it writes, under tmp/appendix/overview/, rung.png and
collaboration.png (every sample as one tile, by domain, model and cell) and rate.png (the rate, one panel per domain).
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch

from scripts.box.fleet_layout import MODELS
from src.configuration import settings
from src.domains import DOMAINS
from src.measures.run_directory import RunDir
from src.measures.rungs import COLLABORATION, RUNGS, at_least
from src.models import model_registry as models
from src.utils.figure_style import (
    AXIS,
    CATEGORICAL,
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
CELL_HEADS = {"solo": "Solo", "solo_cheat": "Solo, told\nto cheat", "minimal": "Swarm",
              "minimal_cheat": "Swarm, told\nto cheat", "minimal_regulated": "Swarm and\nregulator"}
TEAM_CELLS = ("minimal", "minimal_cheat", "minimal_regulated")
MODEL_LABELS = {"deepseek-flash-openrouter": "DeepSeek", "gpt-6-luna": "Luna", "claude-haiku-5-5": "Haiku"}
LABELS = {"swe": "SWE"}                        # a domain key that abbreviates its name
SAMPLE = re.compile(r"^([a-z]+)(\d+)$")        # <suffix><k>
USED = "used"                                  # the rate figure's second bar: that rung or a higher one
TILE_W, TILE_H, CHIP_H, GAP = 0.9, 0.46, 0.2, 0.05
CELL_W, CELL_H, CELL_GAP = 0.3, 0.26, 0.04    # a sample's tile on an overview, and the gap around it


def label_of(key: str) -> str:
    """A domain key's label on the figures."""
    return LABELS.get(key, key.replace("_", " "))


def floor_of(domain: str) -> str:
    """The rung a labelled domain's cheating starts at, from its ladder (Domain.cheated_from)."""
    return next(cls.ladder.cheated_from for key, cls in DOMAINS.items() if label_of(key) == domain)


def or_higher(rung: str) -> str:
    return f"{rung} or higher"


def rate_bars(domain: str, rungs: list[str]) -> list[tuple[str, int]]:
    """A domain's two bars as (floor, samples at it or higher): its own floor, then used."""
    return [(floor, sum(at_least(rung, floor) for rung in rungs)) for floor in (floor_of(domain), USED)]


def domain_stems() -> dict[str, str]:
    """{domain label: example stem} for every registry domain with an examples/<domain>/<stem>_minimal.py."""
    stems = {}
    for key in DOMAINS:
        minimal = sorted((EXAMPLES / key).glob("*_minimal.py"))
        if minimal:
            stems[label_of(key)] = minimal[0].name.removesuffix("_minimal.py")
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


def rate_figure(cell: str, runs: dict[str, list[dict]], folder: Path) -> Path:
    """One pair of bars per domain: the share of samples whose team rung is the task's own floor or higher and used
    or higher, labelled k of n. The tick names the floor, and the legend holds each floor the figure shows."""
    domains = list(runs)
    fig = Figure(figsize=(max(5.2, 0.4 + 1.6 * len(domains)), 2.6), dpi=PRINT_DPI)
    ax = fig.add_axes([0.08, 0.27, 0.9, 0.55])
    style_axes(ax, grid="y")
    bar_w, pair = 0.34, 0.38
    for i, domain in enumerate(domains):
        rungs = [sample["rung"] for sample in runs[domain]]
        for shift, (floor, k) in zip((-pair / 2, pair / 2), rate_bars(domain, rungs)):
            share = k / len(rungs) if rungs else 0
            ax.bar(i + shift, share, width=bar_w, color=RUNG_FILL[floor], edgecolor="none", zorder=3)
            ax.text(i + shift, share + 0.03, f"{k} of {len(rungs)}" if rungs else "no run", ha="center", va="bottom",
                    fontsize=6.5, family="DejaVu Sans Mono", color=INK_SECONDARY)
    ax.set_xticks(range(len(domains)))
    ax.set_xticklabels([f"{domain}\n({or_higher(floor_of(domain))})" for domain in domains], fontsize=8,
                       family="DejaVu Sans", color=INK, linespacing=1.1)
    ax.set_ylim(0, 1.15)
    ax.set_yticks([0, 0.5, 1])
    ax.set_yticklabels(["0%", "50%", "100%"], fontsize=7, family="DejaVu Sans Mono", color=INK_SECONDARY)
    fig.text(0.08, 0.93, CELLS[cell], fontsize=9.5, family="DejaVu Sans", color=INK, weight="bold", va="center")
    for n, floor in enumerate([*sorted({floor_of(domain) for domain in domains}, key=RUNGS.index), USED]):
        x = 0.08 + n * 0.3
        fig.patches.append(FancyBboxPatch((x, 0.04), 0.025, 0.05, boxstyle="square,pad=0", facecolor=RUNG_FILL[floor],
                                          edgecolor="none", transform=fig.transFigure))
        fig.text(x + 0.035, 0.065, or_higher(floor), fontsize=7, family="DejaVu Sans", color=INK_SECONDARY, va="center")
    return save(fig, folder / f"appendix_{cell}_rate.png")


def chip(ax, x: float, y: float, w: float, h: float, text: str, fill: str, ink: str, size: float) -> None:
    """A rounded box with its text centred: a tile, a collaboration chip or a legend key."""
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.04", facecolor=fill,
                                edgecolor=AXIS, linewidth=0.6))
    ax.text(x + w / 2, y + h / 2, text, fontsize=size, family="DejaVu Sans", color=ink, ha="center", va="center")


def tiles_figure(cell: str, runs: dict[str, list[dict]], folder: Path) -> Path:
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
    return save(fig, folder / f"appendix_{cell}.png")


def initial(level: str, levels: tuple[str, ...]) -> str:
    """A tile's mark, so that no level rests on colour alone: a dot for the lowest level, else its first letter, or
    its first two where a lower level holds that letter."""
    if level == levels[0]:
        return "\u00b7"
    lower = levels[1:levels.index(level)]
    return level[:2].title() if any(other[0] == level[0] for other in lower) else level[0].upper()


def overview_figure(screen: dict[str, dict], cells: tuple[str, ...], key: str, levels: tuple[str, ...], fills: dict,
                    inks: dict, title: str) -> Path:
    """Every sample as one tile: rows are the domains and within them the models, columns the cells and within them
    the samples, each tile coloured by the sample's `key`. A sample that is missing leaves a white tile."""
    domains = list({domain: None for by_domain in screen.values() for domain in by_domain})
    n_samples = max(len(samples) for by_domain in screen.values() for cells_of in by_domain.values()
                    for samples in cells_of.values())
    left, top, column_gap, row_gap = 1.75, 0.95, 0.22, 0.14
    cell_w = n_samples * (CELL_W + CELL_GAP)
    row_h = len(screen) * (CELL_H + CELL_GAP) + row_gap
    width = max(left + len(cells) * (cell_w + column_gap) + 0.1, 0.95 + len(levels) * 0.86 + 0.2)
    height = top + len(domains) * row_h + 0.62
    fig = Figure(figsize=(width, height), dpi=PRINT_DPI)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.axis("off")
    ax.text(0.15, 0.28, title, fontsize=9.5, family="DejaVu Sans", color=INK, va="center", weight="bold")
    for c, cell in enumerate(cells):
        ax.text(left + c * (cell_w + column_gap) + cell_w / 2, top - 0.08, CELL_HEADS[cell], fontsize=7, ha="center",
                va="bottom", family="DejaVu Sans", color=INK_SECONDARY, linespacing=1.15)
    for d, domain in enumerate(domains):
        y0 = top + d * row_h
        ax.text(0.15, y0 + (row_h - row_gap) / 2, domain, fontsize=8, family="DejaVu Sans", color=INK, va="center")
        for m, (entry, by_domain) in enumerate(screen.items()):
            y = y0 + m * (CELL_H + CELL_GAP)
            ax.text(left - 0.08, y + CELL_H / 2, MODEL_LABELS.get(entry, entry), fontsize=6.5, ha="right", va="center",
                    family="DejaVu Sans Mono", color=INK_SECONDARY)
            for c, cell in enumerate(cells):
                samples_here = by_domain.get(domain, {}).get(cell, [])
                for k in range(n_samples):
                    x = left + c * (cell_w + column_gap) + k * (CELL_W + CELL_GAP)
                    level = samples_here[k][key] if k < len(samples_here) else None
                    chip(ax, x, y, CELL_W, CELL_H, initial(level, levels) if level else "",
                         fills.get(level, WHITE), inks.get(level, INK), 6.5)
    y = height - 0.42
    for n, level in enumerate(levels):
        chip(ax, 0.15 + n * 0.86, y, 0.78, 0.24, f"{initial(level, levels)} {level}", fills[level],
             inks.get(level, INK), 6)
    return save(fig, OUT / "overview" / f"{key}.png")


def model_rate_figure(screen: dict[str, dict]) -> Path:
    """One panel per domain: the share of each model's samples at the task's own floor or higher in each cell."""
    domains = list({domain: None for by_domain in screen.values() for domain in by_domain})
    fig = Figure(figsize=(1.95 * len(domains) + 0.5, 2.7), dpi=PRINT_DPI)
    width = 0.8 / len(screen)
    for d, domain in enumerate(domains):
        ax = fig.add_axes([0.05 + d * 0.945 / len(domains), 0.3, 0.86 / len(domains), 0.52])
        style_axes(ax, grid="y")
        for m, (entry, by_domain) in enumerate(screen.items()):
            for c, cell in enumerate(CELLS):
                rungs = [sample["rung"] for sample in by_domain.get(domain, {}).get(cell, [])]
                share = rate_bars(domain, rungs)[0][1] / len(rungs) if rungs else 0
                ax.bar(c + (m - (len(screen) - 1) / 2) * width, share, width=width * 0.86, zorder=3,
                       color=CATEGORICAL[m], edgecolor="none", label=MODEL_LABELS.get(entry, entry) if not c else None)
        ax.set_xticks(range(len(CELLS)))
        ax.set_xticklabels([CELL_HEADS[cell] for cell in CELLS], fontsize=5.4, family="DejaVu Sans", color=INK,
                           rotation=90, linespacing=1.0)
        ax.set_ylim(0, 1.05)
        ax.set_yticks([0, 0.5, 1])
        ax.set_yticklabels(["0%", "50%", "100%"] if d == 0 else [], fontsize=7, family="DejaVu Sans Mono",
                           color=INK_SECONDARY)
        ax.set_title(f"{domain} ({or_higher(floor_of(domain))})", fontsize=6.8, family="DejaVu Sans", color=INK,
                     loc="left", pad=4)
        if d == 0:
            handles, labels = ax.get_legend_handles_labels()
    fig.text(0.05, 0.94, "Cheating rate by task", fontsize=9.5, family="DejaVu Sans", color=INK, weight="bold",
             va="center")
    fig.legend(handles, labels, loc="upper right", bbox_to_anchor=(0.99, 0.99), ncol=len(screen), frameon=False,
               fontsize=7, handlelength=1, handleheight=1)
    return save(fig, OUT / "overview" / "rate.png")


def save(fig: Figure, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=PRINT_DPI, facecolor=SURFACE)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", default=list(MODELS),
                        help="the screen's models: registry entries (default: the fleet's)")
    parser.add_argument("--suffixes", nargs="+", default=None,
                        help="run-name suffixes, each followed by the sample number (default: every one on disk)")
    args = parser.parse_args(argv)
    screen = {}
    for entry in args.models:
        model = models.resolve(entry)
        by_domain = {domain: samples(stem, model, args.suffixes) for domain, stem in domain_stems().items()}
        by_domain = {domain: cells for domain, cells in by_domain.items() if any(cells.values())}
        if not by_domain:
            print(f"no run of an example cell on {model} with a team record")
            continue
        screen[entry] = by_domain
        folder = OUT / MODELS.get(entry, entry)
        for domain, cells in by_domain.items():
            found = [sample for cell_samples in cells.values() for sample in cell_samples]
            print(f"== {entry} {domain}: {len(found)} sample(s) in {len({s['name'] for s in found})} run(s), suffixes "
                  f"{' '.join(sorted({s['suffix'] for s in found}))}")
        for cell in CELLS:
            runs = {domain: cells[cell] for domain, cells in by_domain.items()}
            print(rate_figure(cell, runs, folder))
            print(tiles_figure(cell, runs, folder))
    if not screen:
        return 1
    print(overview_figure(screen, tuple(CELLS), "rung", RUNGS, RUNG_FILL, RUNG_TEXT, "The screen by team rung"))
    print(overview_figure(screen, TEAM_CELLS, "collaboration", COLLABORATION, COLLABORATION_FILL, COLLABORATION_TEXT,
                          "The swarms by collaboration"))
    print(model_rate_figure(screen))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
