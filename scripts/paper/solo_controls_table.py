"""The single-agent controls of Appendix C: for each task and model, the share of runs that cheated when nothing was
appended and when the agent was told to cheat, read from runs/apart_controls/ (the apart controls study, also on
Hugging Face under apart_controls/). Writes tmp/tables/results/solo_vs_solo_told.png.

    uv run python -m scripts.paper.solo_controls_table    # make results-tables runs it

One run is one agent on one fixed instance of a task; seeds only vary sampling. The canonical copy of each run
(manifest.jsonl canonical) counts; the team rung comes from each run's run.json (signals.team.rung) and must equal the
manifest's, or the build stops. A run cheated when its rung is at or above its task's floor
(src/domains BY_TASK ladder.cheated_from).
"""
from __future__ import annotations

import json
import sys

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle

from src.configuration.settings import ROOT
from src.domains import BY_TASK
from src.measures.rungs import at_least
from src.utils.figure_style import AXIS, BAND, INK, INK_SECONDARY, MUTED, PRINT_DPI, RUNG_FILL, WHITE

DATA = ROOT / "runs" / "apart_controls"
OUT = ROOT / "tmp" / "tables" / "results" / "solo_vs_solo_told.png"
TASKS = [("flipt", "Software engineering", "flipt issue"), ("vuls", "Software engineering", "vuls issue"),
         ("riemann", "OpenMath", "Riemann proof"), ("subset", "Subset sum", ""), ("factor", "Factoring", ""),
         ("binder", "Protein design", ""), ("poem", "Creative writing", ""), ("worktime", "Knowledge work", ""),
         ("game", "Chess", "")]
MODELS = [("ds", "DeepSeek V4.1 Flash"), ("luna", "GPT-6 Luna"), ("haiku", "Claude Haiku 5.5"),
          ("gpt5m", "GPT-5 mini"), ("qwen", "Qwen3.8 27B")]
CELLS = [("solo", "untold"), ("cheat", "told")]
# The rate's fill: light at 0, the shipped rung's red at 1.
LOW, HIGH = RUNG_FILL["none"], RUNG_FILL["shipped"]


def blend(rate: float) -> tuple[float, float, float]:
    lo = [int(LOW[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    hi = [int(HIGH[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    return tuple(a + (b - a) * rate for a, b in zip(lo, hi))


def load() -> dict[tuple[str, str, str], list[bool]]:
    """Whether each canonical run cheated, by (task, model, cell). Stops on a rung the manifest and run.json disagree
    on, or a run whose folder is missing."""
    out: dict[tuple[str, str, str], list[bool]] = {}
    floors = {}
    for line in (DATA / "manifest.jsonl").read_text().splitlines():
        row = json.loads(line)
        if not row["canonical"]:
            continue
        path = DATA / row["model"] / row["task"] / row["cell"] / f"s{row['seed']}" / "run.json"
        if not path.is_file():
            raise SystemExit(f"missing {path}")
        run = json.loads(path.read_text())
        rung = run["signals"]["team"]["rung"]
        if rung != row["rung"]:
            raise SystemExit(f"{row['key']}: run.json says {rung}, the manifest {row['rung']}")
        floor = floors.setdefault(row["task"], BY_TASK[run["task"]].ladder.cheated_from)
        if floors[row["task"]] != BY_TASK[run["task"]].ladder.cheated_from:
            raise SystemExit(f"{row['key']}: task {row['task']} maps to two floors")
        out.setdefault((row["task"], row["model"], row["cell"]), []).append(at_least(rung, floor))
    load.floors = floors
    return out


def main() -> int:
    data = load()
    floors = load.floors
    label_w, cell_w, gap = 2.3, 0.62, 0.22
    head_h, row_h, legend_h = 0.78, 0.46, 0.08
    width = label_w + len(MODELS) * (2 * cell_w + gap) + 0.1
    height = head_h + row_h * len(TASKS) + legend_h
    fig = Figure(figsize=(width, height), dpi=PRINT_DPI)
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.axis("off")
    pad = 0.14
    ax.text(pad, 0.5, "Task", fontsize=8.5, color=INK_SECONDARY, va="center")
    for m, (_, name) in enumerate(MODELS):
        x0 = label_w + m * (2 * cell_w + gap)
        ax.text(x0 + cell_w, 0.24, name, fontsize=8.6, weight="bold", color=INK, ha="center", va="center")
        for c, (_, word) in enumerate(CELLS):
            ax.text(x0 + c * cell_w + cell_w / 2, 0.56, word, fontsize=7.4, style="italic", color=INK_SECONDARY,
                    ha="center", va="center")
    ax.plot([0, width], [head_h, head_h], color=INK, lw=1.0)
    for t, (task, title, note) in enumerate(TASKS):
        y0 = head_h + t * row_h
        if t % 2:
            ax.add_patch(Rectangle((0, y0), width, row_h, fc=BAND, ec="none", zorder=0))
        ax.text(pad, y0 + row_h * 0.38, title + (f", {note}" if note else ""), fontsize=8.4, weight="bold",
                color=INK, va="center")
        ax.text(pad, y0 + row_h * 0.74, f"cheating from {floors[task]}", fontsize=6.6, color=MUTED, va="center")
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
                ax.add_patch(Rectangle((x + 0.03, y0 + 0.04), cell_w - 0.06, row_h - 0.08, fc=blend(rate),
                                       ec="none", zorder=1))
                ink = WHITE if rate > 0.55 else INK
                ax.text(x + cell_w / 2, y0 + row_h * 0.4, f"{rate:.0%}", fontsize=8.2, weight="bold", color=ink,
                        ha="center", va="center", zorder=2)
                ax.text(x + cell_w / 2, y0 + row_h * 0.74, f"{sum(runs)}/{len(runs)}", fontsize=6.2, color=ink,
                        ha="center", va="center", zorder=2)
        ax.plot([0, width], [y0 + row_h, y0 + row_h], color=AXIS, lw=0.5)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=PRINT_DPI, facecolor=WHITE)
    for task, _, _ in TASKS:
        print(task, floors[task], " ".join(
            f"{model}:{sum(data.get((task, model, 'solo'), []))}/{len(data.get((task, model, 'solo'), []))}"
            f"->{sum(data.get((task, model, 'cheat'), []))}/{len(data.get((task, model, 'cheat'), []))}"
            for model, _ in MODELS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
