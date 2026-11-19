"""The Coordination ladder by task for study_exploration_1's valid team runs: how many workers, teams and hidden
agents (regulators and disregulators) reach each level, with src.measures.coordination on each run's board. Valid runs
are the run index's rows marked valid and not mock. Writes tmp/tables/results/table_coordination_by_task.png.

    uv run python -m scripts.paper.coordination_table    # make results-tables runs every one
"""
from __future__ import annotations

import json
import sys
from collections import Counter

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle

from src.configuration.settings import ROOT
from src.measures.coordination import LEVELS, WORKER_ROLES, run_coordination
from src.utils.figure_style import AXIS, BAND, GREY_RAMP, INK, INK_SECONDARY, MUTED, PRINT_DPI, WHITE

RUNS = ROOT / "runs" / "study_exploration_1"
INDEX = RUNS / "INDEX.jsonl"
OUT = ROOT / "tmp" / "tables" / "results"
# The tasks of the study, in the order of src/measures/ladder_by_domain.py DOMAINS: SWE first.
TASKS = [("swe", "SWE"), ("protein_design", "Protein design"), ("creative_writing", "Creative writing")]
BLOCKS = [("workers", "Workers", "agents"), ("team", "Teams", "runs"),
          ("hidden", "Regulators and disregulators", "agents")]


def valid_runs() -> list[dict]:
    rows = [json.loads(line) for line in INDEX.read_text().splitlines() if line.strip()]
    return [r for r in rows if r["valid"] and not r["mock"]]


def counts() -> dict[tuple[str, str], Counter]:
    """(block, domain) -> Counter of levels, over the valid runs with at least two agents."""
    out: dict[tuple[str, str], Counter] = {(b, d): Counter() for b, _, _ in BLOCKS for d, _ in TASKS}
    domains = {d for d, _ in TASKS}
    for row in valid_runs():
        if row["domain"] not in domains:
            raise SystemExit(f"{row['path']}: domain {row['domain']!r} has no place in TASKS")
        run = run_coordination(RUNS / row["path"])
        if len(run["agents"]) < 2:
            continue
        d = row["domain"]
        for agent, measure in run["agents"].items():
            block = "workers" if run["roles"].get(agent) in WORKER_ROLES else "hidden"
            out[(block, d)][measure["level"]] += 1
        out[("team", d)][run["team"]] += 1
    return out


def shade(share: float) -> str | None:
    """A cell's fill by its share of the row: none below a tenth, then the grey ramp."""
    if share < 0.1:
        return None
    return GREY_RAMP[min(int(share * 5), len(GREY_RAMP) - 1)]


def table(data: dict[tuple[str, str], Counter]) -> None:
    left, total, col = 1.75, 1.0, 0.82
    width = left + total + col * len(LEVELS) + 0.15
    head_h, block_h, row_h = 0.5, 0.36, 0.3
    height = head_h + len(BLOCKS) * (block_h + row_h * len(TASKS)) + 0.12
    fig = Figure(figsize=(width, height), dpi=PRINT_DPI)
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.axis("off")
    pad = 0.15
    x_levels = left + total
    ax.text(pad, head_h / 2, "Task", fontsize=9.5, weight="bold", color=INK, va="center")
    ax.text(left + total - pad, head_h / 2, "Count", fontsize=9.5, weight="bold", color=INK, va="center",
            ha="right")
    for k, level in enumerate(LEVELS):
        ax.text(x_levels + (k + 0.5) * col, head_h / 2, level, fontsize=9.5, weight="bold", color=INK, va="center",
                ha="center")
    ax.plot([0, width], [head_h, head_h], color=INK, lw=1.0)
    y = head_h
    for block, title, unit in BLOCKS:
        ax.text(pad, y + block_h / 2 + 0.03, title, fontsize=8.6, style="italic", color=INK_SECONDARY, va="center")
        y += block_h
        for i, (domain, label) in enumerate(TASKS):
            c = data[(block, domain)]
            n = sum(c.values())
            if i % 2:
                ax.add_patch(Rectangle((0, y), width, row_h, fc=BAND, ec="none", zorder=0))
            ax.text(pad + 0.15, y + row_h / 2, label, fontsize=9, color=INK, va="center")
            ax.text(left + total - pad, y + row_h / 2, f"{n} {unit}", fontsize=8.2, color=INK_SECONDARY,
                    va="center", ha="right")
            for k, level in enumerate(LEVELS):
                value = c.get(level, 0)
                cx = x_levels + k * col
                fill = shade(value / n) if value else None
                if fill:
                    ax.add_patch(Rectangle((cx + 0.04, y + 0.03), col - 0.08, row_h - 0.06, fc=fill, ec="none",
                                           zorder=1))
                ax.text(cx + col / 2, y + row_h / 2, str(value) if value else "·", fontsize=9,
                        color=INK if value else MUTED, va="center", ha="center", zorder=2)
            y += row_h
        ax.plot([0, width], [y, y], color=AXIS, lw=0.6)
    path = OUT / "table_coordination_by_task.png"
    fig.savefig(path, dpi=PRINT_DPI, facecolor=WHITE)
    print(path)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    data = counts()
    for (block, domain), c in data.items():
        print(f"{block:8s} {domain:17s} n={sum(c.values()):3d}", {level: c.get(level, 0) for level in LEVELS},
              f"none-applicable={c.get(None, 0)}")
    table(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
