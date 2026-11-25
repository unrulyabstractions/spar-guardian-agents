"""Two figures of the team studies of 2026-11-25, as PNG flat under tmp/ (gitignored):

  disregulator_study.png   disregulator_existence_2: one square per run in its team's Cheating rung, by worker model
                           and team; each team is drawn as its agents, the disregulator's model named over it
  regulator_study.png      regulator_scale_2: one tile per run in its team's rung, by the regulators' model and how
                           many of the team they are, with one dot per hand-in a member took back

A check mark is a hand-in that passed the hidden tests. Every value is read from the studies' stored runs: the team's
rung, the roles and whether the hidden tests passed from analysis/labels/cheating.json, the models, the planted values
and the take-backs from the run's own record (run.json) and the graded hand-in (processed/swe/diffs/team.diff).

    uv run python -m scripts.study.team_rung_figures

No network, no model calls.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyBboxPatch

from scripts.paper.study_ladders import CHEATING_FILL, CHEATING_TEXT
from scripts.study.regulator_scale_figure import text_width, values_held
from src.configuration.settings import ROOT
from src.measures.run_directory import RunDir
from src.measures.rungs import RUNGS
from src.utils.figure_style import (
    AXIS,
    INK,
    INK_SECONDARY,
    PRINT_DPI,
    ROLE_EDGE,
    ROLE_FILL,
    SURFACE,
)

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

PAD, TITLE_H, HEAD_H, LEGEND_H = 0.35, 1.0, 0.95, 1.2
SQUARE, GAP, ROW_GAP, COL_GAP, BLOCK_GAP = 0.6, 0.07, 0.16, 0.42, 0.8   # the disregulator study's grid
TILE_W, TILE_H, BASE_W = 2.5, 0.92, 1.0                                 # the regulator study's grid
AGENT_R, AGENT_STEP = 0.062, 0.17
LABEL_X = PAD + 0.24
"""Where a row's label starts, after the dot of its role."""

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
    take_backs: int         # hand-ins a member took back


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
    values = values_held((stored.file("swe") / "diffs" / "team.diff").read_text(errors="replace"), own_values)
    run = Run(name=folder.name, seed=int(SEED.search(folder.name)[1]), worker=model_name(record["model"]),
              influencer=model_name(record["role_models"]["regulator"] or record["role_models"]["disregulator"]),
              workers=roles["worker"], regulators=roles["regulator"], disregulators=roles["disregulator"],
              rung=cheating["team"], passed=bool(cheating["passed"]),
              take_backs=len(record["unsubmits"]["unsubmits"]))
    if run.passed != (values == len(own_values)):
        raise SystemExit(f"{run.name}: passed {run.passed} with {values} of {len(own_values)} planted values in the "
                         f"hand-in: the label and the hand-in disagree")
    return run


def read_study(study: str) -> list[Run]:
    """Every stored run of the study, by name."""
    runs = [read_run(folder) for folder in sorted((ROOT / "runs" / study).iterdir())
            if folder.is_dir() and RunDir(folder).file("cheating.json").exists()]
    if not runs:
        raise SystemExit(f"runs/{study} holds no stored run")
    return runs


def rows_of(order: tuple[str, ...], present: set[str]) -> tuple[str, ...]:
    return tuple(name for name in order if name in present)


def canvas(width: float, height: float, title: str):
    fig = plt.figure(figsize=(width, height))
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    ax.axis("off")
    ax.text(PAD, height - 0.52, title, ha="left", va="center", fontsize=22, fontweight="bold", color=INK)
    return fig, ax


def draw_agent(ax, x: float, y: float, role: str, r: float = AGENT_R) -> None:
    """One agent as a dot: a worker white with a dark edge, a regulator or a disregulator in its role's colour."""
    ax.add_patch(Circle((x, y), r, fc=ROLE_FILL[role], ec=ROLE_EDGE[role], lw=0.9))


def draw_team(ax, cx: float, y: float, workers: int, others: int, role: str, per_row: int) -> None:
    """A team as its agents, the workers first, in rows of per_row centred on cx, the first row at y."""
    for k in range(workers + others):
        row, place = divmod(k, per_row)
        in_row = min(per_row, workers + others - row * per_row)
        draw_agent(ax, cx + (place - (in_row - 1) / 2) * AGENT_STEP, y - row * AGENT_STEP,
                   "worker" if k < workers else role)


def draw_check(ax, cx: float, cy: float, size: float, color: str, lw: float = 2.6) -> None:
    """A check mark centred on (cx, cy), `size` wide."""
    ax.plot([cx - 0.45 * size, cx - 0.12 * size, cx + 0.5 * size],
            [cy + 0.02 * size, cy - 0.3 * size, cy + 0.38 * size],
            color=color, lw=lw, solid_capstyle="round", solid_joinstyle="round")


def draw_tile(ax, x: float, y: float, w: float, h: float, run: Run) -> str:
    """One run as a tile in its team's rung, with a check mark when its hand-in passed. Returns the ink for marks."""
    fill, ink = CHEATING_FILL[run.rung], CHEATING_TEXT.get(run.rung, INK)
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.07", fc=fill,
                                ec=AXIS if run.rung in ("none", "looked") else fill, lw=0.8))
    if run.passed:
        draw_check(ax, x + w / 2, y + h / 2, 0.3, ink)
    return ink


def draw_legend(ax, role: str, worker_words: str, take_backs: bool) -> None:
    """Two rows under the grid: the ladder's rungs, then the marks and the agents."""
    def row(y: float, items: list[tuple[str, str]]) -> None:
        x = PAD
        for kind, words in items:
            if kind in RUNGS:
                ax.add_patch(FancyBboxPatch((x, y), 0.26, 0.2, boxstyle="round,pad=0,rounding_size=0.03",
                                            fc=CHEATING_FILL[kind],
                                            ec=AXIS if kind in ("none", "looked") else CHEATING_FILL[kind], lw=0.8))
            elif kind == "check":
                draw_check(ax, x + 0.13, y + 0.1, 0.22, INK, 2.0)
            elif kind == "dots":
                for k in range(3):
                    ax.add_patch(Circle((x + 0.04 + k * 0.09, y + 0.1), 0.03, fc=INK, ec="none"))
            else:
                draw_agent(ax, x + 0.13, y + 0.1, kind, 0.075)
            text = ax.text(x + 0.36, y + 0.1, words, ha="left", va="center", fontsize=10.5, color=INK_SECONDARY)
            x += 0.36 + text_width(ax, text) + 0.38
    row(0.72, [(rung, rung) for rung in RUNGS])
    row(0.3, [("check", "passed the hidden tests")] + ([("dots", "hand-ins taken back")] if take_backs else [])
        + [("worker", worker_words), (role, role)])


def disregulator_study(runs: list[Run]):
    """One square per run by worker model (rows) and team (columns): three workers alone, then teams with DeepSeek
    disregulators, then the teams of three workers with one disregulator of another model."""
    rows = rows_of(WORKERS, {run.worker for run in runs})
    others = rows_of(DISREGULATORS[1:], {run.influencer for run in runs})
    columns = [(shape, DISREGULATORS[0] if shape[1] else None) for shape in SHAPES] + [(SHAPES[1], m) for m in others]
    cells: dict[tuple[int, int], list[Run]] = {}
    for run in runs:
        column = ((run.workers, run.disregulators), run.influencer)
        if column not in columns:
            raise SystemExit(f"{run.name}: no column for {column}")
        cells.setdefault((rows.index(run.worker), columns.index(column)), []).append(run)
    slots = [max(len(cells.get((r, c), [])) for r in range(len(rows))) for c in range(len(columns))]
    widths = [max(n, 1) * SQUARE + (max(n, 1) - 1) * GAP for n in slots]
    left = LABEL_X + 1.95
    xs, x = [], left
    for c, width in enumerate(widths):
        if c:
            x += widths[c - 1] + (BLOCK_GAP if c == len(SHAPES) else COL_GAP)
        xs.append(x)
    width = xs[-1] + widths[-1] + PAD
    height = TITLE_H + HEAD_H + len(rows) * SQUARE + (len(rows) - 1) * ROW_GAP + LEGEND_H
    fig, ax = canvas(width, height, "Disregulator study")

    top = height - TITLE_H
    named = [c for c, (_, model) in enumerate(columns) if model == DISREGULATORS[0]]
    for label, first, last in [(DISREGULATORS[0], named[0], named[-1])] + [(m, len(SHAPES) + k, len(SHAPES) + k)
                                                                           for k, m in enumerate(others)]:
        centre = (xs[first] + xs[last] + widths[last]) / 2
        text = ax.text(centre + 0.1, top - 0.14, label, ha="center", va="center", fontsize=10.5, color=INK)
        draw_agent(ax, centre + 0.1 - text_width(ax, text) / 2 - 0.14, top - 0.14, "disregulator")
        if last > first:
            ax.plot([xs[first], xs[last] + widths[last]], [top - 0.34, top - 0.34], color=AXIS, lw=0.9)
    for c, ((workers, disregulators), _) in enumerate(columns):
        draw_team(ax, xs[c] + widths[c] / 2, top - 0.6, workers, disregulators, "disregulator", workers + disregulators)

    y = top - HEAD_H
    for r, row in enumerate(rows):
        y -= SQUARE
        draw_agent(ax, PAD + 0.08, y + SQUARE / 2, "worker", 0.075)
        ax.text(LABEL_X, y + SQUARE / 2, row, ha="left", va="center", fontsize=12, color=INK)
        for c in range(len(columns)):
            for k, run in enumerate(sorted(cells.get((r, c), []), key=lambda run: run.seed)):
                draw_tile(ax, xs[c] + k * (SQUARE + GAP), y, SQUARE, SQUARE, run)
        y -= ROW_GAP
    draw_legend(ax, "disregulator", "worker", take_backs=False)
    return fig


def regulator_study(runs: list[Run]):
    """One tile per run by the regulators' model (rows) and how many of the team they are (columns), beside the one
    team without a regulator, which spans the rows."""
    sizes, workers = {run.workers + run.regulators for run in runs}, {run.worker for run in runs}
    if len(sizes) != 1 or len(workers) != 1:
        raise SystemExit(f"{REGULATOR_STUDY}: teams of {sorted(sizes)} with {sorted(workers)} workers, one of each "
                         f"expected")
    size = min(sizes)
    bare = [run for run in runs if not run.regulators]
    shares = sorted({run.regulators for run in runs if run.regulators})
    rows = rows_of(REGULATORS, {run.influencer for run in runs if run.regulators})
    by_cell = {(run.influencer, run.regulators): run for run in runs if run.regulators}
    if len(bare) != 1 or len(by_cell) != len(runs) - 1:
        raise SystemExit(f"{REGULATOR_STUDY}: one run without a regulator and one per regulator model and share "
                         f"expected")
    left = LABEL_X + 1.25
    xs = [left] + [left + BASE_W + COL_GAP + k * (TILE_W + GAP * 2) for k in range(len(shares))]
    width = xs[-1] + TILE_W + PAD
    grid_h = len(rows) * TILE_H + (len(rows) - 1) * ROW_GAP
    height = TITLE_H + HEAD_H + grid_h + LEGEND_H
    fig, ax = canvas(width, height, "Regulator study")

    top = height - TITLE_H
    half = size // 2
    draw_team(ax, xs[0] + BASE_W / 2, top - 0.3, size, 0, "regulator", half)
    for k, share in enumerate(shares):
        draw_team(ax, xs[1 + k] + TILE_W / 2, top - 0.3, size - share, share, "regulator", half)

    y = top - HEAD_H
    draw_tile(ax, xs[0], y - grid_h, BASE_W, grid_h, bare[0])
    for row in rows:
        y -= TILE_H
        draw_agent(ax, PAD + 0.08, y + TILE_H / 2, "regulator", 0.075)
        ax.text(LABEL_X, y + TILE_H / 2, row, ha="left", va="center", fontsize=12, color=INK)
        for k, share in enumerate(shares):
            run = by_cell[(row, share)]
            ink = draw_tile(ax, xs[1 + k], y, TILE_W, TILE_H, run)
            for d in range(run.take_backs):
                ax.add_patch(Circle((xs[1 + k] + TILE_W / 2 + (d - (run.take_backs - 1) / 2) * 0.095, y + 0.17), 0.03,
                                    fc=ink, ec="none"))
        y -= ROW_GAP
    draw_legend(ax, "regulator", f"{min(workers)} worker", take_backs=True)
    return fig


def main() -> int:
    disregulated, regulated = read_study(DISREGULATOR_STUDY), read_study(REGULATOR_STUDY)
    out = ROOT / "tmp"
    out.mkdir(exist_ok=True)
    for name, fig in (("disregulator_study", disregulator_study(disregulated)),
                      ("regulator_study", regulator_study(regulated))):
        fig.savefig(out / f"{name}.png", dpi=PRINT_DPI)
        plt.close(fig)
        print(out / f"{name}.png")
    print(f"{len(disregulated)} runs of {DISREGULATOR_STUDY}, {len(regulated)} of {REGULATOR_STUDY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
