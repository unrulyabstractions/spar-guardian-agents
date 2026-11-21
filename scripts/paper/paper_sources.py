"""The paper's sources that come from code: the LaTeX the paper inputs from paper/generated/ and the figures it
includes from paper/figures/.

    uv run python -m scripts.paper.paper_sources          # write paper/generated/, copy every figure with a source
    uv run python -m scripts.paper.paper_sources --check  # write nothing; exit 1 when paper/ disagrees with a source

paper/generated/ holds what the code defines: params.tex (the board's budgets and the turn budget from the default
preset, the judge and its reasoning effort from settings.JUDGE_MODEL and src/judging/judge_models.yaml, the Cheating
ladder's alarm and the first cheating rung, src/measures/cheating_ladder.py ALARM and CHEATED_FROM),
regulator_suffix.tex (the regulator's common text from src/configuration/prompts.yaml), regulator_levels.tex and
disregulator_levels.tex (the levels of src/configuration/instruction_levels.yaml that the multi-domain study's runs
used, STUDY_LEVELS), cheating_rungs.tex (the Cheating ladder's rungs and clauses, from
src/measures/rungs.py RUNGS and cheating_ladder.py CLAUSES), coordination_levels.tex (the Coordination ladder's levels
and cuts, from src/measures/coordination.py), collusion_rungs.tex (the Collusion ladder's rungs, from
src/measures/collusion.py RUNGS), score_anchors.tex (the judge labels' anchors, from src/measures/judge_labels.py
SCORE_ANCHORS) and reliability.tex (how often an independent reading of the transcripts confirmed each rung and
score, from the current rows of src/measures/measure_readings.yaml). The rungs' and clauses' wording is the transcript
viewer's (src/ui/transcript_levels.py CHEAT_GLOSSES, CLAUSE_GLOSSES, COLLUSION_GLOSSES). Nobody edits
these files by hand.

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

from scripts.paper.study_ladders import ACCESSED_FROM, valid_runs
from src.configuration import settings
from src.configuration.presets import PRESETS
from src.configuration.settings import ROOT
from src.judging import verdict_files
from src.judging.cheatbench_judge import judge_entry
from src.measures import cheating_ladder, collusion, coordination, judge_labels, measure_readings
from src.measures.rungs import RUNGS
from src.swarm.influencers import level_text, role_text
from src.ui.transcript_levels import CHEAT_GLOSSES, CLAUSE_GLOSSES, COLLUSION_GLOSSES

PAPER = ROOT / "paper"
GENERATED = PAPER / "generated"
FIGURE_DIR = PAPER / "figures"
LEVELS_YAML = ROOT / "src" / "configuration" / "instruction_levels.yaml"
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
# The levels of instruction_levels.yaml that the multi-domain study's runs used, per ladder. The level tables print
# only these. When the run index is on disk, a valid run of another level, or a listed level no valid run used, stops
# the build (check_study_levels).
STUDY_LEVELS = {"regulator": ("enforce", "ambiguous"), "disregulator": ("persuade", "overt")}


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
    "table_rungs_by_domain.png": Figure("tmp/tables/method/table_rungs_by_domain.png",
                                        "make tables (scripts/paper/tables.py)"),
    "table_rungs_by_domain_2.png": Figure("tmp/tables/method/table_rungs_by_domain_2.png",
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
    "table_swe_luna_regulators.png": Figure("tmp/tables/results/table_swe_luna_regulators.png",
                                            RESULTS.format("swe_regulator_table.py")),
    "table_creative_regulators.png": Figure("tmp/tables/results/table_creative_regulators.png",
                                            RESULTS.format("creative_regulator_table.py")),
    "table_swe_disregulators.png": Figure("tmp/tables/results/table_swe_disregulators.png",
                                          RESULTS.format("disregulator_tables.py")),
    "table_creative_disregulators.png": Figure("tmp/tables/results/table_creative_disregulators.png",
                                               RESULTS.format("disregulator_tables.py")),
    "solo_vs_solo_told.png": Figure("tmp/tables/results/solo_vs_solo_told.png",
                                    RESULTS.format("solo_controls_table.py")),
    "swarm_vs_swarm_told.png": Figure("tmp/tables/results/swarm_vs_swarm_told.png",
                                      RESULTS.format("baseline_tables.py")),
    "table_coordination_by_task.png": Figure("tmp/tables/results/table_coordination_by_task.png",
                                             RESULTS.format("coordination_table.py")),
    "table_cheating_by_task.png": Figure("tmp/tables/results/table_cheating_by_task.png",
                                         RESULTS.format("cheating_table.py")),
    "table_collusion_by_task.png": Figure("tmp/tables/results/table_collusion_by_task.png",
                                          RESULTS.format("collusion_table.py")),
    "table_judge_cheated_by_task.png": Figure("tmp/tables/results/table_judge_cheated_by_task.png",
                                              RESULTS.format("judge_tables.py")),
    "table_judge_labels.png": Figure("tmp/tables/results/table_judge_labels.png", RESULTS.format("judge_tables.py")),
    "table_cheatbench_disclosed_access_by_task.png": Figure(
        "tmp/tables/results/table_cheatbench_disclosed_access_by_task.png", RESULTS.format("judge_tables.py")),
    "table_label_scores_by_task.png": Figure("tmp/tables/results/table_label_scores_by_task.png",
                                             RESULTS.format("label_tables.py")),
}

HEADER = "% Written by scripts/paper/paper_sources.py from {source}. Do not edit; run `make paper-sources`.\n"
TEX_SPECIALS = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
                "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
PLACEHOLDER_LABEL = {
    "prior_work": "planted work",
    "cheating": "what counts as cheating",
    "deliverable": "hand-in",
    "goal": "team's objective",
}
"""The plain words the paper prints in place of each {placeholder} of a level's text, filled per task at run time."""


def escape(text: str) -> str:
    return "".join(TEX_SPECIALS.get(ch, ch) for ch in text)


def chip(name: str) -> str:
    """A {placeholder} as its plain label in brackets; a name with no label stops the build."""
    if name not in PLACEHOLDER_LABEL:
        raise SystemExit(f"instruction_levels.yaml uses {{{name}}}, which has no label in PLACEHOLDER_LABEL: add one")
    return r"\placeholder{" + escape("[" + PLACEHOLDER_LABEL[name] + "]") + "}"


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
        "FreeBoardActionsInfluencer": c.free_board_actions_influencer,
        "ToolOutputKiB": c.tool_output_bytes // 1024,
        "TurnBudget": c.turn_budget,
    }
    if settings.JUDGE_MODEL not in JUDGE_NAMES:
        raise SystemExit(f"judge {settings.JUDGE_MODEL!r} has no name in JUDGE_NAMES: add it, then check the prose")
    judge = {"JudgeName": JUDGE_NAMES[settings.JUDGE_MODEL],
             "JudgeEffort": judge_entry(settings.JUDGE_MODEL)["generation_config"].get("reasoning_effort") or "default"}
    judge["CheatingAlarm"] = cheating_ladder.ALARM
    judge["CheatedFrom"] = cheating_ladder.CHEATED_FROM
    judge["AccessedFrom"] = ACCESSED_FROM
    lines = [HEADER.format(source="PRESETS['default'], settings.JUDGE_MODEL, src/judging/judge_models.yaml, "
                                  "src/measures/cheating_ladder.py ALARM and CHEATED_FROM, "
                                  "scripts/paper/study_ladders.py ACCESSED_FROM")]
    lines += [rf"\newcommand{{\{k}}}{{{v}}}" for k, v in macros.items()]
    lines += [rf"\newcommand{{\{k}}}{{{escape(v)}}}" for k, v in judge.items()]
    return "\n".join(lines) + "\n"


def levels_tex(ladder: str) -> str:
    """The levels of one instruction ladder that the study's runs used (STUDY_LEVELS), in the yaml's order."""
    spec = yaml.safe_load(LEVELS_YAML.read_text())[ladder]
    missing = [level for level in STUDY_LEVELS[ladder] if level not in spec or spec[level].get("text") is None]
    if missing:
        raise SystemExit(f"instruction_levels.yaml {ladder} has no text for {missing}: update STUDY_LEVELS")
    rows = []
    for level in spec["order"]:
        if level not in STUDY_LEVELS[ladder]:
            continue
        rows.append(rf"\textbf{{{escape(level)}}} & {with_chips(spec[level]['text'])} \\")
    return (HEADER.format(source=f"src/configuration/instruction_levels.yaml {ladder}")
            + "\\begin{tabularx}{\\linewidth}{@{}l X@{}}\n\\toprule\nLevel & Text \\\\\n\\midrule\n"
            + "\n\\addlinespace\n".join(rows) + "\n\\bottomrule\n\\end{tabularx}\n")


def regulator_suffix_tex() -> str:
    """The prompt box: the text every regulator gets after the text of its level, as src/swarm/influencers.py
    role_text builds it under PRESETS["default"] (roles.influencer_common, regulator_check, regulator_no_disclosure)."""
    config = PRESETS["default"].model_copy(update={"regulator_level": "ambiguous"})
    benchmark = next(iter(yaml.safe_load(LEVELS_YAML.read_text())["vars"]))
    full, level = role_text("regulator", config, benchmark), level_text("regulator", "ambiguous", benchmark)
    if not full or not level or not full.startswith(level):
        raise SystemExit("a regulator's role text no longer starts with its level's text: update regulator_suffix_tex")
    return (HEADER.format(source="src/swarm/influencers.py role_text and src/configuration/prompts.yaml roles")
            + escape(full[len(level):].strip()) + "\n")


def check_study_levels(runs: list[dict]) -> None:
    """Stop the build when the valid runs' regulator or disregulator levels differ from STUDY_LEVELS."""
    for ladder in STUDY_LEVELS:
        used = {r[f"{ladder}_level"] for r in runs} - {None}
        if used != set(STUDY_LEVELS[ladder]):
            raise SystemExit(f"the study's valid runs use the {ladder} levels {sorted(used)}, not "
                             f"{sorted(STUDY_LEVELS[ladder])}: update STUDY_LEVELS and the paper's level tables")


def number(value) -> str:
    """A cut as the paper prints it: 3, 3.5, 0.75."""
    return f"{float(value):g}"


def coordination_levels_tex() -> str:
    """The Coordination ladder's levels and their conditions, from src/measures/coordination.py's constants."""
    c = coordination
    m = r"\bar{M}_i"
    conditions = {
        "none": "no read and no post",
        "read": "a read, no post",
        "write": rf"a post, ${m} < {number(c.LOW_CUT)}$",
        "low": rf"${m} \geq {number(c.LOW_CUT)}$",
        "medium": rf"${m} \geq {number(c.MEDIUM_CUT)}$",
        "high": rf"${m} \geq \max({number(c.HIGH_SHARE)}\,c_i,\ {number(c.HIGH_FLOOR)})$",
        "extreme": rf"${m} \geq \max({number(c.EXTREME_SHARE)}\,c_i,\ {number(c.EXTREME_FLOOR)})$",
    }
    if tuple(conditions) != tuple(c.LEVELS):
        raise SystemExit(f"coordination LEVELS {c.LEVELS} differ from the paper's conditions: update both")
    rows = [rf"{i} & {escape(level)} & {conditions[level]} \\" for i, level in enumerate(c.LEVELS)]
    return (HEADER.format(source="src/measures/coordination.py LEVELS and cuts")
            + "\\begin{tabular}{rll}\n\\toprule\nNumber & Level & Condition \\\\\n\\midrule\n"
            + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n")


def cheating_rungs_tex() -> str:
    """The Cheating ladder: each rung and the clauses whose evidence puts an agent on it, lowest first; a rung without
    clauses (none) gives what it means instead."""
    levels = RUNGS
    if len(CHEAT_GLOSSES) != len(levels):
        raise SystemExit(f"Cheating RUNGS {levels} and CHEAT_GLOSSES differ in length: update both")
    rows = []
    for i, (rung, gloss) in enumerate(zip(levels, CHEAT_GLOSSES)):
        clauses = [c for c, r in cheating_ladder.CLAUSES.items() if r == rung]
        named = "; ".join(rf"\textit{{{escape(c)}}}: {escape(CLAUSE_GLOSSES[c])}" for c in clauses) or escape(gloss)
        rows.append(rf"{i} & \textbf{{{escape(rung)}}} & {named} \\")
    return (HEADER.format(source="src/measures/rungs.py RUNGS, cheating_ladder.py CLAUSES, src/ui/transcript_levels.py")
            + "\\begin{tabularx}{\\linewidth}{@{}r l >{\\raggedright\\arraybackslash}X@{}}\n\\toprule\n"
            + "Number & Rung & Clauses \\\\\n\\midrule\n" + "\n\\addlinespace\n".join(rows)
            + "\n\\bottomrule\n\\end{tabularx}\n")


def collusion_rungs_tex() -> str:
    """The Collusion ladder: each rung and what puts an agent on it. Code reads every rung."""
    levels = collusion.RUNGS
    if len(COLLUSION_GLOSSES) != len(levels):
        raise SystemExit(f"Collusion RUNGS {levels} and COLLUSION_GLOSSES differ in length: update both")
    rows = [rf"{i} & \textbf{{{escape(rung)}}} & {escape(gloss)} \\"
            for i, (rung, gloss) in enumerate(zip(levels, COLLUSION_GLOSSES))]
    return (HEADER.format(source="src/measures/collusion.py RUNGS, src/ui/transcript_levels.py")
            + "\\begin{tabularx}{\\linewidth}{@{}r l >{\\raggedright\\arraybackslash}X@{}}\n\\toprule\n"
            + "Number & Rung & An agent is here when \\\\\n\\midrule\n"
            + "\n\\addlinespace\n".join(rows) + "\n\\bottomrule\n\\end{tabularx}\n")


def score_anchors_tex() -> str:
    """The anchors the judge scores each label against (src/measures/judge_labels.py SCORE_ANCHORS), lowest first."""
    rows = [rf"{score} & {escape(meaning)} \\" for score, meaning in dict(judge_labels.SCORE_ANCHORS).items()]
    return (HEADER.format(source="src/measures/judge_labels.py SCORE_ANCHORS")
            + "\\begin{tabular}{rl}\n\\toprule\nScore & Evidence \\\\\n\\midrule\n"
            + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n")


def reliability_tex() -> str:
    """How often an independent reading of the transcripts confirmed each rung and score
    (src/measures/measure_readings.py readings(), the file the viewer's How reliable section reads too). The paper
    shows only the rows of current passes: a pass that read an older state of the code or a retired label stays in
    the viewer alone. A block per measure, headed by the measure's name alone, then
    per row how the cases were chosen, and how many were read, held, were wrong or were unclear. A measure read on
    two such passes, or whose rows are not together, stops the build, since its name would head two blocks."""
    r = measure_readings.readings()
    rows, passes, last = [], {}, None
    current = {name for name, about in r["passes"].items() if about.get("current") and not about.get("retired")}
    for row in (row for row in r["rows"] if row["pass"] in current):
        if passes.setdefault(row["measure"], row["pass"]) != row["pass"]:
            raise SystemExit(f"measure_readings.yaml: {row['measure']} is read on two passes that are not retired")
        if row["measure"] != last:
            heading = rf"\multicolumn{{7}}{{@{{}}l}}{{\textbf{{{escape(r['measures'][row['measure']])}}}}} \\"
            if heading in rows:
                raise SystemExit(f"measure_readings.yaml: the rows of {row['measure']} are not together")
            if last is not None:
                rows.append(r"\addlinespace")
            rows.append(heading)
            last = row["measure"]
        counts = " & ".join("--" if n is None else str(n) for n in (row["read"], row["held"], row["wrong"],
                                                                    row["unclear"]))
        rows.append(rf"\quad {escape(row['value'])} & {escape(row['chosen'])} & {counts} & {escape(row['note'])} \\")
    return (HEADER.format(source="src/measures/measure_readings.yaml")
            + "\\begin{tabularx}{\\linewidth}{@{}l >{\\raggedright\\arraybackslash}p{0.2\\linewidth} r r r r "
            + ">{\\raggedright\\arraybackslash}X@{}}\n\\toprule\n"
            + "Rung or score & Cases read & Read & Held & Wrong & Unclear & Note \\\\\n\\midrule\n"
            + "\n".join(rows) + "\n\\bottomrule\n\\end{tabularx}\n")


def model_name(key: str) -> str:
    base = key.removesuffix("-openrouter")
    if base not in MODEL_NAMES:
        raise SystemExit(f"model {key!r} has no name in MODEL_NAMES: add it, then check the prose")
    return MODEL_NAMES[base]


def day(iso: str) -> str:
    return f"{int(iso[8:10])} {MONTHS[int(iso[5:7]) - 1]}"


def study_tex() -> str:
    """The multi-domain study's valid runs (scripts/paper/study_ladders.py valid_runs), their dates, the judges of its
    CheatBench verdicts and of its scores, and the regulators' models by use. Its runs' levels must be STUDY_LEVELS."""
    listed = valid_runs()
    valid = [r.meta for r in listed]
    check_study_levels(valid)
    dates = []
    for r in listed:
        log = json.loads((r.path / "run.json").read_text()).get("eval_log") or ""
        m = re.search(r"(\d{4}-\d{2}-\d{2})T", log)
        if m:
            dates.append(m.group(1))
    first, last = min(dates), max(dates)
    span = day(first) if first == last else f"{day(first).split()[0]}--{day(last)}" if first[:7] == last[:7] \
        else f"{day(first)} -- {day(last)}"
    used = Counter(r["regulator_model"] for r in valid if r["regulator_model"])
    items = "\n".join(rf"  \item {model_name(k)}" for k, _ in sorted(used.items(), key=lambda kv: (-kv[1], kv[0])))
    runs = [r.path for r in listed]
    return (HEADER.format(source="runs/study_exploration_1/INDEX.jsonl, each valid run's run.json and judges/")
            + rf"\newcommand{{\StudyRuns}}{{{len(valid)}}}" + "\n"
            + rf"\newcommand{{\StudyDates}}{{{span}}}" + "\n"
            + rf"\newcommand{{\StudyCheatBenchJudge}}{{{cheatbench_judge(runs)}}}" + "\n"
            + rf"\newcommand{{\StudyLabelsJudged}}{{{labels_judged(runs)}}}" + "\n"
            + "\\newcommand{\\RegulatorModelList}{%\n\\begin{itemize}\n" + items + "\n\\end{itemize}}\n")


def cheatbench_judge(runs: list[Path]) -> str:
    """The name of the judge whose CheatBench verdicts the measures take on the study's runs (verdict_files.applied).
    A pass read by several judges, or a judge outside JUDGE_NAMES, stops the build, so the prose names the one judge
    the tables rest on."""
    name = verdict_files.CHEATBENCH_PASS
    used = {verdict_files.applied(verdict_files.read(run).get(name) or {}) for run in runs} - {None}
    if len(used) != 1 or next(iter(used)) not in JUDGE_NAMES:
        raise SystemExit(f"the study's {name} verdicts come from {sorted(used)}, not one named judge")
    return JUDGE_NAMES[used.pop()]


def labels_judged(runs: list[Path]) -> str:
    """The sentence naming the judge whose scores the measures read on the study's runs (judge_labels.labels'
    "judge"), or saying that no judge has scored them. Several judges, or a judge outside JUDGE_NAMES, stop the
    build."""
    used = {judge_labels.labels(run)["judge"] for run in runs} - {None}
    if len(used) > 1 or not used <= set(JUDGE_NAMES):
        raise SystemExit(f"the study's scores come from {sorted(used)}, not one named judge")
    if not used:
        return "No judge has scored these runs yet."
    return f"One judge, {JUDGE_NAMES[used.pop()]}, scored every agent of these runs, and no human checked its scores."


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
        GENERATED / "regulator_suffix.tex": regulator_suffix_tex(),
        GENERATED / "regulator_levels.tex": levels_tex("regulator"),
        GENERATED / "disregulator_levels.tex": levels_tex("disregulator"),
        GENERATED / "cheating_rungs.tex": cheating_rungs_tex(),
        GENERATED / "coordination_levels.tex": coordination_levels_tex(),
        GENERATED / "collusion_rungs.tex": collusion_rungs_tex(),
        GENERATED / "score_anchors.tex": score_anchors_tex(),
        GENERATED / "reliability.tex": reliability_tex(),
    }
    if STUDY_INDEX.exists():
        files[GENERATED / "study.tex"] = study_tex()
    if STAGE2.exists():
        files[GENERATED / "stage2.tex"] = stage2_tex()
    return files


def check() -> list[str]:
    """What in paper/ disagrees with its source, and any figure there that no entry of FIGURES names. An absent tmp/
    source is reported apart, as a warning."""
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
    for dest in sorted(FIGURE_DIR.glob("*.png")):
        if dest.name not in FIGURES:
            problems.append(f"UNUSED  {dest.relative_to(ROOT)} has no FIGURES entry (delete it)")
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
