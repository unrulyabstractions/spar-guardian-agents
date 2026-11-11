"""SWE (fixed task) regulators with DeepSeek V4.1 Flash workers, valid runs only, read from each run's run.json and
src.measures.run_validity at build time. Writes tmp/tables/results/table_swe_regulators.png.

    uv run python -m scripts.paper.swe_regulator_table    # make results-tables runs every one
"""
from __future__ import annotations

import json
import re
import sys

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch, Rectangle

from src.configuration.settings import ROOT
from src.measures.run_validity import validity
from src.utils.figure_style import (
    AXIS,
    BAND,
    COLLABORATION_FILL,
    COLLABORATION_TEXT,
    GOOD,
    INK,
    INK_SECONDARY,
    MUTED,
    PRINT_DPI,
    RUNG_FILL,
    RUNG_TEXT,
    WHITE,
)

RUNS = ROOT / "runs" / "study_exploration_1"
OUT = ROOT / "tmp" / "tables" / "results" / "table_swe_regulators.png"
ORDER = ["none", "exposed", "tried", "opened", "used", "tampered", "checked", "shipped"]
LETTER = {"none": "·", "exposed": "E", "tried": "T", "opened": "O", "used": "U", "tampered": "X", "checked": "C",
          "shipped": "S"}
ROWS = [
    ("None", "baseline", "flipt_minimal_dsflash_fixed"),
    ("Claude Haiku 5.5", "ambiguous", "flipt_minimal_regulated_dsflash_haiku_ambiguous_fixed"),
    ("Claude Haiku 5.5", "enforce", "flipt_minimal_regulated_dsflash_haiku_enforce_fixed"),
    ("GPT-6 Luna", "ambiguous", "flipt_minimal_regulated_dsflash_luna_ambiguous_fixed",
     "flipt_minimal_regulated_dsflash_luna_ambiguous_fill"),
    ("GPT-6 Luna", "enforce", "flipt_minimal_regulated_dsflash_luna_enforce_fixed",
     "flipt_minimal_regulated_dsflash_luna_enforce_fill"),
    ("Claude Sonnet 5.5", "ambiguous", "flipt_minimal_regulated_dsflash_sonnet_ambiguous_fill"),
    ("Claude Sonnet 5.5", "enforce", "flipt_minimal_regulated_dsflash_sonnet_enforce_fill"),
]


def read(name: str) -> dict:
    run = RUNS / name
    data = json.loads((run / "run.json").read_text())
    v = validity(run)
    team = data["signals"]["team"]
    shipped = set()
    for line in team.get("shipped_evidence") or []:
        shipped |= set(re.findall(r"piece ([ABC])", line))
    regs = v.get("regulators") or []
    reverts = None
    if regs:
        reg = regs[0]["agent"]
        log = ((data.get("swe") or {}).get("team") or {}).get("log") or ""
        reverts = sum(1 for ln in log.splitlines() if ln.split()[1:2] == [reg] and "Revert" in ln)
    return {"name": name, "valid": v["valid"], "rung": team["rung"], "passed": bool(team.get("passed")),
            "pieces": shipped, "collab": team.get("collaboration"), "posts": regs[0]["posts"] if regs else None,
            "reverts": reverts}


def main() -> int:
    table = []
    for label, level, *prefixes in ROWS:
        runs = []
        for prefix in prefixes:
            found = [p for p in RUNS.iterdir() if re.fullmatch(re.escape(prefix) + r"\d+", p.name)]
            for path in sorted(found, key=lambda p: int(re.search(r"(\d+)$", p.name).group(1))):
                if (path / "run.json").is_file():
                    runs.append(read(path.name))
        kept = [r for r in runs if r["valid"]]
        print(label, level, "kept", [(r["name"], r["rung"], sorted(r["pieces"]), r["passed"], r["collab"], r["posts"],
                                      r["reverts"]) for r in kept],
              "dropped", [r["name"] for r in runs if not r["valid"]])
        if kept:
            table.append((label, level, kept))

    widths = [3.0, 1.55, 2.35, 1.05, 3.05, 2.0]
    header = ["Regulator × level", "Rungs", "Pieces shipped", "Pass", "Collaboration", "Regulator posts; reverts"]
    title_h, head_h, row_h, legend_h = 0.55, 0.5, 0.72, 0.6
    width = sum(widths)
    height = title_h + head_h + row_h * len(table) + legend_h
    fig = Figure(figsize=(width, height), dpi=PRINT_DPI)
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.axis("off")
    pad = 0.16
    xs = [sum(widths[:j]) for j in range(len(widths))]

    ax.text(pad, title_h / 2 + 0.04, "Software engineering", fontsize=11.5, weight="bold", color=INK, va="center")
    ax.text(pad + 2.18, title_h / 2 + 0.05, "DeepSeek V4.1 Flash workers", fontsize=9, color=INK_SECONDARY,
            va="center")
    y = title_h
    for text, x in zip(header, xs):
        ax.text(x + pad, y + head_h / 2, text.upper(), fontsize=6.5, family="DejaVu Sans Mono", color=INK_SECONDARY,
                va="center")
    y += head_h
    ax.plot([0, width], [y, y], color=INK, lw=1.0)

    groups = list(dict.fromkeys(row[0] for row in table))
    level_x = pad + 1.75
    chip, gap = 0.3, 0.08
    for i, (label, level, runs) in enumerate(table):
        y0 = y + i * row_h
        cy = y0 + row_h / 2 - 0.05
        if groups.index(label) % 2:
            ax.add_patch(Rectangle((0, y0), width, row_h, fc=BAND, ec="none", zorder=0))
        if i == 0 or table[i - 1][0] != label:
            ax.text(pad, cy, label, fontsize=9.2, weight="bold", color=INK, va="center")
        ax.text(level_x, cy, level, fontsize=8.6, color=INK_SECONDARY, va="center",
                style="italic" if label == "None" else "normal")
        for k, r in enumerate(runs):
            # rung chip, a check under it when the hidden tests passed
            x = xs[1] + pad + k * (chip + gap)
            ax.add_patch(FancyBboxPatch((x, cy - chip / 2), chip, chip, boxstyle="round,pad=0,rounding_size=0.035",
                                        fc=RUNG_FILL[r["rung"]], ec="none"))
            ax.text(x + chip / 2, cy, LETTER[r["rung"]], fontsize=8.5, weight="bold",
                    color=RUNG_TEXT.get(r["rung"], INK), ha="center", va="center")
            if r["passed"]:
                ax.text(x + chip / 2, cy + chip / 2 + 0.13, "✓", fontsize=9.5, weight="bold", color=GOOD,
                        ha="center", va="center")
            # pieces A, B, C: filled when on the submitted branch
            s, sg = 0.17, 0.035
            px = xs[2] + pad + k * (3 * s + 2 * sg + 0.2)
            for m, piece in enumerate("ABC"):
                on = piece in r["pieces"]
                bx = px + m * (s + sg)
                ax.add_patch(FancyBboxPatch((bx, cy - s / 2), s, s, boxstyle="round,pad=0,rounding_size=0.025",
                                            fc=INK if on else WHITE, ec=INK if on else AXIS, lw=0.7))
                ax.text(bx + s / 2, cy, piece, fontsize=6.3, weight="bold", color=WHITE if on else MUTED,
                        ha="center", va="center")
            # collaboration pill
            pw, ph = 0.86, 0.24
            cx = xs[4] + pad + k * (pw + 0.07)
            lvl = r["collab"] or "isolated"
            ax.add_patch(FancyBboxPatch((cx, cy - ph / 2), pw, ph, boxstyle="round,pad=0,rounding_size=0.06",
                                        fc=COLLABORATION_FILL[lvl], ec="none"))
            ax.text(cx + pw / 2, cy, lvl, fontsize=7.2, color=COLLABORATION_TEXT.get(lvl, INK), ha="center",
                    va="center")
            # regulator posts; reverts
            qx = xs[5] + pad + k * 0.6
            ax.text(qx, cy, "n/a" if r["posts"] is None and k == 0 else ("" if r["posts"] is None
                    else f"{r['posts']}; {r['reverts']}"), fontsize=8.8,
                    color=MUTED if r["posts"] is None else INK, va="center")
        passed = sum(r["passed"] for r in runs)
        ax.text(xs[3] + pad, cy, f"{passed} of {len(runs)}", fontsize=9.2, weight="bold", color=INK, va="center")
        same_next = i + 1 < len(table) and table[i + 1][0] == label
        ax.plot([0 if not same_next else level_x - 0.04, width], [y0 + row_h, y0 + row_h], color=AXIS,
                lw=0.35 if same_next else 0.7)

    # legend: the rungs shown, the check, a piece on and off the submitted branch
    ly = y + row_h * len(table) + legend_h / 2
    x = pad
    s = 0.2
    shown = {r["rung"] for _, _, runs in table for r in runs}
    for rung in [r for r in ORDER if r in shown]:
        ax.add_patch(FancyBboxPatch((x, ly - s / 2), s, s, boxstyle="round,pad=0,rounding_size=0.03",
                                    fc=RUNG_FILL[rung], ec="none"))
        ax.text(x + s / 2, ly, LETTER[rung], fontsize=6.5, weight="bold", color=RUNG_TEXT.get(rung, INK),
                ha="center", va="center")
        ax.text(x + s + 0.07, ly, rung, fontsize=7.8, color=INK_SECONDARY, va="center")
        x += s + 0.07 + len(rung) * 0.075 + 0.3
    ax.text(x, ly, "✓", fontsize=10, weight="bold", color=GOOD, va="center")
    ax.text(x + 0.18, ly, "hidden tests passed", fontsize=7.8, color=INK_SECONDARY, va="center")
    x += 0.18 + 1.45
    ps = 0.17
    ax.add_patch(FancyBboxPatch((x, ly - ps / 2), ps, ps, boxstyle="round,pad=0,rounding_size=0.025", fc=INK, ec=INK,
                                lw=0.7))
    ax.text(x + ps / 2, ly, "A", fontsize=6.3, weight="bold", color=WHITE, ha="center", va="center")
    ax.text(x + ps + 0.07, ly, "lines of this piece on the submitted branch", fontsize=7.8, color=INK_SECONDARY,
            va="center")
    x += ps + 0.07 + 2.9
    ax.add_patch(FancyBboxPatch((x, ly - ps / 2), ps, ps, boxstyle="round,pad=0,rounding_size=0.025", fc=WHITE,
                                ec=AXIS, lw=0.7))
    ax.text(x + ps / 2, ly, "A", fontsize=6.3, weight="bold", color=MUTED, ha="center", va="center")
    ax.text(x + ps + 0.07, ly, "missing", fontsize=7.8, color=INK_SECONDARY, va="center")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=PRINT_DPI, facecolor=WHITE)
    print(OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
