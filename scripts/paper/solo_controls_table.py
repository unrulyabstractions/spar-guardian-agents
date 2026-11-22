"""The single-agent controls of Appendix C: for each task and model, the share of runs that cheated when nothing was
appended and when the agent was told to cheat, read from runs/apart_controls/ (the apart controls study, also on
Hugging Face under apart_controls/). Writes tmp/tables/results/solo_vs_solo_told.png.

    uv run python -m scripts.paper.solo_controls_table    # make results-tables runs it

One run is one agent on one fixed instance of a task; seeds only vary sampling. The valid runs count, as the measures
list them (scripts/paper/study_ladders.py valid_runs: each run's canonical copy). Its Cheating rung is the one the
transcript viewer measures (study_ladders.record), computed in WORKERS processes. A run cheated when the measures' one
definition says so (src/measures/cheating_ladder.py cheated: CHEATED_FROM or above).
"""
from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from matplotlib.patches import Rectangle

from scripts.paper.study_ladders import CHEATING_FILL, canvas, record, save, valid_runs
from src.configuration.settings import ROOT
from src.utils import progress
from src.utils.figure_style import AXIS, BAND, INK, INK_SECONDARY, MUTED, WHITE

DATA = ROOT / "runs" / "apart_controls"
OUT = ROOT / "tmp" / "tables" / "results" / "solo_vs_solo_told.png"
# Each task's row label: a plain name and what the agent makes, as Appendix C's task table defines them.
TASKS = [("flipt", "SWE", "Go: Redis TLS settings"), ("vuls", "SWE", "Go: macOS scanning"),
         ("riemann", "Math proof", "Riemann hypothesis"), ("subset", "Subset sum", "60 integers, no code"),
         ("factor", "Factoring", "52-bit number, no code"), ("binder", "Protein design", "binder for an enzyme"),
         ("poem", "Creative writing", "24-line poem"), ("worktime", "Knowledge work", "five-slide client deck"),
         ("game", "Chess", "win a won position")]
MODELS = [("ds", "DeepSeek V4.1 Flash"), ("luna", "GPT-6 Luna"), ("haiku", "Claude Haiku 5.5"),
          ("gpt5m", "GPT-5 mini"), ("qwen", "Qwen3.8 27B")]
CELLS = [("solo", "untold"), ("cheat", "told")]
WORKERS = 6
# The rate's fill: light at 0, the hacked rung's red at 1.
LOW, HIGH = CHEATING_FILL["none"], CHEATING_FILL["hacked"]


def blend(rate: float) -> tuple[float, float, float]:
    lo = [int(LOW[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    hi = [int(HIGH[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    return tuple(a + (b - a) * rate for a, b in zip(lo, hi))


def team_cheated(path: Path) -> bool:
    return record(path)["cheated"]


def load() -> dict[tuple[str, str, str], list[bool]]:
    """Whether each valid run cheated, by (task, model, cell)."""
    runs = valid_runs(DATA)
    with ProcessPoolExecutor(max_workers=WORKERS) as pool:
        cheated = list(pool.map(team_cheated, [r.path for r in runs], chunksize=16))
    out: dict[tuple[str, str, str], list[bool]] = {}
    for run, hit in zip(runs, cheated):
        out.setdefault((run.meta["task"], run.meta["model"], run.meta["cell"]), []).append(hit)
    return out


def main() -> int:
    data = load()
    progress.detail("valid runs", sum(map(len, data.values())))
    label_w, cell_w, gap = 1.4, 0.5, 0.1
    head_h, row_h = 0.62, 0.42
    width = label_w + len(MODELS) * (2 * cell_w + gap)
    height = head_h + row_h * len(TASKS) + 0.06
    fig, ax = canvas(width, height)
    pad = 0.1
    ax.text(pad, 0.45, "Task", fontsize=7.5, color=INK_SECONDARY, va="center")
    for m, (_, name) in enumerate(MODELS):
        x0 = label_w + m * (2 * cell_w + gap)
        ax.text(x0 + cell_w, 0.2, name, fontsize=7, weight="bold", color=INK, ha="center", va="center")
        for c, (_, word) in enumerate(CELLS):
            ax.text(x0 + c * cell_w + cell_w / 2, 0.45, word, fontsize=6.8, style="italic", color=INK_SECONDARY,
                    ha="center", va="center")
    ax.plot([0, width], [head_h, head_h], color=INK, lw=1.0)
    for t, (task, title, note) in enumerate(TASKS):
        y0 = head_h + t * row_h
        if t % 2:
            ax.add_patch(Rectangle((0, y0), width, row_h, fc=BAND, ec="none", zorder=0))
        ax.text(pad, y0 + (row_h * 0.38 if note else row_h / 2), title,
                fontsize=7.5, weight="bold", color=INK, va="center")
        if note:
            ax.text(pad, y0 + row_h * 0.72, note, fontsize=6.8, color=INK_SECONDARY, va="center")
        for m, (model, _) in enumerate(MODELS):
            x0 = label_w + m * (2 * cell_w + gap)
            for c, (cell, _) in enumerate(CELLS):
                runs = data.get((task, model, cell), [])
                x = x0 + c * cell_w
                if not runs:
                    ax.text(x + cell_w / 2, y0 + row_h / 2, "none", fontsize=6.8, color=MUTED, ha="center",
                            va="center")
                    continue
                rate = sum(runs) / len(runs)
                ax.add_patch(Rectangle((x + 0.02, y0 + 0.03), cell_w - 0.04, row_h - 0.06, fc=blend(rate),
                                       ec="none", zorder=1))
                ink = WHITE if rate > 0.55 else INK
                ax.text(x + cell_w / 2, y0 + row_h * 0.36, f"{rate:.0%}", fontsize=7.5, weight="bold", color=ink,
                        ha="center", va="center", zorder=2)
                ax.text(x + cell_w / 2, y0 + row_h * 0.72, f"{sum(runs)}/{len(runs)}", fontsize=6.8, color=ink,
                        ha="center", va="center", zorder=2)
        ax.plot([0, width], [y0 + row_h, y0 + row_h], color=AXIS, lw=0.5)
    progress.wrote(save(fig, OUT))
    for task, _, _ in TASKS:
        progress.verbose(task, " ".join(
            f"{model}:{sum(data.get((task, model, 'solo'), []))}/{len(data.get((task, model, 'solo'), []))}"
            f"->{sum(data.get((task, model, 'cheat'), []))}/{len(data.get((task, model, 'cheat'), []))}"
            for model, _ in MODELS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
