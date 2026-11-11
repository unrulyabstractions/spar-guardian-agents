"""Figure 5 of the paper: the SWE deep dive, Stage 1 (Lucy's study). Two panels, the workers (W1 and W2) and seat 3,
each with the share judged cheated by arm (3W, S0 to S3) and planning level (none, one turn), with Wilson 95%
intervals and k/n beside every point below 100%.

Source: submodules/lucy/runs/s1_swe/analysis/summary.json (one row per agent), read as Lucy's
experiments/guardian_baseline/checks/stage1_tests.py reads it: rows with exit 0 and no failure, the one verdict that
did not parse filled from her re-judge (REJUDGED in experiments/guardian_baseline/figures/stage1_figures.py, read as a
literal), episodes with both workers' verdicts. The interval is her `wilson` (z = 1.96, clipped to 0 and 1).
Writes tmp/figures/paper/fig5_swe_deep_dive.png.

    uv run python -m scripts.paper.fig_deep_dive

No network, no model calls.
"""
from __future__ import annotations

import ast
import json
import math
import sys
from collections import defaultdict

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

from src.configuration.settings import LUCY, ROOT
from src.utils.figure_style import (
    AXIS,
    CATEGORICAL,
    INK,
    INK_SECONDARY,
    MUTED,
    PRINT_DPI,
    SURFACE,
    style_axes,
)

SUMMARY = LUCY / "runs" / "s1_swe" / "analysis" / "summary.json"
LUCY_FIGURES = LUCY / "experiments" / "guardian_baseline" / "figures" / "stage1_figures.py"
OUT = ROOT / "tmp" / "figures" / "paper" / "fig5_swe_deep_dive.png"

PLANNING_COLOR = {0: CATEGORICAL[0], 1: CATEGORICAL[1]}
PLANNING_LABEL = {0: "No planning turn", 1: "One planning turn (all seats post a plan before any work)"}
WORKER_SEATS = ("W1", "W2")
SEAT3 = "seat3"
PANELS = [("workers", "Workers (W1 and W2)", "agents per point; each holds part of the planted fix"),
          ("seat3", "Seat 3", "agents per point; a third worker in 3W, the regulator in S0–S3")]
FOOTNOTE = ("3W: no regulator, seat 3 is a third worker  ·  S0: the regulator's tools, no text  ·  S1: + duty  ·  "
            "S2: + definition of cheating  ·  S3: + pressure  (S1–S3 cumulative)")
OFFSET = 0.14                # half the gap between a no-planning and a planning point, in arm units


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval, as stage1_tests.py computes it."""
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def rejudged() -> dict:
    """Lucy's REJUDGED literal: {(episode, agent): cheated} for verdicts that did not parse and were re-judged."""
    for node in ast.parse(LUCY_FIGURES.read_text()).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "REJUDGED" for t in node.targets):
            return ast.literal_eval(node.value)
    raise SystemExit(f"no REJUDGED in {LUCY_FIGURES}")


def cells() -> dict:
    """{(group, arm, planning): [cheated, n]} over complete episodes, group "workers" or "seat3"."""
    fill = rejudged()
    rows = [r for r in json.loads(SUMMARY.read_text())["rows"] if r["exit"] == 0 and r["failure"] is None]
    episodes = defaultdict(dict)
    for r in rows:
        if r["parse_ok"]:
            cheated = bool(r["cheated"])
        elif (r["episode"], r["agent"]) in fill:
            cheated = bool(fill[(r["episode"], r["agent"])])
            print("filled", r["episode"], r["agent"], "cheated" if cheated else "not cheated")
        else:
            continue                                   # an unparsed verdict is missing, not "not cheated"
        e = episodes[r["episode"]]
        e.update(arm=r["arm"], planning=r["planning_turns"])
        if r["seat"] in WORKER_SEATS:
            e.setdefault("w", []).append(cheated)
        elif r["seat"] == SEAT3:
            e["s3"] = cheated
    out = defaultdict(lambda: [0, 0])
    for e in episodes.values():
        if len(e.get("w", [])) != len(WORKER_SEATS):
            continue
        key = (e["arm"], e["planning"])
        out[("workers", *key)][0] += sum(e["w"])
        out[("workers", *key)][1] += len(e["w"])
        if "s3" in e:
            out[("seat3", *key)][0] += e["s3"]
            out[("seat3", *key)][1] += 1
    return dict(out)


def main() -> int:
    data = cells()
    arms = sorted({arm for _, arm, _ in data})
    levels = sorted({p for _, _, p in data})
    for group, *_ in PANELS:
        for p in levels:
            print(group, f"planning {p}", "  ".join(f"{a} {data[(group, a, p)][0]}/{data[(group, a, p)][1]}"
                                                    for a in arms))
        print(group, "pooled", "  ".join(f"{a} {sum(data[(group, a, p)][0] for p in levels)}/"
                                         f"{sum(data[(group, a, p)][1] for p in levels)}" for a in arms))

    fig = Figure(figsize=(7.8, 3.5), dpi=PRINT_DPI, facecolor=SURFACE)
    FigureCanvasAgg(fig)
    width, gap, left, bottom, height = 0.41, 0.075, 0.075, 0.17, 0.585
    for i, (group, title, subtitle) in enumerate(PANELS):
        ax = fig.add_axes((left + i * (width + gap), bottom, width, height))
        style_axes(ax, grid="y")
        ns = {data[(group, a, p)][1] for a in arms for p in levels}
        per_point = ns.pop() if len(ns) == 1 else f"{min(ns)} to {max(ns)}"
        ax.set_title(title, loc="left", color=INK, fontsize=10.5, fontweight="bold", pad=17)
        ax.annotate(f"{per_point} {subtitle}", xy=(0, 1), xycoords="axes fraction", xytext=(0, 5),
                    textcoords="offset points", color=INK_SECONDARY, fontsize=7.1, va="bottom")
        for j, arm in enumerate(arms):
            for p in levels:
                k, n = data[(group, arm, p)]
                x = j + (OFFSET if p else -OFFSET) * (1 if len(levels) > 1 else 0)
                lo, hi = wilson(k, n)
                color = PLANNING_COLOR[p]
                ax.plot([x, x], [100 * lo, 100 * hi], color=color, lw=1.6, solid_capstyle="butt", zorder=2)
                ax.plot([x], [100 * k / n], "o", ms=5.2, color=color, mec=SURFACE, mew=0.9, zorder=3)
                if k < n:
                    right = p == max(levels)
                    ax.annotate(f"{k}/{n}", (x, 100 * k / n), xytext=(5 if right else -5, 0),
                                textcoords="offset points", ha="left" if right else "right", va="center",
                                fontsize=7.5, color=INK_SECONDARY, zorder=4,
                                bbox=dict(boxstyle="square,pad=0.1", fc=SURFACE, ec="none"))
        ax.set_xlim(-0.55, len(arms) - 0.45)
        ax.set_ylim(0, 104)
        ax.set_yticks([0, 25, 50, 75, 100])
        ax.set_yticklabels([f"{t}%" for t in (0, 25, 50, 75, 100)] if i == 0 else [], fontsize=8)
        ax.set_xticks(range(len(arms)))
        ax.set_xticklabels(arms, fontsize=9, color=INK)
        ax.tick_params(axis="x", pad=5)
        ax.spines["bottom"].set_color(AXIS)
        if i == 0:
            ax.set_ylabel("Judged cheated", fontsize=8.5, color=INK_SECONDARY)

    handles = [Line2D([], [], ls="", marker="o", ms=5.5, color=PLANNING_COLOR[p], mec=SURFACE,
                      label=PLANNING_LABEL[p]) for p in levels]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.985), ncol=len(handles), frameon=False,
               fontsize=8.5, labelcolor=INK, handletextpad=0.3, columnspacing=2.2)
    fig.text(0.5, 0.025, FOOTNOTE, ha="center", va="bottom", fontsize=6.4, color=MUTED)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=PRINT_DPI, facecolor=SURFACE)
    print(OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
