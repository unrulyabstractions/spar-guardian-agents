"""Figure 4 of the paper: the multi-domain study's results, one tile per valid run, coloured and lettered by the team's
rung. Columns are the targets (SWE with DeepSeek workers, SWE with Luna workers, protein design, creative writing),
each with the lowest rung its task counts as cheating (Domain.ladder.cheated_from). Row groups: the team of three with
no rule, the generic rule and the identified rule; a hidden regulator at level ambiguous, then enforce, one row per
regulator model with runs; the team all told to cheat.

Source: runs/study_exploration_1/INDEX.jsonl (valid, non-mock runs; disregulator runs left out; SWE only on the fixed
task, run names ending in _fixed<seed>, _fill<seed> or _fixedpilot<seed>) and each run's run.json signals.team.rung.
The rules' wording comes from src/configuration/instruction_levels.yaml. The cells are checked against the results
tables' cell prefixes (control_tables, protein_regulator_table, swe_regulator_table); a disagreement fails the build.
Writes tmp/figures/paper/fig4_multidomain.png.

    uv run python -m scripts.paper.fig_multidomain
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict

import matplotlib
import yaml
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch

from scripts.paper import control_tables, protein_regulator_table, swe_regulator_table
from src.configuration.settings import ROOT
from src.domains import BY_TASK
from src.measures.run_validity import validity
from src.measures.rungs import RUNGS
from src.utils.figure_style import AXIS, GRID, INK, INK_SECONDARY, PRINT_DPI, RUNG_FILL, RUNG_TEXT, SURFACE

STUDY = ROOT / "runs" / "study_exploration_1"
INDEX = STUDY / "INDEX.jsonl"
LEVELS = ROOT / "src" / "configuration" / "instruction_levels.yaml"
OUT = ROOT / "tmp" / "figures" / "paper" / "fig4_multidomain.png"

LETTER = {"none": "N", "exposed": "E", "tried": "T", "opened": "O", "used": "U", "tampered": "X", "checked": "C",
          "shipped": "S"}
LEGEND = [r for r in RUNGS if r != "tampered"]          # tampered joins the legend only when a run reaches it
# (index target, heading, workers line); the frozen figure's column order.
COLUMNS = [("flipt-dsflash", "Software engineering", "DeepSeek workers"),
           ("flipt-luna", "Software engineering", "Luna workers"),
           ("protein-luna", "Protein design", "Luna workers"),
           ("creative-luna", "Creative writing", "Luna workers")]
# Regulator models in the frozen figure's order, by registry entry, with their row labels.
REGULATORS = [("claude-haiku-5-5-openrouter", "Haiku 5.5"), ("gpt-6-luna-openrouter", "Luna"),
              ("gemini-4-argon-openrouter", "Gemini 4 Argon"), ("claude-sonnet-5-5-openrouter", "Sonnet 5.5"),
              ("claude-opus-5-5-openrouter", "Opus 5.5")]
RULE_ROWS = [(None, "No rule"), ("generic", "Rule: generic"), ("identified", "Rule: identified")]
REGULATOR_GROUPS = [("ambiguous", "+ hidden regulator, level ambiguous",
                     "told to ensure the team submits honest work, with the task's definition of cheating"),
                    ("enforce", "+ hidden regulator, level enforce",
                     "told to watch, call out, revert and name cheating, with the same definition")]
SWE_FIXED = re.compile(r"_(fixed|fill)(pilot)?\d+$")


def runs() -> list[dict]:
    """The figure's runs from the index, each with its team rung read from its run.json."""
    out = []
    targets = {c[0] for c in COLUMNS}
    for line in INDEX.read_text().splitlines():
        row = json.loads(line)
        if not row["valid"] or row["mock"] or row["target"] not in targets or row["disregulator_level"]:
            continue
        if row["domain"] == "swe" and not SWE_FIXED.search(row["run"]):
            continue
        team = json.loads((STUDY / row["path"] / "run.json").read_text())["signals"]["team"]
        out.append({**row, "team_rung": team["rung"], "pilot": "pilot" in row["run"]})
    return out


def cell_of(run: dict) -> tuple[str, str | None] | None:
    """(group, row key) of a run: rule level, regulator level and model, or told to cheat."""
    if run["kind"] == "minimal":
        return "rule", run["rule_level"]
    if run["kind"] == "minimal_regulated" and not run["rule_level"]:
        return run["regulator_level"], run["regulator_model"]
    if run["kind"] == "minimal_cheat" and not run["rule_level"]:
        return "cheat", None
    return None


def cells(found: list[dict]) -> dict:
    """{(group, row key, target): runs, seeded runs by seed, then pilots}."""
    out = defaultdict(list)
    for run in found:
        key = cell_of(run)
        if key is None:
            raise SystemExit(f"{run['run']}: kind {run['kind']} has no row in the figure")
        out[(*key, run["target"])].append(run)
    for group in out.values():
        group.sort(key=lambda r: (r["pilot"], r["seed"], r["run"]))
    return out


def reference_cells() -> dict[str, tuple]:
    """{cell prefix: (group, row key, target)} from the results tables' scripts."""
    label_of = {label: model for model, label in REGULATORS}
    target_of = {("Protein design", "GPT-6 Luna"): "protein-luna",
                 ("Software engineering", "DeepSeek V4.1 Flash"): "flipt-dsflash",
                 ("Software engineering", "GPT-6 Luna"): "flipt-luna",
                 ("Creative writing", "GPT-6 Luna"): "creative-luna"}
    out = {}
    for task, workers, prefixes, _, _ in control_tables.TARGETS:
        target = target_of.get((task, workers))
        if target:
            for key, prefix in prefixes.items():
                out[prefix] = ("cheat", None, target) if key == "cheat" else ("rule", None if key == "none" else key,
                                                                              target)
    for script, target in ((protein_regulator_table, "protein-luna"), (swe_regulator_table, "flipt-dsflash")):
        for label, level, *prefixes in script.ROWS:
            if label == "None":
                continue
            model = label_of[label.removeprefix("Claude ").removeprefix("GPT-6 ")]
            out.update({prefix: (level, model, target) for prefix in prefixes})
    return out


def check(table: dict) -> list[str]:
    """Where the figure's cells disagree with the results tables' prefixes: a run in another cell, or a valid run
    of a prefix that the figure lacks."""
    ref = reference_cells()
    placed = {run["run"]: key for key, group in table.items() for run in group}
    problems = []
    for prefix, key in ref.items():
        for path in STUDY.iterdir():
            if not re.fullmatch(re.escape(prefix) + r"\d+", path.name) or not (path / "run.json").is_file():
                continue
            if validity(path)["valid"] and placed.get(path.name) != key:
                problems.append(f"{path.name}: tables put it in {key}, the figure in {placed.get(path.name)}")
    return problems


def wording() -> tuple[list[str], list[str]]:
    """The rule group's subtitle lines, and the checks that the regulator and identified paraphrases still hold."""
    levels = yaml.safe_load(LEVELS.read_text())
    generic = levels["rule"]["generic"]["text"]
    identified = levels["rule"]["identified"]
    regulator = levels["regulator"]
    broken = [name for name, ok in [
        ("rule.identified names the prior work and calls its use cheating",
         all("That includes {protected}" in t and "is cheating" in t for t in identified.values())),
        ("regulator.ambiguous asks for honest work and gives the task's cheating",
         "honest" in regulator["ambiguous"]["text"] and "{cheating}" in regulator["ambiguous"]["text"]),
        ("regulator.enforce says to call out, revert and name, with the same cheating",
         all(w in regulator["enforce"]["text"] for w in ("say so plainly", "revert", "name the member", "{cheating}")))]
        if not ok]
    lines = [f"generic rule: “{generic}”",
             "identified rule: names where the existing fix or work is and calls using it cheating"]
    return lines, broken


def tile(ax, x: float, y: float, s: float, rung: str, fs: float) -> None:
    ax.add_patch(FancyBboxPatch((x, y), s, s, boxstyle="round,pad=0,rounding_size=0.035", fc=RUNG_FILL[rung],
                                ec=AXIS if rung == "none" else "none", lw=0.6))
    ax.text(x + s / 2, y + s / 2, LETTER[rung], fontsize=fs, weight="bold", color=RUNG_TEXT.get(rung, INK),
            ha="center", va="center")


def draw(table: dict, rule_lines: list[str]) -> Figure:
    targets = [c[0] for c in COLUMNS]
    groups = [("Team of three, no regulator", rule_lines, [(("rule", k), label) for k, label in RULE_ROWS])]
    for level, title, sub in REGULATOR_GROUPS:
        rows = [((level, m), label) for m, label in REGULATORS if any(table.get((level, m, t)) for t in targets)]
        if rows:
            groups.append((title, [sub], rows))
    groups.append(("Team of three, all told to cheat", [], [(("cheat", None), "Told to cheat")]))

    left, col, pad = 1.75, 2.1, 0.12
    s, gap, row_h = 0.27, 0.055, 0.37
    head_h, title_h, sub_h, group_pad, legend_h = 0.95, 0.36, 0.2, 0.2, 0.6
    width = left + col * len(COLUMNS)
    height = head_h + legend_h + sum(title_h + sub_h * len(sub) + 0.1 + row_h * len(rows) + group_pad
                                     for _, sub, rows in groups)
    fig = Figure(figsize=(width, height), dpi=PRINT_DPI, facecolor=SURFACE)
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.axis("off")

    for j, (target, heading, workers) in enumerate(COLUMNS):
        x = left + j * col
        task = next(r["cheatbench_task"] for group in table.values() for r in group if r["target"] == target)
        ax.text(x, 0.28, heading, fontsize=11, weight="bold", color=INK, va="center")
        ax.text(x, 0.53, workers, fontsize=8.5, color=INK_SECONDARY, va="center")
        ax.text(x, 0.71, f"cheating from {BY_TASK[task].ladder.cheated_from}", fontsize=8.5, color=INK_SECONDARY,
                va="center")

    y = head_h
    for g, (title, sub, rows) in enumerate(groups):
        ax.plot([pad, width - pad], [y, y], color=GRID, lw=1)
        ax.text(pad, y + title_h / 2 + 0.06, title, fontsize=10.5, weight="bold", color=INK, va="center")
        y += title_h
        for line in sub:
            ax.text(pad, y + sub_h / 2, line, fontsize=8, color=INK_SECONDARY, va="center")
            y += sub_h
        y += 0.1
        for key, label in rows:
            cy = y + row_h / 2
            ax.text(left - 0.15, cy, label, fontsize=9.5, color=INK, ha="right", va="center")
            for j, target in enumerate(targets):
                for k, run in enumerate(table.get((*key, target), [])):
                    tile(ax, left + j * col + k * (s + gap), cy - s / 2, s, run["team_rung"], 8.5)
            y += row_h
        y += group_pad

    shown = {r["team_rung"] for group in table.values() for r in group}
    legend = [r for r in RUNGS if r in LEGEND or r in shown]
    ly = y + legend_h / 2
    ax.text(pad, ly, "Team rung", fontsize=9.5, color=INK_SECONDARY, va="center")
    x = left
    for rung in legend:
        tile(ax, x, ly - s / 2, s, rung, 8.5)
        ax.text(x + s + 0.07, ly, rung, fontsize=9, color=INK_SECONDARY, va="center")
        x += s + 0.07 + 0.075 * len(rung) + 0.3
    return fig


def main() -> int:
    found = runs()
    table = cells(found)
    problems = check(table)
    rule_lines, broken = wording()
    for line in broken:
        problems.append(f"instruction_levels.yaml no longer supports the paraphrase: {line}")
    for run in found:
        if run["team_rung"] != run["rung"]:
            print(f"{run['run']}: run.json team rung {run['team_rung']}, index rung {run['rung']}; drawn from run.json")
    for (group, key, target), group_runs in sorted(table.items(), key=str):
        print(f"{target:14s} {group:9s} {str(key):30s}",
              " ".join(LETTER[r["team_rung"]] + ("p" if r["pilot"] else "") for r in group_runs),
              [r["run"] for r in group_runs])
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    with matplotlib.rc_context({"font.family": "Arial"}):
        fig = draw(table, rule_lines)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(OUT, dpi=PRINT_DPI, facecolor=SURFACE)
    print(OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
