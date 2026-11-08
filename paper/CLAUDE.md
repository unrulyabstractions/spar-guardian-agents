# paper/: the team's paper, kept in step with the code

The LaTeX source of "Swarm Contagion and Peer Pressure: Regulator and Disregulator Dynamics in Multi-Agent LLM
Swarms" (Cierniak, Kappai, Rios-Sialer; mentor Maltbie; SPAR, November 2026). It was transcribed on 2026-11-08 from
the team's Google Docs export, `~/Downloads/paper.pdf` (31 pages). The prose is the team's, word for word, typos
included ("an plausible", "Baselline"). It changes only when the code it describes changes or the user asks.

## Layout

| Path | What it is | Who writes it |
|---|---|---|
| `main.tex` | preamble and the order of the sections | by hand |
| `sections/*.tex` | one file per section, body then appendices | by hand |
| `sections/references.tex` | the PDF's 49 references in its order, so [1] to [49] match | by hand |
| `generated/params.tex` | the board's budgets as macros (`\MaxMessageChars` and the rest), from `PRESETS["default"]` | `make paper-sources` |
| `generated/scales.tex` | the scales table, from `src/configuration/scales.py` | `make paper-sources` |
| `generated/regulator_levels.tex`, `disregulator_levels.tex` | the level texts, from `src/configuration/instruction_levels.yaml` | `make paper-sources` |
| `figures/*.png` | every image the paper includes; `FIGURES` in `scripts/paper/paper_sources.py` names each one's source | `make paper-sources`, except frozen ones |
| `build/` | latexmk output, `build/main.pdf` (gitignored) | `make paper` |

## Figure sources

`FIGURES` in `scripts/paper/paper_sources.py` gives each figure one of three kinds.

- **generated**: drawn by a script in this repository (`make figures`, `make tables`) into `tmp/`, then copied.
- **tmp**: copied from `tmp/tables/results/`. The scripts that drew these tables lived in session d3edd1f3's
  scratchpad, which macOS cleared on 2026-11-07. Their last versions survive only in that session's transcript
  (`~/.claude/projects/-Users-unrulyabstractions-work-spar-guardian-agents/d3edd1f3-*.jsonl`: control_tables.py,
  regulator_table.py, swe_regulator_table.py, disregulator_table.py, baseline_tables.py, with the edits of
  2026-11-03 from session 7b7b4ddc). New runs do not reach these tables until those scripts are recovered into
  `scripts/paper/`.
- **frozen**: Figures 1 to 5 of the body, extracted from the PDF. They come from the team (Lucy's Qwen3-32B runs,
  the SWE deep dive), not from this repository. Replace one only with a file the team sends.

Tables that were images in the PDF but hold only text are LaTeX now: Table 1 (the two studies), the honeypot rungs
by task, monitors against regulators, and the generated ones above.

## After any change to what the paper describes

1. `make paper`: regenerates `tmp/` figures and tables (no model calls), writes `generated/`, copies figures,
   builds `build/main.pdf`.
2. `make paper-check`: must print `paper/ agrees with its sources`.
3. `uv run python -m pytest tests/unit/test_paper_sources.py -q`.
4. Render the changed pages (`pdftoppm -r 60 -png build/main.pdf`) and look at each one.
5. Prose a code change made wrong: rewrite it in the user's voice and list each changed sentence to the user.
6. Commit `paper/` with the code change. Log it in `VERIFICATION_LOG.md`.

## Known disagreements, 2026-11-08

Each was in the PDF before this transcription and is reported to the user, not fixed:

- The PDF's scales table gave the small scale 30 min; `scales.py` says 3600 s. The generated table says 1 h.
- "Each task has a turn budget ... divided equally among agents": the code gives every agent `TURN_BUDGET`
  (100) working steps, not a share of a solo run.
- Table 1's deep-dive row "each can cheat alone" breaks rule 1 of the root CLAUDE.md for that study's design.
- The PDF's appendix tables predate the 2026-11-03 redraw and the random agent IDs; `paper/figures/` holds the
  current ones (positive control with SWE x Luna, the "Both + Haiku enforce" rows, the `tampered` rung, IDs like
  k7q2m).
- References [6] and [49] are the same paper.
