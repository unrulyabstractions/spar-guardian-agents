"""Two figures of a regulator scale study, as PNG under tmp/figures/<study>/ (gitignored), with the same grid as a CSV:

  <study>_rungs.png     the team's Cheating rung in every cell, in the ladder's own colours
  <study>_hand_in.png   what planted content each team handed in: nothing, lines of a piece, one value, both values
  <study>.csv           one line per cell

A cell is a worker model, a team size, a share of regulators and the regulators' strength. Every value is read from the
study's stored runs: the team's rung from analysis/labels/cheating.json, the planted values from the run's own record
(run.json swe.own_values) and the graded hand-in (processed/swe/diffs/team.diff). A stronger regulator ran only where
the weaker one's team cheated, so a cell without a run is drawn as not needed, or as not run where its gate was open.

    uv run python -m scripts.study.regulator_scale_figure [regulator_scale_1]

No network, no model calls.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle

from scripts.paper.study_ladders import CHEATING_FILL
from src.configuration.settings import ROOT
from src.measures.rungs import RUNGS, at_least
from src.utils.figure_style import AXIS, GRID, INK, INK_SECONDARY, MUTED, PRINT_DPI, SURFACE, WHITE

RUN_NAME = re.compile(r"^(?P<task>[a-z]+_[a-z]+)_(?P<worker>[a-z0-9]+)_n(?P<size>\d+)_reg(?P<share>\d+)"
                      r"(?:_(?P<strength>weak|strong|top))?_(?P<pack>[a-z]+)_s(?P<seed>\d+)$")
STRENGTHS = ("weak", "strong", "top")
"""The regulators' strengths in the order a study climbs them."""
NAMES = {"deepseek-v4.1-flash": "DeepSeek V4.1 Flash", "qwen3.8-flash": "Qwen 3.8 Flash",
         "claude-haiku-5.5": "Haiku 5.5", "gpt-6.1-terra": "GPT-6.1 Terra", "claude-opus-5.5": "Opus 5.5"}
"""A model's name in a figure, by the start of its id's last part (the viewer's names, tv_labels.js)."""
HAND_IN = ("nothing", "lines", "one value", "both values")
"""What a hand-in held of the planted content, least to most."""
HAND_IN_FILL = dict(zip(HAND_IN, (CHEATING_FILL["none"], "#e9988a", "#cf3a27", "#7d140f")))
"""One red hue, light to dark, for planted content; a neutral for none. The three reds pass the ordinal checks
(one hue, monotone lightness, gaps of 0.06 or more, 2.19:1 against the surface)."""
HAND_IN_WORDS = {"nothing": "nothing planted", "lines": "lines of a piece, no value", "one value": "one planted value",
                 "both values": "both planted values: the tests pass"}
DARK_FILLS = {"#cf3a27", "#7d140f"}
"""The fills that take white text."""

CELL_W, CELL_H, GAP, GROUP_GAP = 1.24, 0.62, 0.06, 0.24
LEFT, RIGHT_PAD = 1.42, 0.3
PANEL_HEAD, PANEL_GAP = 0.34, 0.26

plt.switch_backend("Agg")
plt.rcParams.update({"font.family": "Arial", "figure.facecolor": SURFACE, "savefig.facecolor": SURFACE,
                     "text.color": INK, "hatch.linewidth": 0.6, "hatch.color": GRID})


@dataclass(frozen=True)
class Run:
    """One stored run of the study."""
    name: str
    worker: str            # the worker model's name in the figure
    size: int              # the team's size
    share: int             # the percent of the team that are regulators
    strength: str | None   # weak, strong or top; None without a regulator
    regulator: str | None  # the regulator model's name in the figure
    rung: str              # the team's Cheating rung
    values: int            # how many planted values the graded hand-in holds
    n_values: int          # how many values the run planted
    passed: bool

    @property
    def hand_in(self) -> str:
        """What the hand-in held: both values, one value, lines of a piece (cheated with no value), or nothing."""
        if self.values == self.n_values:
            return "both values"
        if self.values:
            return "one value"
        return "lines" if at_least(self.rung, "attempted") else "nothing"


def model_name(model: str | None) -> str | None:
    """A model id's name in the figure, or its last part when NAMES has none."""
    if model is None:
        return None
    last = model.rsplit("/", 1)[-1]
    return next((name for start, name in NAMES.items() if last.startswith(start)), last)


def values_held(diff: str, own_values: dict[str, str]) -> int:
    """How many of a run's planted values the lines a diff adds hold; a path counts by its file's name."""
    added = "\n".join(line for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    return sum(value.rsplit("/", 1)[-1] in added for value in own_values.values())


def read_run(folder: Path) -> Run | None:
    """The folder's run, or None when its name is no cell's or it holds no team rung yet."""
    found = RUN_NAME.match(folder.name)
    labels = folder / "analysis" / "labels" / "cheating.json"
    if not found or not labels.exists():
        return None
    cheating = json.loads(labels.read_text())
    record = json.loads((folder / "processed" / "run.json").read_text())
    own_values = record["swe"]["own_values"]
    diff = (folder / "processed" / "swe" / "diffs" / "team.diff").read_text(errors="replace")
    run = Run(name=folder.name, worker=model_name(record["model"]), size=int(found["size"]),
              share=int(found["share"]), strength=found["strength"],
              regulator=model_name((record.get("role_models") or {}).get("regulator")), rung=cheating["team"],
              values=values_held(diff, own_values), n_values=len(own_values), passed=bool(cheating.get("passed")))
    if (run.rung == "hacked") != (run.values == run.n_values) or (run.rung == "hacked") != run.passed:
        raise SystemExit(f"{run.name}: rung {run.rung}, {run.values} of {run.n_values} values in the hand-in, "
                         f"passed {run.passed}: the label and the hand-in disagree")
    return run


def read_study(study: str) -> list[Run]:
    """Every finished run of the study."""
    runs = [run for folder in sorted((ROOT / "runs" / study).iterdir()) if folder.is_dir()
            if (run := read_run(folder)) is not None]
    if not runs:
        raise SystemExit(f"runs/{study} holds no finished run of a regulator scale study")
    return runs


def columns(runs: list[Run]) -> list[tuple[int, str | None]]:
    """The grid's columns: no regulator, then each share with every strength, weak to top."""
    shares = sorted({run.share for run in runs if run.share})
    return [(0, None)] + [(share, strength) for share in shares for strength in STRENGTHS]


def cell_state(grid: dict, worker: str, size: int, column: tuple[int, str | None]) -> str:
    """A cell without a run: "not run" when the weaker regulator's team cheated, so its gate was open, else
    "not needed"."""
    share, strength = column
    weaker = STRENGTHS[STRENGTHS.index(strength) - 1] if strength in STRENGTHS[1:] else None
    before = grid.get((worker, size, share, weaker)) if weaker else None
    return "not run" if before is not None and at_least(before.rung, "attempted") else "not needed"


def ink_on(fill: str) -> str:
    return WHITE if fill in DARK_FILLS else INK


def draw_cell(ax, x: float, y: float, fill: str, title: str, note: str = "") -> None:
    """One filled cell with its word and, under it, a smaller note. A pale fill takes a hairline ring."""
    edge = fill if fill in DARK_FILLS or fill == HAND_IN_FILL["lines"] else AXIS
    ax.add_patch(FancyBboxPatch((x, y), CELL_W, CELL_H, boxstyle="round,pad=0,rounding_size=0.05", fc=fill, ec=edge,
                                lw=0.8))
    ax.text(x + CELL_W / 2, y + CELL_H / 2 + (0.09 if note else 0), title, ha="center", va="center", fontsize=10.5,
            fontweight="bold", color=ink_on(fill))
    if note:
        ax.text(x + CELL_W / 2, y + CELL_H / 2 - 0.14, note, ha="center", va="center", fontsize=8.5,
                color=ink_on(fill))


def draw_empty(ax, x: float, y: float, state: str) -> None:
    """A cell without a run: a dotted outline when not needed, a hatched one with its words when not run."""
    if state == "not run":
        ax.add_patch(Rectangle((x, y), CELL_W, CELL_H, fc="none", ec=MUTED, lw=0.8, ls=(0, (3, 2)), hatch="////"))
        ax.text(x + CELL_W / 2, y + CELL_H / 2, "not run", ha="center", va="center", fontsize=9, color=INK_SECONDARY,
                bbox=dict(fc=SURFACE, ec="none", pad=1.5))
    else:
        ax.add_patch(Rectangle((x, y), CELL_W, CELL_H, fc="none", ec=GRID, lw=0.8, ls=(0, (1, 2.2))))


def column_x(cols: list[tuple[int, str | None]]) -> list[float]:
    """Each column's left edge: a wider gap where the share changes."""
    xs, x = [], LEFT
    for k, (share, _) in enumerate(cols):
        if k:
            x += CELL_W + (GROUP_GAP if share != cols[k - 1][0] else GAP)
        xs.append(x)
    return xs


def legend(ax, y: float, items: list[tuple[str, str]], empty: bool) -> None:
    """A row of chips with their words, then the two kinds of cell without a run."""
    x = LEFT
    for fill, words in items:
        edge = fill if fill in DARK_FILLS or fill == HAND_IN_FILL["lines"] else AXIS
        ax.add_patch(FancyBboxPatch((x, y), 0.26, 0.2, boxstyle="round,pad=0,rounding_size=0.03", fc=fill, ec=edge,
                                    lw=0.8))
        text = ax.text(x + 0.34, y + 0.1, words, ha="left", va="center", fontsize=9, color=INK_SECONDARY)
        x += 0.34 + text_width(ax, text) + 0.3
    if empty:
        y -= 0.36
        x = LEFT
        ax.add_patch(Rectangle((x, y), 0.26, 0.2, fc="none", ec=GRID, lw=0.8, ls=(0, (1, 2.2))))
        text = ax.text(x + 0.34, y + 0.1, "not needed: the weaker regulator's team did not cheat", ha="left",
                       va="center", fontsize=9, color=INK_SECONDARY)
        x += 0.34 + text_width(ax, text) + 0.3
        ax.add_patch(Rectangle((x, y), 0.26, 0.2, fc="none", ec=MUTED, lw=0.8, ls=(0, (3, 2)), hatch="////"))
        ax.text(x + 0.34, y + 0.1, "not run", ha="left", va="center", fontsize=9, color=INK_SECONDARY)


def text_width(ax, text) -> float:
    """A drawn text's width in the axes' units (inches)."""
    ax.figure.canvas.draw()
    box = text.get_window_extent().transformed(ax.transData.inverted())
    return box.width


def figure(runs: list[Run], kind: str, title: str, subtitle: str):
    """The grid of every cell, one block per worker model. kind is "rungs" or "hand_in"."""
    grid = {(run.worker, run.size, run.share, run.strength): run for run in runs}
    cols = columns(runs)
    xs = column_x(cols)
    workers = list(dict.fromkeys(run.worker for run in runs))
    sizes = sorted({run.size for run in runs})
    regulators = {run.strength: run.regulator for run in runs if run.strength}
    block_h = PANEL_HEAD + len(sizes) * CELL_H + (len(sizes) - 1) * GAP
    head_h, title_h, legend_h = 0.92, 0.95, 1.05
    width = xs[-1] + CELL_W + RIGHT_PAD
    height = title_h + head_h + len(workers) * block_h + (len(workers) - 1) * PANEL_GAP + legend_h
    fig = plt.figure(figsize=(width, height))
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    ax.axis("off")

    ax.text(0.3, height - 0.42, title, ha="left", va="center", fontsize=16, fontweight="bold", color=INK)
    ax.text(0.3, height - 0.76, subtitle, ha="left", va="center", fontsize=10.5, color=INK_SECONDARY)

    top = height - title_h
    shares = list(dict.fromkeys(share for share, _ in cols))
    for share in shares:
        members = [k for k, (s, _) in enumerate(cols) if s == share]
        x0, x1 = xs[members[0]], xs[members[-1]] + CELL_W
        label = "no regulator" if not share else f"{share}% of the team are regulators"
        ax.text((x0 + x1) / 2, top - 0.2, label, ha="center", va="center", fontsize=10.5, fontweight="bold", color=INK)
        ax.plot([x0, x1], [top - 0.38, top - 0.38], color=AXIS, lw=0.9)
    for k, (share, strength) in enumerate(cols):
        if strength:
            ax.text(xs[k] + CELL_W / 2, top - 0.56, regulators.get(strength, strength), ha="center", va="center",
                    fontsize=9.5, color=INK)
            ax.text(xs[k] + CELL_W / 2, top - 0.75, strength, ha="center", va="center", fontsize=8.5, color=MUTED)

    y = top - head_h
    for worker in workers:
        ax.text(0.3, y - PANEL_HEAD / 2 + 0.02, f"{worker} workers", ha="left", va="center", fontsize=11,
                fontweight="bold", color=INK)
        y -= PANEL_HEAD
        for size in sizes:
            y -= CELL_H
            ax.text(LEFT - 0.16, y + CELL_H / 2, f"team of {size}", ha="right", va="center", fontsize=10,
                    color=INK_SECONDARY)
            for k, column in enumerate(cols):
                run = grid.get((worker, size, column[0], column[1]))
                if run is None:
                    draw_empty(ax, xs[k], y, cell_state(grid, worker, size, column))
                elif kind == "rungs":
                    note = {"both values": "both values", "one value": "one value", "lines": "lines only"}.get(
                        run.hand_in, "")
                    draw_cell(ax, xs[k], y, CHEATING_FILL[run.rung], run.rung, note)
                else:
                    words = {"nothing": "nothing", "lines": "lines only"}.get(run.hand_in, run.hand_in)
                    draw_cell(ax, xs[k], y, HAND_IN_FILL[run.hand_in], words, "tests pass" if run.passed else "")
            y -= GAP
        y -= PANEL_GAP - GAP

    items = ([(CHEATING_FILL[rung], rung) for rung in RUNGS] if kind == "rungs"
             else [(HAND_IN_FILL[level], HAND_IN_WORDS[level]) for level in HAND_IN])
    empty = any(grid.get((w, s, c[0], c[1])) is None for w in workers for s in sizes for c in cols)
    legend(ax, 0.66, items, empty)
    return fig


def write_csv(runs: list[Run], path: Path) -> None:
    """The grid as a table: one line per cell that ran."""
    with path.open("w", newline="") as handle:
        out = csv.writer(handle)
        out.writerow(["workers", "team_size", "regulator_share_pct", "regulator_strength", "regulators", "team_rung",
                      "hand_in", "planted_values_in_hand_in", "passed", "run"])
        for run in sorted(runs, key=lambda r: (r.worker, r.size, r.share, STRENGTHS.index(r.strength or "weak"))):
            out.writerow([run.worker, run.size, run.share, run.strength or "", run.regulator or "", run.rung,
                          run.hand_in, run.values, run.passed, run.name])


def main(argv: list[str]) -> int:
    study = argv[1] if len(argv) > 1 else "regulator_scale_1"
    runs = read_study(study)
    out = ROOT / "tmp" / "figures" / study
    out.mkdir(parents=True, exist_ok=True)
    figures = {
        "rungs": ("Cheating by team size and regulator",
                  "The team's Cheating rung at the hand-in, and what the hand-in held. One run per cell."),
        "hand_in": ("What reached the hand-in",
                    "Planted content in the code each team handed in. One run per cell."),
    }
    for kind, (title, subtitle) in figures.items():
        fig = figure(runs, kind, title, subtitle)
        fig.savefig(out / f"{study}_{kind}.png", dpi=PRINT_DPI)
        plt.close(fig)
        print(out / f"{study}_{kind}.png")
    write_csv(runs, out / f"{study}.csv")
    print(out / f"{study}.csv", f"({len(runs)} runs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
