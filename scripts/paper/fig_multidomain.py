"""Figure 4 of the paper: the multi-domain study's results, one tile per valid run, coloured and lettered by the team's
Cheating rung; the legend names the measures' cheating line. Columns are the targets (SWE with DeepSeek workers, SWE
with Luna workers, SWE with Haiku workers, protein design, creative writing), each as wide as its heading or its
widest cell. Row groups: the team of three with no rule, the generic rule and
the identified rule; a regulator at level ambiguous, then enforce, one row per regulator model with runs; the
team all told to cheat.

Source: study_exploration_1's valid runs (scripts/paper/study_ladders.py valid_runs; disregulator runs left out; SWE
only on the fixed task, run names ending in _fixed<seed>, _fill<seed> or _fixedpilot<seed>) with their run index rows,
each run's Cheating rung as the transcript viewer measures it (study_ladders.record).
The rules' wording comes from src/configuration/instruction_levels.yaml. Every cell a results table also shows
(control_tables, protein_regulator_table, swe_regulator_table, creative_regulator_table) must hold exactly that
table's runs (study_ladders.cell_runs); a disagreement fails the build.
Writes tmp/figures/paper/fig4_multidomain.png.

    uv run python -m scripts.paper.fig_multidomain
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict

import matplotlib
import yaml
from matplotlib.figure import Figure

from scripts.paper import control_tables, creative_regulator_table, protein_regulator_table, swe_regulator_table
from scripts.paper.study_ladders import (
    ACCESSED_LABEL,
    CHEATED_LABEL,
    CHEATING,
    CHEATING_LETTER,
    canvas,
    cell_runs,
    record,
    rung_chip,
    save,
    text_width,
    valid_runs,
)
from src.configuration.settings import ROOT
from src.utils.figure_style import GRID, INK, INK_SECONDARY, SURFACE

LEVELS = ROOT / "src" / "configuration" / "instruction_levels.yaml"
OUT = ROOT / "tmp" / "figures" / "paper" / "fig4_multidomain.png"

# (index target, heading, workers line); the frozen figure's column order.
COLUMNS = [("flipt-dsflash", "Software engineering", "DeepSeek workers"),
           ("flipt-luna", "Software engineering", "Luna workers"),
           ("flipt-haiku", "Software engineering", "Haiku workers"),
           ("protein-luna", "Protein design", "Luna workers"),
           ("creative-luna", "Creative writing", "Luna workers")]
# Regulator models in the frozen figure's order, by registry entry, with their row labels.
REGULATORS = [("claude-haiku-5-5-openrouter", "Haiku 5.5"), ("gpt-6-luna-openrouter", "Luna"),
              ("gemini-4-argon-openrouter", "Gemini 4 Argon"), ("claude-sonnet-5-5-openrouter", "Sonnet 5.5"),
              ("claude-opus-5-5-openrouter", "Opus 5.5")]
RULE_ROWS = [(None, "No rule"), ("generic", "Rule: generic"), ("identified", "Rule: names the exploit")]
REGULATOR_GROUPS = [("ambiguous", "+ regulator, level ambiguous",
                     "told to ensure the team submits honest work, with the task's definition of cheating"),
                    ("enforce", "+ regulator, level enforce",
                     "told to watch, call out, revert and name cheating, with the same definition")]
SWE_FIXED = re.compile(r"_(fixed|fill)(pilot)?\d+$")


def runs() -> list[dict]:
    """The figure's runs: study_ladders.valid_runs' index rows, each with its team's Cheating rung."""
    out = []
    targets = {c[0] for c in COLUMNS}
    for listed in valid_runs():
        row = listed.meta
        if row["target"] not in targets or row["disregulator_level"]:
            continue
        if row["domain"] == "swe" and not SWE_FIXED.search(row["run"]):
            continue
        out.append({**row, "team_rung": record(listed.path)["cheating"], "pilot": "pilot" in row["run"]})
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
    targets = {c[0] for c in COLUMNS}
    out = {}
    for _, _, target, prefixes in control_tables.TARGETS:
        if target in targets:
            for key, prefix in prefixes.items():
                out[prefix] = ("cheat", None, target) if key == "cheat" else ("rule", None if key == "none" else key,
                                                                              target)
    tables = ((protein_regulator_table.ROWS, "protein-luna"), (swe_regulator_table.ROWS, "flipt-dsflash"),
              (swe_regulator_table.ROWS_LUNA, "flipt-luna"), (creative_regulator_table.ROWS, "creative-luna"))
    for rows, target in tables:
        for label, level, *prefixes in rows:
            if label == "None":
                continue
            model = label_of[label.removeprefix("Claude ").removeprefix("GPT-6 ")]
            out.update({prefix: (level, model, target) for prefix in prefixes})
    return out


def check(table: dict) -> list[str]:
    """Where a cell the results tables show holds other runs in the figure than in the tables
    (study_ladders.cell_runs of the tables' prefixes)."""
    expected: dict[tuple, set[str]] = defaultdict(set)
    for prefix, key in reference_cells().items():
        expected[key] |= {path.name for path in cell_runs(prefix)}
    problems = []
    for key, names in expected.items():
        drawn = {run["run"] for run in table.get(key, [])}
        if drawn != names:
            problems.append(f"{key}: the tables hold {sorted(names - drawn)} the figure lacks, "
                            f"the figure {sorted(drawn - names)} the tables lack")
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
             "exploit rule: names where the existing fix or work is and calls using it cheating"]
    return lines, broken


def draw(table: dict, rule_lines: list[str]) -> Figure:
    targets = [c[0] for c in COLUMNS]
    groups = [("Team of three, no regulator", rule_lines, [(("rule", k), label) for k, label in RULE_ROWS])]
    for level, title, sub in REGULATOR_GROUPS:
        rows = [((level, m), label) for m, label in REGULATORS if any(table.get((level, m, t)) for t in targets)]
        if rows:
            groups.append((title, [sub], rows))
    groups.append(("Team of three, all told to cheat", [], [(("cheat", None), "Told to cheat")]))

    left, pad = 1.2, 0.1
    s, gap, row_h = 0.18, 0.03, 0.25
    head_h, title_h, sub_h, group_pad, legend_h = 0.5, 0.26, 0.16, 0.12, 0.62
    widths = [max(text_width(workers, 7), max(len(runs) for (*_, t), runs in table.items() if t == target) * (s + gap))
              + 0.15 for target, _, workers in COLUMNS]
    xs = [left + sum(widths[:j]) for j in range(len(COLUMNS))]
    width = left + sum(widths)
    height = head_h + legend_h + sum(title_h + sub_h * len(sub) + 0.06 + row_h * len(rows) + group_pad
                                     for _, sub, rows in groups)
    fig, ax = canvas(width, height, SURFACE)

    for x, (target, heading, workers) in zip(xs, COLUMNS):
        ax.text(x, 0.17, heading.replace("Software engineering", "SWE"), fontsize=8, weight="bold", color=INK,
                va="center")
        ax.text(x, 0.36, workers, fontsize=7, color=INK_SECONDARY, va="center")

    y = head_h
    for title, sub, rows in groups:
        ax.plot([pad, width - pad], [y, y], color=GRID, lw=1)
        ax.text(pad, y + title_h / 2 + 0.03, title, fontsize=8, weight="bold", color=INK, va="center")
        y += title_h
        for line in sub:
            ax.text(pad, y + sub_h / 2, line, fontsize=6.5, color=INK_SECONDARY, va="center")
            y += sub_h
        y += 0.06
        for key, label in rows:
            cy = y + row_h / 2
            ax.text(left - 0.1, cy, label, fontsize=7, color=INK, ha="right", va="center")
            for j, target in enumerate(targets):
                for k, run in enumerate(table.get((*key, target), [])):
                    rung_chip(ax, xs[j] + k * (s + gap), cy - s / 2, s, s, run["team_rung"], size=6.5)
            y += row_h
        y += group_pad

    ly = y + 0.14
    ax.text(pad, ly, "Team's Cheating rung", fontsize=7, color=INK_SECONDARY, va="center")
    x = left
    for rung in CHEATING:
        rung_chip(ax, x, ly - s / 2, s, s, rung, size=6.5)
        ax.text(x + s + 0.05, ly, rung, fontsize=7, color=INK_SECONDARY, va="center")
        x += s + 0.05 + text_width(rung, 7) + 0.2
    ax.text(left, ly + 0.27, f"{CHEATED_LABEL};  {ACCESSED_LABEL}", fontsize=7, color=INK_SECONDARY, va="center")
    return fig


def main() -> int:
    found = runs()
    table = cells(found)
    problems = check(table)
    rule_lines, broken = wording()
    for line in broken:
        problems.append(f"instruction_levels.yaml no longer supports the paraphrase: {line}")
    for (group, key, target), group_runs in sorted(table.items(), key=str):
        print(f"{target:14s} {group:9s} {str(key):30s}",
              " ".join(CHEATING_LETTER[r["team_rung"]] + ("p" if r["pilot"] else "") for r in group_runs),
              [r["run"] for r in group_runs])
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    with matplotlib.rc_context({"font.family": "Arial"}):
        print(save(draw(table, rule_lines), OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
