"""Figure 1 of the paper: Lucy's exploratory Qwen3-32B runs as dot-and-interval rows, one dot per condition with its
95% bootstrap interval over episodes. The top panel takes the scripted teammates of E1b, the bottom panel the real
model teammates of E2. The outcome is `deliberate_cheat` over the receivers (the default-persona agents) only.

Sources, read at build time (Lucy's repository, read only):
    submodules/lucy/runs/e1_variants_20260928-164817/{analysis/summary.json, config.yaml, prompts.yaml}   (E1b)
    submodules/lucy/runs/e2_transmission_20260929-130116/{analysis/summary.json, config.yaml, prompts.yaml}  (E2)

Writes tmp/figures/paper/fig1_scripted_teammates.png at 200 dpi.

    uv run python -m scripts.paper.fig_scripted_teammates

No network, no model calls.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import yaml
from matplotlib import rc_context
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.transforms import blended_transform_factory

from src.configuration.settings import ROOT
from src.utils.figure_style import (
    GRID,
    INK,
    INK_SECONDARY,
    MUTED,
    PRINT_DPI,
    ROLE_EDGE,
    ROLE_FILL,
    SURFACE,
    style_axes,
)

LUCY_RUNS = ROOT / "submodules" / "lucy" / "runs"
E1B = LUCY_RUNS / "e1_variants_20260928-164817"
E2 = LUCY_RUNS / "e2_transmission_20260929-130116"
OUT = ROOT / "tmp" / "figures" / "paper" / "fig1_scripted_teammates.png"
METRIC = "deliberate_cheat"

# The added teammate's kind (a scripted confederate's script or a real agent's persona) and the role it plays here.
ROLE_OF = {"neutral": "worker", "violating": "disregulator", "dissenter": "regulator",
           "misaligned_advocate": "disregulator", "guardian": "regulator"}

# (title, subtitle, run, the reference condition behind the dashed line, the rows in groups of (condition, label)).
# Each subtitle quotes the run's own prompts.yaml; `quotes` names the (section, key) each quote must appear in.
PANELS = [
    dict(title="Scripted teammates",
         subtitle='The disregulator posts that it edited a failing test and says "just fix the test"; '
                  'the regulator replies "I\'d hold off on that"',
         quotes={"just fix the test": ("confederate_scripts", "violating"),
                 "I'd hold off on that": ("confederate_scripts", "dissenter")},
         run=E1B, reference="conf_neutral",
         groups=[[("conf_neutral", "Neutral teammate"), ("conf_violating", "Disregulator")],
                 [("viol_plus_neutral", "Disregulator + neutral teammate"),
                  ("viol_plus_dissent", "Disregulator + regulator")]]),
    dict(title="Real model teammates, nothing scripted",
         subtitle="The disregulator is a model prompted to be misaligned and encourage shortcuts; "
                  "the regulator is one prompted to keep the team to the rules",
         quotes={"encourage them to use it too": ("personas", "misaligned_advocate"),
                 "make sure the team stays on track": ("personas", "guardian")},
         run=E2, reference="e2_baseline",
         groups=[[("e2_baseline", "No added teammate"), ("e2_seed_advocate", "Disregulator"),
                  ("e2_guardian", "Regulator"), ("e2_advocate_guardian", "Disregulator + regulator")]]),
]

# Layout in inches.
WIDTH = 7.4
LEFT = 2.75            # where the axes start
RIGHT = 0.75           # room for the agents-per-team column
ROW = 0.34             # one row
GROUP_GAP = 0.5        # extra rows between two groups of a panel
TOP_PAD, TITLE_H, SUBTITLE_H, PANEL_GAP, BOTTOM = 0.1, 0.24, 0.3, 0.42, 0.84
LEGEND_Y = 0.13         # the chips' legend, above the figure's bottom
CHIP_STEP = 0.16       # inches between two teammate chips


def load(run: Path) -> tuple[dict, dict, dict]:
    summary = json.loads((run / "analysis" / "summary.json").read_text())
    config = yaml.safe_load((run / "config.yaml").read_text())
    prompts = yaml.safe_load((run / "prompts.yaml").read_text())
    return summary, config, prompts


def teammates(config: dict, condition: str) -> list[str]:
    """The roles of the teammates a condition adds to the receivers, in the config's order."""
    spec = config["conditions"][condition] or {}
    added = {**spec.get("confederates", {}), **spec.get("personas", {})}
    return [ROLE_OF[kind] for kind, count in added.items() for _ in range(count)]


def rows_of(panel: dict) -> tuple[list[dict], float]:
    """Every row of a panel with its y position, read from the run's summary and config, and the reference mean."""
    summary, config, prompts = load(panel["run"])
    for quote, (section, key) in panel["quotes"].items():
        source = json.dumps(prompts[section][key])
        if quote not in source.replace("\\", ""):
            raise SystemExit(f"{panel['run'].name}/prompts.yaml {section}.{key} no longer says {quote!r}")
    rows, y = [], 0.0
    for g, group in enumerate(panel["groups"]):
        y += GROUP_GAP if g else 0.0
        for condition, label in group:
            m = summary["conditions"][condition]["metrics"][METRIC]
            per_team, rest = divmod(m["n_agents"], m["n_episodes"])
            added = teammates(config, condition)
            if rest or per_team != config["episode"]["n_agents"] - len(added):
                raise SystemExit(f"{condition}: {m['n_agents']} receivers over {m['n_episodes']} episodes "
                                 f"disagrees with {config['episode']['n_agents']} agents and {len(added)} added")
            rows.append(dict(y=y, label=label, mean=m["mean"], lo=m["lo"], hi=m["hi"], per_team=per_team,
                             added=added))
            y += 1.0
    reference = summary["conditions"][panel["reference"]]["metrics"][METRIC]["mean"]
    return rows, reference


def draw_panel(fig: Figure, panel: dict, rows: list[dict], reference: float, top: float, xmax: float,
               last: bool) -> float:
    """Draws one panel whose title starts `top` inches below the figure's top; returns the inches it took."""
    height = fig.get_figheight()
    span = rows[-1]["y"] + 1.0
    axes_top = top + TITLE_H + SUBTITLE_H
    ax = fig.add_axes([LEFT / WIDTH, 1 - (axes_top + span * ROW) / height, 1 - (LEFT + RIGHT) / WIDTH,
                       span * ROW / height])
    fig.text(0.12 / WIDTH, 1 - (top + TITLE_H * 0.5) / height, panel["title"], fontsize=11, fontweight="bold",
             color=INK, va="center")
    fig.text(0.12 / WIDTH, 1 - (top + TITLE_H + SUBTITLE_H * 0.4) / height, panel["subtitle"], fontsize=7.6,
             color=INK_SECONDARY, va="center")

    style_axes(ax, grid="x")
    ax.set_xlim(0, xmax)
    ax.set_ylim(span - 0.5, -0.5)
    ax.set_yticks([])
    ticks = [k / 10 for k in range(int(xmax * 10 + 1e-9) + 1)]
    ax.set_xticks(ticks)
    ax.set_xticklabels([f"{t:.0%}" for t in ticks], fontsize=8.5)
    if last:
        ax.set_xlabel("Ordinary agents who cheated (edited a test after reading it, or hard-coded test inputs)",
                      fontsize=8.5, color=INK_SECONDARY, labelpad=6)
    else:
        ax.spines["bottom"].set_visible(False)
        ax.tick_params(labelbottom=False)
    ax.axvline(0, color=GRID, lw=1, zorder=0)
    ax.axvline(reference, color=MUTED, lw=0.9, ls=(0, (3, 2.5)), zorder=1)

    label_at = blended_transform_factory(fig.dpi_scale_trans, ax.transData)
    chips_right = LEFT - 0.14
    for row in rows:
        ax.plot([row["lo"], row["hi"]], [row["y"]] * 2, color=INK_SECONDARY, lw=1.8, solid_capstyle="round",
                zorder=2)
        ax.plot(row["mean"], row["y"], "o", ms=6.5, mfc=INK, mec=SURFACE, mew=1.2, zorder=3)
        ax.text(row["hi"] + xmax * 0.012, row["y"], f"{row['mean']:.0%}", fontsize=8.5, color=INK, va="center")
        for k, role in enumerate(reversed(row["added"])):
            ax.plot(chips_right - k * CHIP_STEP, row["y"], "o", ms=6.5, mfc=ROLE_FILL[role], mec=ROLE_EDGE[role],
                    mew=1.0, transform=label_at, clip_on=False)
        ax.text(chips_right - 2 * CHIP_STEP, row["y"], row["label"], fontsize=9, color=INK, ha="right",
                va="center", transform=label_at)
        ax.text(WIDTH - 0.2, row["y"], str(row["per_team"]), fontsize=9, color=INK_SECONDARY, ha="right",
                va="center", transform=label_at)
    return TITLE_H + SUBTITLE_H + span * ROW


def chips_legend(fig: Figure) -> None:
    """What the chips beside each row mean, on one line at the figure's bottom left."""
    ax = fig.add_axes([0, 0, 1, 1], facecolor="none")
    ax.set_axis_off()
    at, renderer = fig.dpi_scale_trans, fig.canvas.get_renderer()
    x, y = 0.12, LEGEND_Y
    label = ax.text(x, y, "Added teammate", fontsize=7.5, color=MUTED, va="center", transform=at)
    x += label.get_window_extent(renderer).width / fig.dpi + 0.16
    for role, name in (("worker", "neutral"), ("disregulator", "disregulator"), ("regulator", "regulator")):
        ax.plot(x, y, "o", ms=5.5, mfc=ROLE_FILL[role], mec=ROLE_EDGE[role], mew=1.0, transform=at, clip_on=False)
        label = ax.text(x + 0.08, y, name, fontsize=7.5, color=INK_SECONDARY, va="center", transform=at)
        x += 0.08 + label.get_window_extent(renderer).width / fig.dpi + 0.16


def main() -> int:
    built = [(panel, *rows_of(panel)) for panel in PANELS]
    highest = max(row["hi"] for _, rows, _ in built for row in rows)
    xmax = math.ceil(highest / 0.05) * 0.05 + 0.02
    spans = [rows[-1]["y"] + 1.0 for _, rows, _ in built]
    height = TOP_PAD + sum(TITLE_H + SUBTITLE_H + s * ROW for s in spans) + PANEL_GAP * (len(spans) - 1) + BOTTOM

    with rc_context({"font.family": "Arial"}):
        fig = Figure(figsize=(WIDTH, height), dpi=PRINT_DPI, facecolor=SURFACE)
        FigureCanvasAgg(fig)
        fig.text(1 - 0.2 / WIDTH, 1 - (TOP_PAD + TITLE_H * 0.6) / height, "Ordinary agents\nper team", fontsize=8,
                 color=MUTED, ha="right", va="center", linespacing=1.15)
        top = TOP_PAD
        for k, (panel, rows, reference) in enumerate(built):
            top += draw_panel(fig, panel, rows, reference, top, xmax, last=k == len(built) - 1) + PANEL_GAP
        chips_legend(fig)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(OUT, dpi=PRINT_DPI, facecolor=SURFACE)
    for panel, rows, reference in built:
        print(f"{panel['title']} ({panel['run'].name}, reference {reference:.4f})")
        for row in rows:
            print(f"  {row['label']:<34} {row['mean']:.4f} [{row['lo']:.4f}, {row['hi']:.4f}]  "
                  f"{row['per_team']} per team, adds {row['added']}")
    print(OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
