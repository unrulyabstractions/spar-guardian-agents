"""The paper's sources that come from code: the LaTeX the paper inputs from paper/generated/ and the figures it
includes from paper/figures/.

    uv run python -m scripts.paper.paper_sources          # write paper/generated/, copy every figure with a source
    uv run python -m scripts.paper.paper_sources --check  # write nothing; exit 1 when paper/ disagrees with a source

paper/generated/ holds what the code defines: params.tex (the board's budgets as macros, from the default preset),
scales.tex (from src/configuration/scales.py), regulator_levels.tex and disregulator_levels.tex (from
src/configuration/instruction_levels.yaml). Nobody edits these files by hand.

FIGURES names each PNG in paper/figures/ and where it comes from. A `generated` figure is copied from tmp/ after its
generator runs (`make figures`, `make tables`). A `tmp` figure is copied from tmp/ too, but its generator is not in
the repository: those scripts lived in a session scratchpad that macOS has since cleared, and their last versions
survive only in that session's transcript. A `frozen` figure was extracted from the team's Google Docs PDF
(paper.pdf, 2026-11-08) and has no source here; it changes only when the team sends a new one.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from scripts.paper.tables import hours
from src.configuration.presets import PRESETS
from src.configuration.scales import SCALES
from src.configuration.settings import ROOT

PAPER = ROOT / "paper"
GENERATED = PAPER / "generated"
FIGURE_DIR = PAPER / "figures"
LEVELS_YAML = ROOT / "src" / "configuration" / "instruction_levels.yaml"
LOST = "generator lost with session d3edd1f3's scratchpad"


@dataclass(frozen=True)
class Figure:
    kind: str          # generated | tmp | frozen
    source: str        # a path under the repository root, or a note for a frozen figure
    note: str = ""


FIGURES: dict[str, Figure] = {
    # Main text. Figures 1 to 5 come from the team, not from this repository's code.
    "fig1_scripted_teammates.png": Figure("frozen", "paper.pdf page 2", "Lucy's Qwen3-32B exploratory runs"),
    "fig2_roles_access.png": Figure("frozen", "paper.pdf page 4", "the two studies' roles and access"),
    "fig3_jigsaw.png": Figure("frozen", "paper.pdf page 5", "the jigsaw exploit per task"),
    "fig4_multidomain.png": Figure("frozen", "paper.pdf page 7", "multi-domain results"),
    "fig5_swe_deep_dive.png": Figure("frozen", "paper.pdf page 8", "SWE deep dive results"),
    # Appendix A.
    "topology_compare.png": Figure("generated", "tmp/figures/method/design/topology_compare.png",
                                   "make figures (scripts/paper/figures.py)"),
    "round_structure.png": Figure("generated", "tmp/figures/method/design/round_structure.png",
                                  "make figures (scripts/paper/figures.py)"),
    "access.png": Figure("generated", "tmp/figures/method/design/access.png",
                         "make figures (scripts/paper/figures.py)"),
    "table_collaboration_by_domain.png": Figure("generated", "tmp/tables/method/table_collaboration_by_domain.png",
                                                "make tables (scripts/paper/tables.py)"),
    "table_rungs_by_domain.png": Figure("generated", "tmp/tables/method/table_rungs_by_domain.png",
                                        "make tables (scripts/paper/tables.py)"),
    "table_rungs.png": Figure("generated", "tmp/tables/method/table_rungs.png",
                              "make tables (scripts/paper/tables.py)"),
    # Appendices B and C: the results tables.
    "positive_control.png": Figure("tmp", "tmp/tables/results/positive_control.png", f"control_tables.py, {LOST}"),
    "negative_control.png": Figure("tmp", "tmp/tables/results/negative_control.png", f"control_tables.py, {LOST}"),
    "table_protein_regulators.png": Figure("tmp", "tmp/tables/results/table_protein_regulators.png",
                                           f"regulator_table.py, {LOST}"),
    "table_swe_regulators.png": Figure("tmp", "tmp/tables/results/table_swe_regulators.png",
                                       f"swe_regulator_table.py, {LOST}"),
    "table_swe_disregulators.png": Figure("tmp", "tmp/tables/results/table_swe_disregulators.png",
                                          f"disregulator_table.py, {LOST}"),
    "table_creative_disregulators.png": Figure("tmp", "tmp/tables/results/table_creative_disregulators.png",
                                               f"disregulator_table.py, {LOST}"),
    "solo_vs_solo_told.png": Figure("tmp", "tmp/tables/results/solo_vs_solo_told.png", f"baseline_tables.py, {LOST}"),
    "swarm_vs_swarm_told.png": Figure("tmp", "tmp/tables/results/swarm_vs_swarm_told.png",
                                      f"baseline_tables.py, {LOST}"),
}

HEADER = "% Written by scripts/paper/paper_sources.py from {source}. Do not edit; run `make paper-sources`.\n"
TEX_SPECIALS = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
                "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
PLACEHOLDER_MEANING = {
    "prior_work": "the task's planted work and where it sits",
    "cheating": "what counts as cheating on this task",
    "deliverable": "what the team hands in",
    "goal": "the team's objective on the task",
}


def escape(text: str) -> str:
    return "".join(TEX_SPECIALS.get(ch, ch) for ch in text)


def chip(name: str) -> str:
    return r"\placeholder{" + escape("{" + name + "}") + "}"


def with_chips(text: str) -> str:
    """Escape a level's text and set each {placeholder} as a chip."""
    out, last = [], 0
    for m in PLACEHOLDER.finditer(text):
        out.append(escape(text[last:m.start()]))
        out.append(chip(m.group(1)))
        last = m.end()
    out.append(escape(text[last:]))
    return "".join(out).strip()


def params_tex() -> str:
    c = PRESETS["default"]
    macros = {
        "MaxMessageChars": c.max_message_chars,
        "PostsPerTurn": c.posts_per_turn,
        "ReadsPerTurn": c.reads_per_turn,
        "ReadPage": c.read_page,
        "FreeBoardActions": c.free_board_actions,
        "FreeBoardActionsRegulator": c.free_board_actions_regulator,
        "TurnBudget": c.turn_budget,
    }
    lines = [HEADER.format(source="src/configuration/presets.py PRESETS['default']")]
    lines += [rf"\newcommand{{\{k}}}{{{v}}}" for k, v in macros.items()]
    return "\n".join(lines) + "\n"


def scales_tex() -> str:
    rows = [rf"{escape(name.replace('_', ' '))} & {s.n_agents} & {hours(s.time_limit_s)} \\" for name, s in
            SCALES.items()]
    return (HEADER.format(source="src/configuration/scales.py SCALES")
            + "\\begin{tabular}{lrr}\n\\toprule\nScale & Agents & Time limit \\\\\n\\midrule\n"
            + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n")


def levels_tex(ladder: str) -> str:
    spec = yaml.safe_load(LEVELS_YAML.read_text())[ladder]
    rows, used = [], []
    for level in spec["order"]:
        text = spec[level].get("text")
        if text is None:
            continue
        for name in PLACEHOLDER.findall(text):
            if name not in used:
                used.append(name)
        rows.append(rf"\textbf{{{escape(level)}}} & {with_chips(text)} \\")
    legend = r" \quad ".join(f"{chip(n)} {escape(PLACEHOLDER_MEANING.get(n, n))}" for n in used)
    return (HEADER.format(source=f"src/configuration/instruction_levels.yaml {ladder}")
            + "\\begin{tabularx}{\\linewidth}{@{}l X@{}}\n\\toprule\nLevel & Text \\\\\n\\midrule\n"
            + "\n\\addlinespace\n".join(rows) + "\n\\bottomrule\n\\end{tabularx}\n\n"
            + "\\smallskip\n{\\footnotesize " + legend + "}\n")


def generated_files() -> dict[Path, str]:
    return {
        GENERATED / "params.tex": params_tex(),
        GENERATED / "scales.tex": scales_tex(),
        GENERATED / "regulator_levels.tex": levels_tex("regulator"),
        GENERATED / "disregulator_levels.tex": levels_tex("disregulator"),
    }


def check() -> list[str]:
    """What in paper/ disagrees with its source. An absent tmp/ source is reported apart, as a warning."""
    problems = []
    for path, text in generated_files().items():
        if not path.exists() or path.read_text() != text:
            problems.append(f"STALE   {path.relative_to(ROOT)} (run make paper-sources)")
    for name, fig in FIGURES.items():
        dest = FIGURE_DIR / name
        if not dest.exists():
            problems.append(f"MISSING {dest.relative_to(ROOT)} ({fig.kind}: {fig.source})")
            continue
        if fig.kind == "frozen":
            continue
        src = ROOT / fig.source
        if src.exists() and src.read_bytes() != dest.read_bytes():
            problems.append(f"STALE   {dest.relative_to(ROOT)} differs from {fig.source}")
    return problems


def absent_sources() -> list[str]:
    return [f"ABSENT  {f.source} ({f.note}); paper/figures/{n} kept as is" for n, f in FIGURES.items()
            if f.kind != "frozen" and not (ROOT / f.source).exists()]


def sync() -> None:
    GENERATED.mkdir(parents=True, exist_ok=True)
    for path, text in generated_files().items():
        if not path.exists() or path.read_text() != text:
            path.write_text(text)
            print(f"wrote  {path.relative_to(ROOT)}")
    for name, fig in FIGURES.items():
        src, dest = ROOT / fig.source, FIGURE_DIR / name
        if fig.kind == "frozen" or not src.exists():
            continue
        if not dest.exists() or src.read_bytes() != dest.read_bytes():
            shutil.copyfile(src, dest)
            print(f"copied {fig.source} -> {dest.relative_to(ROOT)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 when paper/ is stale")
    args = parser.parse_args(argv)
    if not args.check:
        sync()
    for line in absent_sources():
        print(line, file=sys.stderr)
    problems = check()
    for line in problems:
        print(line)
    if not problems:
        print("paper/ agrees with its sources")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
