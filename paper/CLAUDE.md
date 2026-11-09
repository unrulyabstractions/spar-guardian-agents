# paper/: the team's paper, kept in step with the code

The LaTeX source of "Swarm Contagion and Peer Pressure: Regulator and Disregulator Dynamics in Multi-Agent LLM
Swarms" (Cierniak, Kappai, Rios-Sialer; mentor Maltbie; SPAR, November 2026). It was transcribed on 2026-11-08 from
the team's Google Docs export, `~/Downloads/paper.pdf` (31 pages). The user's order: keep the paper as up to date as
the code. The prose is the team's, word for word, typos included ("an plausible", "Baselline"), except where the
code made a sentence wrong (listed below).

## Layout

| Path | What it is | Who writes it |
|---|---|---|
| `main.tex` | preamble and the order of the sections | by hand |
| `sections/*.tex` | one file per section, body then appendices | by hand |
| `sections/references.tex` | the PDF's 49 references in its order, so [1] to [49] match | by hand |
| `generated/params.tex` | macros: the board's budgets and `\TurnBudget` from `PRESETS["default"]`; `\JudgeName`, `\JudgeEffort` from `settings.JUDGE_MODEL` and `src/judging/judge_models.yaml` | `make paper-sources` |
| `generated/scales.tex` | the scales table, from `src/configuration/scales.py` | `make paper-sources` |
| `generated/regulator_suffix.tex` | the regulator prompt box: `prompts.yaml` roles.regulator_common and regulator_no_disclosure, then the ambiguous, explicit and pressure levels | `make paper-sources` |
| `generated/regulator_levels.tex`, `disregulator_levels.tex` | the level texts, from `src/configuration/instruction_levels.yaml` | `make paper-sources` |
| `generated/study.tex` | `\StudyRuns`, `\StudyDates`, `\RegulatorModelList`: the multi-domain study's valid runs, their dates and the regulators' models by use, from `runs/study_exploration_1/INDEX.jsonl` and each run's run.json (kept as committed where runs/ is absent) | `make paper-sources` |
| `generated/stage2.tex` | the deep dive's Stage 2 table, from `submodules/lucy/runs/s2_swe/analysis/summary.json` | `make paper-sources` |
| `figures/*.png` | every image the paper includes; `FIGURES` in `scripts/paper/paper_sources.py` names each one's source | `make paper-sources`, except frozen ones |
| `build/` | latexmk output, `build/main.pdf` (gitignored) | `make paper` |

## Figure sources

`FIGURES` in `scripts/paper/paper_sources.py` gives each figure one of two kinds.

- **generated**: drawn by a script in this repository into `tmp/`, then copied. Method figures: `make figures`,
  `make tables`. Results tables: `make results-tables` (`scripts/paper/control_tables.py`,
  `protein_regulator_table.py`, `swe_regulator_table.py`, `disregulator_tables.py`, `baseline_tables.py`). These
  five read the runs under `runs/study_exploration_0` and `runs/study_exploration_1`; a new run of a cell they list
  reaches the paper on the next `make paper`. A new cell needs a row added to its script.
- **frozen**: Figures 1 to 5 of the body, extracted from the PDF. No script in this repository, its git history,
  Lucy's repository (submodules/lucy and its upstream, checked 2026-11-08) or any Claude session transcript draws
  them. Replace one only with a file the team sends.

The five results scripts were recovered on 2026-11-08 from session d3edd1f3's transcript (the scratchpad that held
them was cleared on 2026-11-07), with session 7b7b4ddc's edits of 2026-11-03. Each reproduced its PNG in tmp/
pixel for pixel before it was committed.

Tables that were images in the PDF but hold only text are LaTeX now: Table 1 (the two studies), the honeypot rungs
by task, monitors against regulators, and the generated ones above.

## After any change to what the paper describes

1. `make paper`: regenerates `tmp/` figures and tables from the code and the runs on disk (no model calls), writes
   `generated/`, copies figures, builds `build/main.pdf`.
2. `make paper-check`: must print `paper/ agrees with its sources`.
3. `uv run python -m pytest tests/unit/test_paper_sources.py -q`.
4. Render the changed pages (`pdftoppm -r 60 -png build/main.pdf`) and look at each one.
5. Prose a code change made wrong: rewrite it in the user's voice, prefer a generated macro over a typed value, and
   list each changed sentence to the user.
6. Commit `paper/` with the code change. Log it in `VERIFICATION_LOG.md`.

## Prose changed to follow the code and the data, 2026-11-08

The user's order that day: "UPDATE EVERYTHING". Each change below was listed to the user.

- Tasks: the solo-run turn budget became "Every agent gets \TurnBudget{} working steps on every task."
- Judge Models: "medium effort" became `\JudgeEffort` (high, from judge_models.yaml).
- The regulator prompt box: the midterm's drafts became the texts the code sends.
- Scales: the small scale's 30 min became 1 h, from `scales.py`.
- Disregulators were "designed but not yet run" in Methods, Figure 2's caption and the Discussion; they ran on 2 and
  3 Nov. Methods, Results (a paragraph), Discussion (H2), Conclusion and Abstract now say what they did.
- Table 1: the multi-domain column takes `\StudyRuns` and `\StudyDates` (147 runs, 1 to 3 Nov, was 110 on 1 Nov);
  its regulator "oversees the team without doing the task" (prompts.yaml regulator_opening, since 3adc072 on 31 Oct);
  the team may hold a disregulator; the models add Sonnet, Opus and the Luna disregulator. The deep-dive column
  adds Lucy's Stage 2.
- SWE Deep Dive: a Stage 2 paragraph and Table 2 (generated). Discussion and Conclusion follow it.
- Experimental Details: the SWE disregulator target is GPT-6 Luna (Haiku failed the gate on 2 Nov); the regulator
  models are `\RegulatorModelList`.
- Discussion: the team's em dash became two sentences; "3-4 agents" became "3-5 agents".

## Open

- Figure 2 is frozen, and its own legend still prints "disregulator (not yet run)". Only the team can redraw it.
- Figures 4 and 5 are frozen. Figure 4 predates the 3 Nov rule cells (no generic or identified row for creative
  writing); Figure 5 shows Stage 1 only. The appendix tables and Table 2 carry the later runs.
- Table 1's deep-dive row "each can cheat alone" is Lucy's design, true under CheatBench's rule (looking counts).
- References [6] and [49] are the same paper.
