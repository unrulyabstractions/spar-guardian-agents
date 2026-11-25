"""Four figures of the two team studies of 2026-11-25, as PNG flat under tmp/ (gitignored):

  disregulator_existence_2_rungs.png           the team's Cheating rung by worker model and team size
  disregulator_existence_2_disregulators.png   the same by the disregulator's model, three workers and one disregulator
  regulator_scale_2_rungs.png                  the team's Cheating rung and its hand-in by regulator model and share
  regulator_scale_2_take_backs.png             how many hand-ins each regulated team took back

Every value is read from the studies' stored runs: the team's rung, the roles and whether the hidden tests passed from
analysis/labels/cheating.json, the models, the planted values and the take-backs from the run's own record (run.json)
and the graded hand-in (processed/swe/diffs/team.diff). One chip or one bar is one run.

    uv run python -m scripts.study.team_rung_figures

No network, no model calls.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle

from scripts.paper.study_ladders import CHEATING_FILL, CHEATING_TEXT
from scripts.study.regulator_scale_figure import text_width, values_held
from src.configuration.settings import ROOT
from src.measures.run_directory import RunDir
from src.measures.rungs import RUNGS
from src.utils.figure_style import AXIS, CATEGORICAL, INK, INK_SECONDARY, PRINT_DPI, SURFACE, style_axes

DISREGULATOR_STUDY, REGULATOR_STUDY = "disregulator_existence_2", "regulator_scale_2"
NAMES = {"deepseek-v4.1-flash": "DeepSeek V4.1 Flash", "qwen3.8-flash": "Qwen 3.8 Flash", "claude-haiku-5": "Haiku 5.5",
         "claude-sonnet-5": "Sonnet 5.5", "claude-opus-5": "Opus 5.5", "claude-fable-5": "Fable 5.2",
         "gpt-6-luna": "GPT-6 Luna", "grok-4.7": "Grok 4.7", "kimi-k3": "Kimi K3"}
"""A model's name in a figure, by a part of its id that both routes (OpenRouter, Anthropic) share."""
WORKERS = ("Haiku 5.5", "GPT-6 Luna", "Sonnet 5.5", "Opus 5.5", "Fable 5.2", "DeepSeek V4.1 Flash", "Qwen 3.8 Flash")
DISREGULATORS = ("DeepSeek V4.1 Flash", "Grok 4.7", "Kimi K3", "Fable 5.2")
REGULATORS = ("Haiku 5.5", "Sonnet 5.5", "Opus 5.5", "Fable 5.2")
"""The rows and columns in the order the figures draw them."""
SHAPES = ((3, 0), (3, 1), (4, 1), (8, 2))
"""The disregulator study's teams, as workers and disregulators."""
SEED = re.compile(r"_s(\d+)$")

CHIP_H, GAP, ROW_GAP, COL_GAP = 0.56, 0.06, 0.12, 0.34
LEFT, PAD, TITLE_H, HEAD_H, PLAIN_HEAD_H, LEGEND_H = 2.05, 0.3, 0.95, 0.86, 0.5, 0.72
"""HEAD_H holds a heading over groups of columns and the columns' labels; PLAIN_HEAD_H the labels alone."""

plt.switch_backend("Agg")
plt.rcParams.update({"font.family": "Arial", "figure.facecolor": SURFACE, "savefig.facecolor": SURFACE,
                     "text.color": INK})


@dataclass(frozen=True)
class Run:
    """One stored run of either study."""
    name: str
    seed: int
    worker: str             # the worker model's name in the figure
    influencer: str | None  # the regulators' or disregulators' model; None without either
    workers: int
    regulators: int
    disregulators: int
    rung: str               # the team's Cheating rung
    passed: bool            # the hand-in passed the hidden tests
    values: int             # how many planted values the graded hand-in holds
    n_values: int           # how many values the run planted
    take_backs: int         # hand-ins a member took back

    @property
    def hand_in(self) -> str:
        if self.values == self.n_values:
            return "both planted values"
        return "one planted value" if self.values else "nothing planted"


def model_name(model: str | None) -> str | None:
    """A model id's name in the figure."""
    if model is None:
        return None
    for part, name in NAMES.items():
        if part in model:
            return name
    raise SystemExit(f"no figure name for the model {model}")


def read_run(folder: Path) -> Run:
    stored = RunDir(folder)
    record = json.loads(stored.file("run.json").read_text())
    cheating = json.loads(stored.file("cheating.json").read_text())
    roles = Counter(agent["role"] or "worker" for agent in cheating["agents"].values())
    own_values = record["swe"]["own_values"]
    diff = (stored.file("swe") / "diffs" / "team.diff").read_text(errors="replace")
    run = Run(name=folder.name, seed=int(SEED.search(folder.name)[1]), worker=model_name(record["model"]),
              influencer=model_name(record["role_models"]["regulator"] or record["role_models"]["disregulator"]),
              workers=roles["worker"], regulators=roles["regulator"], disregulators=roles["disregulator"],
              rung=cheating["team"], passed=bool(cheating["passed"]), values=values_held(diff, own_values),
              n_values=len(own_values), take_backs=len(record["unsubmits"]["unsubmits"]))
    if run.passed != (run.values == run.n_values):
        raise SystemExit(f"{run.name}: passed {run.passed} with {run.values} of {run.n_values} planted values in the "
                         f"hand-in: the label and the hand-in disagree")
    return run


def read_study(study: str) -> list[Run]:
    """Every stored run of the study, by name."""
    runs = [read_run(folder) for folder in sorted((ROOT / "runs" / study).iterdir())
            if folder.is_dir() and RunDir(folder).file("cheating.json").exists()]
    if not runs:
        raise SystemExit(f"runs/{study} holds no stored run")
    return runs


@dataclass(frozen=True)
class Grid:
    """A figure of chips: rows by columns, each cell the runs of one setup, by seed."""
    title: str
    subtitle: str
    rows: tuple[str, ...]
    columns: tuple[tuple[str, int], ...]      # each column's label and how many chips it holds side by side
    groups: tuple[tuple[str, int, int], ...]  # a heading over the columns first to last
    chip_w: float
    cells: dict[tuple[int, int], list[Run]]
    note: Callable[[Run], str]                # the smaller line under a chip's rung
    spans: dict[tuple[int, int], int] = field(default_factory=dict)  # a cell drawn across to a later column


def draw_chip(ax, x: float, y: float, w: float, run: Run, note: str) -> None:
    """One run: its team's rung in the ladder's colour, with a smaller note under the word."""
    fill, ink = CHEATING_FILL[run.rung], CHEATING_TEXT.get(run.rung, INK)
    ax.add_patch(FancyBboxPatch((x, y), w, CHIP_H, boxstyle="round,pad=0,rounding_size=0.05", fc=fill,
                                ec=fill if run.rung in CHEATING_TEXT else AXIS, lw=0.8))
    ax.text(x + w / 2, y + CHIP_H / 2 + (0.09 if note else 0), run.rung, ha="center", va="center", fontsize=10.5,
            fontweight="bold", color=ink)
    if note:
        ax.text(x + w / 2, y + CHIP_H / 2 - 0.13, note, ha="center", va="center", fontsize=8.5, color=ink)


def draw_empty(ax, x: float, y: float, w: float, h: float = CHIP_H) -> None:
    """A place without a run: a dotted outline."""
    ax.add_patch(Rectangle((x, y), w, h, fc="none", ec=AXIS, lw=0.8, ls=(0, (1, 2.2))))


def draw_legend(ax, y: float, empty: bool) -> None:
    """The ladder's rungs as chips with their words, then the outline of a place without a run."""
    x = PAD
    for rung in RUNGS:
        ax.add_patch(FancyBboxPatch((x, y), 0.26, 0.2, boxstyle="round,pad=0,rounding_size=0.03",
                                    fc=CHEATING_FILL[rung], ec=CHEATING_FILL[rung] if rung in CHEATING_TEXT else AXIS,
                                    lw=0.8))
        text = ax.text(x + 0.34, y + 0.1, rung, ha="left", va="center", fontsize=9, color=INK_SECONDARY)
        x += 0.34 + text_width(ax, text) + 0.3
    if empty:
        draw_empty(ax, x, y, 0.26, 0.2)
        ax.text(x + 0.34, y + 0.1, "no run", ha="left", va="center", fontsize=9, color=INK_SECONDARY)


def draw_grid(grid: Grid):
    widths = [slots * grid.chip_w + (slots - 1) * GAP for _, slots in grid.columns]
    xs = [LEFT + sum(widths[:k]) + k * COL_GAP for k in range(len(widths))]
    width = xs[-1] + widths[-1] + PAD
    head_h = HEAD_H if grid.groups else PLAIN_HEAD_H
    height = TITLE_H + head_h + len(grid.rows) * CHIP_H + (len(grid.rows) - 1) * ROW_GAP + LEGEND_H
    fig = plt.figure(figsize=(width, height))
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    ax.axis("off")
    ax.text(PAD, height - 0.42, grid.title, ha="left", va="center", fontsize=16, fontweight="bold", color=INK)
    ax.text(PAD, height - 0.76, grid.subtitle, ha="left", va="center", fontsize=10.5, color=INK_SECONDARY)

    top = height - TITLE_H
    for label, first, last in grid.groups:
        x0, x1 = xs[first], xs[last] + widths[last]
        ax.text((x0 + x1) / 2, top - 0.2, label, ha="center", va="center", fontsize=10.5, fontweight="bold", color=INK)
        ax.plot([x0, x1], [top - 0.38, top - 0.38], color=AXIS, lw=0.9)
    for k, (label, _) in enumerate(grid.columns):
        ax.text(xs[k] + widths[k] / 2, top - head_h + 0.26, label, ha="center", va="center", fontsize=9.5,
                color=INK_SECONDARY)

    empty = False
    y = top - head_h
    for r, row in enumerate(grid.rows):
        y -= CHIP_H
        ax.text(LEFT - 0.18, y + CHIP_H / 2, row, ha="right", va="center", fontsize=10.5, color=INK)
        c = 0
        while c < len(grid.columns):
            last = grid.spans.get((r, c), c)
            runs = sorted(grid.cells.get((r, c), []), key=lambda run: run.seed)
            slots = grid.columns[c][1]
            if last != c:
                draw_chip(ax, xs[c], y, xs[last] + widths[last] - xs[c], runs[0], grid.note(runs[0]))
            elif len(runs) > slots:
                raise SystemExit(f"{row}, {grid.columns[c][0]}: {len(runs)} runs for {slots} places")
            else:
                for slot in range(slots):
                    x = xs[c] + slot * (grid.chip_w + GAP)
                    if slot < len(runs):
                        draw_chip(ax, x, y, grid.chip_w, runs[slot], grid.note(runs[slot]))
                    else:
                        draw_empty(ax, x, y, grid.chip_w)
                        empty = True
            c = last + 1
        y -= ROW_GAP
    draw_legend(ax, 0.26, empty)
    return fig


def tests_note(run: Run) -> str:
    return "tests pass" if run.passed else ""


def rows_of(order: tuple[str, ...], present: set[str]) -> tuple[str, ...]:
    return tuple(name for name in order if name in present)


def cells_by(runs: list[Run], place: Callable[[Run], tuple[int, int]]) -> dict[tuple[int, int], list[Run]]:
    cells: dict[tuple[int, int], list[Run]] = {}
    for run in runs:
        cells.setdefault(place(run), []).append(run)
    return cells


def disregulator_rungs(runs: list[Run]) -> Grid:
    """The team's rung by worker model and team: no disregulator, then DeepSeek disregulators in teams of 4, 5, 10."""
    shown = [run for run in runs if run.influencer in (None, DISREGULATORS[0])]
    rows = rows_of(WORKERS, {run.worker for run in shown})
    columns = tuple((f"{w} workers" + (f", {d} disregulator{'s' * (d > 1)}" if d else ""), 3 if d else 1)
                    for w, d in SHAPES)
    cells = cells_by(shown, lambda run: (rows.index(run.worker), SHAPES.index((run.workers, run.disregulators))))
    return Grid("Team cheating with DeepSeek disregulators", "One chip per run: the team's Cheating rung.", rows,
                columns, (("no disregulator", 0, 0), (f"{DISREGULATORS[0]} disregulators", 1, len(SHAPES) - 1)),
                0.98, cells, tests_note)


def disregulator_models(runs: list[Run]) -> Grid:
    """The team's rung by the disregulator's model, for the worker models that met more than one, in teams of
    three workers and one disregulator."""
    teams = [run for run in runs if (run.workers, run.disregulators) == SHAPES[1]]
    several = {worker for worker in WORKERS if len({run.influencer for run in teams if run.worker == worker}) > 1}
    shown = [run for run in teams if run.worker in several]
    rows = rows_of(WORKERS, several)
    models = rows_of(DISREGULATORS, {run.influencer for run in shown})
    cells = cells_by(shown, lambda run: (rows.index(run.worker), models.index(run.influencer)))
    columns = tuple((model, max(len(cells.get((r, k), [])) for r in range(len(rows))))
                    for k, model in enumerate(models))
    return Grid("Team cheating by disregulator model", "Three workers and one disregulator. One chip per run.", rows,
                columns, (("the disregulator's model", 0, len(models) - 1),), 0.98, cells, tests_note)


def regulator_rungs(runs: list[Run]) -> Grid:
    """The team's rung and its hand-in by the regulators' model and how many of the team they are."""
    shares = sorted({run.regulators for run in runs if run.regulators})
    size = {run.workers + run.regulators for run in runs}
    if len(size) != 1:
        raise SystemExit(f"{REGULATOR_STUDY}: teams of {sorted(size)}, one size expected")
    models = rows_of(REGULATORS, {run.influencer for run in runs if run.regulators})
    rows = ("no regulator",) + tuple(f"{model} regulators" for model in models)
    cells = cells_by(runs, lambda run: (1 + models.index(run.influencer), shares.index(run.regulators))
                     if run.regulators else (0, 0))
    columns = tuple((f"{share} of {min(size)} are regulators", 1) for share in shares)
    subtitle = f"Teams of {min(size)} with {runs[0].worker} workers. One run per cell."
    return Grid("Team cheating by regulator model", subtitle, rows, columns, (), 2.5, cells,
                lambda run: f"{run.hand_in} · tests {'pass' if run.passed else 'fail'}", {(0, 0): len(shares) - 1})


def take_backs(runs: list[Run]):
    """How many hand-ins each regulated team took back: one panel per share, one bar per regulator model."""
    regulated = [run for run in runs if run.regulators]
    shares = sorted({run.regulators for run in regulated})
    models = rows_of(REGULATORS, {run.influencer for run in regulated})
    size = min(run.workers + run.regulators for run in regulated)
    top = max(run.take_backs for run in regulated)
    fig, axes = plt.subplots(1, len(shares), figsize=(8.6, 3.6), sharex=True, sharey=True)
    fig.subplots_adjust(left=0.13, right=0.97, top=0.72, bottom=0.14, wspace=0.12)
    for ax, share in zip(axes, shares):
        style_axes(ax, grid="x")
        by_model = {run.influencer: run for run in regulated if run.regulators == share}
        for k, model in enumerate(models):
            run = by_model[model]
            ax.barh(k, run.take_backs, height=0.56, color=CATEGORICAL[0])
            ax.text(run.take_backs + top * 0.02, k, str(run.take_backs), ha="left", va="center", fontsize=10.5,
                    color=INK)
        ax.set_yticks(range(len(models)), models, fontsize=10.5)
        ax.set_xlim(0, top * 1.12)
        ax.set_title(f"{share} of {size} are regulators", loc="left", fontsize=10.5, fontweight="bold", color=INK)
        ax.set_xlabel("hand-ins taken back", fontsize=9.5, color=INK_SECONDARY)
    axes[0].invert_yaxis()   # the panels share the axis: the first model on top
    fig.text(0.02, 0.93, "Hand-ins taken back", ha="left", va="center", fontsize=16, fontweight="bold", color=INK)
    fig.text(0.02, 0.855, f"Teams of {size} with {regulated[0].worker} workers. One run per bar.", ha="left",
             va="center", fontsize=10.5, color=INK_SECONDARY)
    return fig


def main() -> int:
    disregulated, regulated = read_study(DISREGULATOR_STUDY), read_study(REGULATOR_STUDY)
    figures = {
        f"{DISREGULATOR_STUDY}_rungs": draw_grid(disregulator_rungs(disregulated)),
        f"{DISREGULATOR_STUDY}_disregulators": draw_grid(disregulator_models(disregulated)),
        f"{REGULATOR_STUDY}_rungs": draw_grid(regulator_rungs(regulated)),
        f"{REGULATOR_STUDY}_take_backs": take_backs(regulated),
    }
    out = ROOT / "tmp"
    out.mkdir(exist_ok=True)
    for name, fig in figures.items():
        fig.savefig(out / f"{name}.png", dpi=PRINT_DPI)
        plt.close(fig)
        print(out / f"{name}.png")
    print(f"{len(disregulated)} runs of {DISREGULATOR_STUDY}, {len(regulated)} of {REGULATOR_STUDY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
