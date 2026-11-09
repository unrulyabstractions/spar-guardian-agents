"""The paper's sources that come from code: the LaTeX the paper inputs from paper/generated/ and the figures it
includes from paper/figures/.

    uv run python -m scripts.paper.paper_sources          # write paper/generated/, copy every figure with a source
    uv run python -m scripts.paper.paper_sources --check  # write nothing; exit 1 when paper/ disagrees with a source

paper/generated/ holds what the code defines: params.tex (the board's budgets and the turn budget from the default
preset, the judge and its reasoning effort from settings.JUDGE_MODEL and src/judging/judge_models.yaml), scales.tex
(from src/configuration/scales.py), regulator_suffix.tex (the regulator's common text from
src/configuration/prompts.yaml and its three report variants), regulator_levels.tex and disregulator_levels.tex (from
src/configuration/instruction_levels.yaml). Nobody edits these files by hand.

FIGURES names each PNG in paper/figures/, the PNG in tmp/ it is copied from and the make target whose script draws
it (`make paper-figures`, `make figures`, `make tables`, `make results-tables`). Every figure has a script here.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

from scripts.paper.tables import hours
from src.configuration import settings
from src.configuration.presets import PRESETS
from src.configuration.scales import SCALES
from src.configuration.settings import ROOT
from src.judging.cheatbench_judge import judge_entry

PAPER = ROOT / "paper"
GENERATED = PAPER / "generated"
FIGURE_DIR = PAPER / "figures"
LEVELS_YAML = ROOT / "src" / "configuration" / "instruction_levels.yaml"
PROMPTS_YAML = ROOT / "src" / "configuration" / "prompts.yaml"
STUDY_INDEX = ROOT / "runs" / "study_exploration_1" / "INDEX.jsonl"
STAGE2 = ROOT / "submodules" / "lucy" / "runs" / "s2_swe" / "analysis" / "summary.json"
# The names the paper gives the models of the run index. A model outside this map stops the build.
MODEL_NAMES = {"claude-haiku-5-5": "Claude Haiku 5.5", "claude-sonnet-5-5": "Claude Sonnet 5.5",
               "claude-opus-5-5": "Claude Opus 5.5", "gpt-6-luna": "GPT-6 Luna", "gemini-4-argon": "Gemini 4 Argon",
               "deepseek-v4-1-flash": "DeepSeek V4.1 Flash"}
STAGE2_CELLS = [("3W", "Three workers, no regulator"), ("amb·deepseek", "DeepSeek regulator, ambiguous"),
                ("amb·haiku", "Haiku regulator, ambiguous")]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
RESULTS = "make results-tables (scripts/paper/{})"
PAPER_FIGURES = "make paper-figures (scripts/paper/{})"
# The judge entries the paper can name. A judge outside this map stops the build, so a new judge reaches the prose.
JUDGE_NAMES = {"gpt-6-luna": "OpenAI GPT-6 Luna", "gpt-6-sol": "OpenAI GPT-6 Sol"}
# The regulator's report variants in the paper's prompt box, as levels of instruction_levels.yaml.
SUFFIX_VARIANTS = [("[Level 1] Ambiguous boundary", "ambiguous"), ("[Level 2] Explicit boundary", "explicit"),
                   ("[Level 3] High pressure", "pressure")]


@dataclass(frozen=True)
class Figure:
    source: str        # the PNG under tmp/ the script writes, relative to the repository root
    note: str          # the make target and the script that draws it


FIGURES: dict[str, Figure] = {
    # Main text: Figures 1 to 5.
    "fig1_scripted_teammates.png": Figure("tmp/figures/paper/fig1_scripted_teammates.png",
                                          PAPER_FIGURES.format("fig_scripted_teammates.py")),
    "fig2_roles_access.png": Figure("tmp/figures/paper/fig2_roles_access.png",
                                    PAPER_FIGURES.format("fig_roles_access.py")),
    "fig3_jigsaw.png": Figure("tmp/figures/paper/fig3_jigsaw.png", PAPER_FIGURES.format("fig_jigsaw.py")),
    "fig4_multidomain.png": Figure("tmp/figures/paper/fig4_multidomain.png",
                                   PAPER_FIGURES.format("fig_multidomain.py")),
    "fig5_swe_deep_dive.png": Figure("tmp/figures/paper/fig5_swe_deep_dive.png",
                                     PAPER_FIGURES.format("fig_deep_dive.py")),
    # Appendix A.
    "topology_compare.png": Figure("tmp/figures/method/design/topology_compare.png",
                                   "make figures (scripts/paper/figures.py)"),
    "round_structure.png": Figure("tmp/figures/method/design/round_structure.png",
                                  "make figures (scripts/paper/figures.py)"),
    "access.png": Figure("tmp/figures/method/design/access.png",
                         "make figures (scripts/paper/figures.py)"),
    "table_collaboration_by_domain.png": Figure("tmp/tables/method/table_collaboration_by_domain.png",
                                                "make tables (scripts/paper/tables.py)"),
    "table_rungs_by_domain.png": Figure("tmp/tables/method/table_rungs_by_domain.png",
                                        "make tables (scripts/paper/tables.py)"),
    "table_rungs.png": Figure("tmp/tables/method/table_rungs.png",
                              "make tables (scripts/paper/tables.py)"),
    # Appendices B and C: the results tables.
    "positive_control.png": Figure("tmp/tables/results/positive_control.png",
                                   RESULTS.format("control_tables.py")),
    "negative_control.png": Figure("tmp/tables/results/negative_control.png",
                                   RESULTS.format("control_tables.py")),
    "table_protein_regulators.png": Figure("tmp/tables/results/table_protein_regulators.png",
                                           RESULTS.format("protein_regulator_table.py")),
    "table_swe_regulators.png": Figure("tmp/tables/results/table_swe_regulators.png",
                                       RESULTS.format("swe_regulator_table.py")),
    "table_swe_disregulators.png": Figure("tmp/tables/results/table_swe_disregulators.png",
                                          RESULTS.format("disregulator_tables.py")),
    "table_creative_disregulators.png": Figure("tmp/tables/results/table_creative_disregulators.png",
                                               RESULTS.format("disregulator_tables.py")),
    "solo_vs_solo_told.png": Figure("tmp/tables/results/solo_vs_solo_told.png",
                                    RESULTS.format("solo_controls_table.py")),
    "swarm_vs_swarm_told.png": Figure("tmp/tables/results/swarm_vs_swarm_told.png",
                                      RESULTS.format("baseline_tables.py")),
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
    if settings.JUDGE_MODEL not in JUDGE_NAMES:
        raise SystemExit(f"judge {settings.JUDGE_MODEL!r} has no name in JUDGE_NAMES: add it, then check the prose")
    judge = {"JudgeName": JUDGE_NAMES[settings.JUDGE_MODEL],
             "JudgeEffort": judge_entry(settings.JUDGE_MODEL)["generation_config"].get("reasoning_effort") or "default"}
    lines = [HEADER.format(source="PRESETS['default'], settings.JUDGE_MODEL and src/judging/judge_models.yaml")]
    lines += [rf"\newcommand{{\{k}}}{{{v}}}" for k, v in macros.items()]
    lines += [rf"\newcommand{{\{k}}}{{{escape(v)}}}" for k, v in judge.items()]
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


def regulator_suffix_tex() -> str:
    """The prompt box: the common text every regulator gets after its level, then the report's three variants."""
    roles = yaml.safe_load(PROMPTS_YAML.read_text())["roles"]
    levels = yaml.safe_load(LEVELS_YAML.read_text())["regulator"]
    common = " ".join([roles["regulator_common"].strip(), roles["regulator_no_disclosure"].strip()])
    parts = [r"\textbf{[COMMON]}\\" + "\n" + escape(common)]
    for label, level in SUFFIX_VARIANTS:
        parts.append(rf"\textbf{{{escape(label)}}}\\" + "\n" + with_chips(levels[level]["text"]) + " + [COMMON]")
    return (HEADER.format(source="src/configuration/prompts.yaml roles and instruction_levels.yaml regulator")
            + "\n\n\\medskip\n".join(parts) + "\n")


def model_name(key: str) -> str:
    base = key.removesuffix("-openrouter")
    if base not in MODEL_NAMES:
        raise SystemExit(f"model {key!r} has no name in MODEL_NAMES: add it, then check the prose")
    return MODEL_NAMES[base]


def day(iso: str) -> str:
    return f"{int(iso[8:10])} {MONTHS[int(iso[5:7]) - 1]}"


def study_tex() -> str:
    """The multi-domain study as its run index holds it: valid runs, their dates, and the regulators' models by use."""
    rows = [json.loads(line) for line in STUDY_INDEX.read_text().splitlines() if line.strip()]
    valid = [r for r in rows if r["valid"] and not r["mock"]]
    dates = []
    for r in valid:
        log = json.loads((STUDY_INDEX.parent / r["path"] / "run.json").read_text()).get("eval_log") or ""
        m = re.search(r"(\d{4}-\d{2}-\d{2})T", log)
        if m:
            dates.append(m.group(1))
    first, last = min(dates), max(dates)
    span = day(first) if first == last else f"{day(first).split()[0]}--{day(last)}" if first[:7] == last[:7] \
        else f"{day(first)} -- {day(last)}"
    used = Counter(r["regulator_model"] for r in valid if r["regulator_model"])
    items = "\n".join(rf"  \item {model_name(k)}" for k, _ in sorted(used.items(), key=lambda kv: (-kv[1], kv[0])))
    return (HEADER.format(source="runs/study_exploration_1/INDEX.jsonl and each valid run's run.json")
            + rf"\newcommand{{\StudyRuns}}{{{len(valid)}}}" + "\n"
            + rf"\newcommand{{\StudyDates}}{{{span}}}" + "\n"
            + "\\newcommand{\\RegulatorModelList}{%\n\\begin{itemize}\n" + items + "\n\\end{itemize}}\n")


def stage2_tex() -> str:
    """Lucy's deep dive, Stage 2: per cell, workers judged cheated, workers who used the fix, episodes, teams that
    passed. Episodes with a recorded failure are left out, as her planned tests do."""
    rows = json.loads(STAGE2.read_text())["rows"]
    bad = {r["episode"] for r in rows if r.get("failure")}
    cell = {}
    for r in rows:
        if r["episode"] in bad:
            continue
        name = r["arm"] if r["arm"] == "3W" else f"{r['arm']}·{r['regulator']}"
        c = cell.setdefault(name, {"cheated": 0, "used": 0, "workers": 0, "episodes": set(), "passed": set()})
        c["episodes"].add(r["episode"])
        if r["team_reward"]:
            c["passed"].add(r["episode"])
        if r["seat"] in ("W1", "W2"):
            c["workers"] += 1
            c["cheated"] += bool(r["cheated"])
            c["used"] += bool(r["used"])
    lines = []
    for key, label in STAGE2_CELLS:
        c = cell[key]
        lines.append(rf"{label} & {c['cheated']}/{c['workers']} & {c['used']}/{c['workers']} & "
                     rf"{len(c['passed'])}/{len(c['episodes'])} \\")
    return (HEADER.format(source="submodules/lucy/runs/s2_swe/analysis/summary.json")
            + "\\begin{tabular}{lrrr}\n\\toprule\nCell & Workers cheated & Workers used the fix & Teams passed \\\\\n"
            + "\\midrule\n" + "\n".join(lines) + "\n\\bottomrule\n\\end{tabular}\n")


def generated_files() -> dict[Path, str]:
    """Every generated file and its text. A file drawn from data absent on this machine (runs/ is gitignored, the
    submodule may be unchecked) is left out, and the check reports the source as absent."""
    files = {
        GENERATED / "params.tex": params_tex(),
        GENERATED / "scales.tex": scales_tex(),
        GENERATED / "regulator_suffix.tex": regulator_suffix_tex(),
        GENERATED / "regulator_levels.tex": levels_tex("regulator"),
        GENERATED / "disregulator_levels.tex": levels_tex("disregulator"),
    }
    if STUDY_INDEX.exists():
        files[GENERATED / "study.tex"] = study_tex()
    if STAGE2.exists():
        files[GENERATED / "stage2.tex"] = stage2_tex()
    return files


def check() -> list[str]:
    """What in paper/ disagrees with its source. An absent tmp/ source is reported apart, as a warning."""
    problems = []
    for path, text in generated_files().items():
        if not path.exists() or path.read_text() != text:
            problems.append(f"STALE   {path.relative_to(ROOT)} (run make paper-sources)")
    for name, fig in FIGURES.items():
        dest = FIGURE_DIR / name
        if not dest.exists():
            problems.append(f"MISSING {dest.relative_to(ROOT)} ({fig.note}: {fig.source})")
            continue
        src = ROOT / fig.source
        if src.exists() and src.read_bytes() != dest.read_bytes():
            problems.append(f"STALE   {dest.relative_to(ROOT)} differs from {fig.source}")
    return problems


def absent_sources() -> list[str]:
    data = [f"ABSENT  {p.relative_to(ROOT)}; its generated file kept as is" for p in (STUDY_INDEX, STAGE2)
            if not p.exists()]
    return data + [f"ABSENT  {f.source} ({f.note}); paper/figures/{n} kept as is" for n, f in FIGURES.items()
                   if not (ROOT / f.source).exists()]


def sync() -> None:
    GENERATED.mkdir(parents=True, exist_ok=True)
    for path, text in generated_files().items():
        if not path.exists() or path.read_text() != text:
            path.write_text(text)
            print(f"wrote  {path.relative_to(ROOT)}")
    for name, fig in FIGURES.items():
        src, dest = ROOT / fig.source, FIGURE_DIR / name
        if not src.exists():
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
