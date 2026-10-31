# Analysis tools

The analysis layer of the midterm report: Inspect Scout, Docent, NetworkX with a Gephi export, Transect, NDlib and
EoN, MAST and Who&When, and Hawk. Each tool reads one or more run directories under `runs/` and writes under
`runs/<study>/<name>/tools/<tool>/`: a `summary.json` with `tool`, `run`, `written_at` and the tool's own fields, plus its
files. The graph tool's folder is `graphs`. The results browser shows every tool's output on the run page ("Analysis
tools") and serves the files at `/api/runs/<study>/<run>/tools/<tool>/<file>`.

`tools/tool_dispatch.py` holds the command line, one subcommand per tool package, and each package's `<tool>_command.py`
adds its arguments. `tools/run_reader.py` is the shared reader: a run's agents, roles, verdicts, board posts,
transcripts, the turn timeline (run.json `rules.timeline`), each agent's steps with their rounds, its board reads, and
the domain's exploit vocabulary. Every tool refuses a missing run with exit code 2 before it does any work. The tools
import what they share with the run code from `src/` (the paths, the model registry, the run directory reader, the JSON
readers, the figure style and the judge's flags per task). They never import ORBIT, which their environment lacks.

## Setup

```
make tools-setup
```

This creates `tools/.venv` (Python 3.12) from `tools/pyproject.toml` and `tools/uv.lock` and downloads MAST's
taxonomy texts into `tools/failures/mast_taxonomy/` (gitignored, sha256 checked against the pinned commit). Hawk needs
no install: the hawk tool runs it through `uvx` on Python 3.13. The tools have their own environment because Transect
needs Python 3.12 and Inspect Scout a newer inspect-ai than ORBIT's pin in the run environment. Never create a
`pyproject.toml` at the repository root: uv would sync and empty `.venv`.

Run a tool from the repository root:

```
uv run --project tools python -m tools <tool> <run> [<run> ...]
make <tool> RUN=<run>
make analyse RUN=<run>
```

Tests: `make test-tools` runs `tests/tools/`. Lint: `make lint` covers `tools/`.

Keys: the LLM tools (scout, transect, failures, and hawk with `--run`) default to the judge's model,
`settings.JUDGE_MODEL` (`gpt-6-luna`, through `OPENAI_API_KEY`); `--model` or `--judge` take any entry of
`src/models/model_registry.yaml`. Docent needs
`DOCENT_API_KEY`, which the SDK reads from a `docent.env` in the working directory or a parent, then
`~/.docent/docent.env`, then the environment, in that order. A placeholder key in the file wins over a real key in the
environment.

## The tools

| command | what it does | writes |
|---|---|---|
| `scout <run>... [--model ENTRY] [--scanners a,b] [--grep-only]` | Inspect Scout scanners over each agent's transcript: honeypot_seen, honeypot_opened, honeypot_used, honeypot_submitted, cheating_proposed_on_board, regulator_intervened, each with the message, the ATIF step and the round it first shows; a grep scanner of the honeypot names; the findings validated against the judge's fields | `summary.json`, `findings.csv`, `grep.csv`, `onset.png` + `onset.csv`, `transcripts/` (the Scout database of the run's agents), `scans/` |
| `graph <run>... [--layout circular\|spring]` | NetworkX: who read whose posts (reader to author, weight = posts read, first round), oversight edges, each agent's cheating onset from scout; who started, the spread order, the posts each cheater read before its onset and whether their author was already cheating, contagion edges, degrees, density | `summary.json`, `graph.png` + `graph.csv`, `graph.gexf` (open in Gephi), `graph.json`, `nodes.csv`, `edges.csv` |
| `transect <run>... [--judge ENTRY] [--structural]` | Transect reports, one per agent (Transect refuses ORBIT's merged sample, so each agent's transcript is converted to OpenClaw telemetry); phases from `tools/transect/spec.yaml` (orientation, planning_on_board, honest_work, exploit_discovery, exploit_use, registration_or_submission, regulation, coordination); each agent's exploit_use phases checked against the judge | `summary.json`, `judged/<agent>/report.html`, `structural/<agent>/report.html`, `<mode>/phases.csv`, `token_timeline.csv`, `merged/report.html` |
| `contagion <run>... [--models si,threshold,sir] [--synthetic N] [--seed S]` | NDlib's SI and linear threshold models and EoN's SIR fitted by least squares to the observed adoption curves of dishonest behaviour (cheating onsets) and honest behaviour (interventions, and the judge's `disclosed_access` flag) over the read graph; runs under ten agents are marked degenerate; `--synthetic N` exercises the pipeline on a synthetic board | `summary.json`, `curves.csv`, `fit.json`, `contagion.png` |
| `failures <run>... [--model ENTRY] [--methods ...] [--max-steps 60] [--no-mast] [--no-who-when]` | MAST's fourteen failure modes with the annotator prompt of the MAST notebook word for word, and Who&When's three attribution methods (all at once, step by step, binary search) with their prompts word for word, over the run's merged trace of posts and steps | `summary.json`, `mast.json`, `who_when.json` |
| `docent <run>... [--board] [--dry-run] [--collection NAME \| --collection-id ID]` | one Docent AgentRun per agent episode (the ATIF transcript through the SDK's converter) with the run, role, verdict, roster, read graph and rounds in its metadata, and with `--board` one AgentRun per run holding the board; uploads to a collection and prints its URL | `summary.json`, `agent_runs.json` |
| `hawk <run>... [--run] [--source scout\|eval_log] [--scanner NAME ...] [--model ENTRY]` | the scan config for Hawk's local runner (`hawk local scan`) over the run's Scout database with our scanners, through the shim package in `tools/hawk/scanner_package/`; `--run` runs it and reads the scan's completion and errors back | `scan.yaml`, `summary.json`, `results/` |

The order matters: graph and contagion read scout's onsets when they exist; contagion reads graph's read graph; hawk
reads scout's transcripts database. `make analyse RUN=<run>` runs scout, graph, transect, contagion, failures and hawk
in that order; it leaves out docent, which uploads to a server. Scout, transect and failures call the judge's model
there, so `make analyse` costs money; hawk only writes its scan config.

## What each tool's output means

**scout.** `agents.<agent>.<signal>` gives `found`, the message and ATIF step, the round (from the timeline), the quote
and the explanation. `onset.<agent>` is the earliest round of honeypot_opened, honeypot_used or honeypot_submitted.
`validation` sets each signal beside one of the task's judge flags (`src/measures/verdict_fields.py`) and counts the
agreements. The signal definitions are a choice, written in `scout_scanners.py`: opened counts an attempt, even a
failed one, which is openmath's `attempted_access`; reading the openmath registry or a knowledge-work review log
counts as seen and never as opened. SWE's team branches are permitted collaboration and no part of the exploit. The
scanners are LLM calls, so a signal's step can move between runs by a message or two.

**graph.** `who_started` is the agent with the earliest onset (`first_onset_ties` lists ties), `spread_order` the
agents by onset, `exposure_before_onset` the posts each cheater read before its onset with `exposed_to_cheater`
when the author's onset was earlier, `contagion_edges` those author-to-reader pairs. `onset_judge_disagreements`
lists agents scout gives an onset and the judge clears, and agents the judge calls cheaters and scout gives no onset.
In GEXF an unknown onset is -1 and the verdict is a string.
Gephi: `brew install --cask gephi`, then File, Open, `graph.gexf`; Layout, ForceAtlas 2; Appearance, nodes by `role`.

**transect.** `phases.<agent>` is Transect's phase per turn span, with the rounds; `agreement.<agent>` says whether
the agent's exploit_use phases agree with the judge's verdict. The context gives the phase judge the run's task, its
goal, exploit and protected material (the `instruction_levels.yaml` vars of the run's domain), the agent's hidden role
and the branches that hold the exploit (from `run.json` access), so a teammate's merges do not read as exploit use.
The reports load their charts from a CDN; a browser renders them, `report.html` is also readable as text.

**contagion.** `observed_final` gives the final shares; `fits.<behaviour>.<model>` the fitted parameter, the final
size and the sum of squared residuals, or `skipped` with the reason; `degenerate` with its reason for boards under ten
agents. On the synthetic complete board the fitted SI beta sits about a fifth under the planted one because the
plant makes regulators immune and the model does not. The honest curve reads each task's disclosure flag from the judge:
`disclosed_access`, `disclosed`, `disclosed_to_arbiter` or `final_source_disclosure`. Knowledge work and SWE have none.

**failures.** `mast.modes` is the notebook parser's reading and `modes_strict` a line-anchored one; the parser takes
the first yes or no after a mode number, including inside a word, so prefer `modes_strict` and read `disagreements`.
`reply_names` records the name the reply gave each number: gpt-6-luna swaps 3.2 and 3.3 between the prompt's and
the taxonomy file's numbering. `who_when.<method>` gives the agent, the step (an index into the merged trace), the
round, and `step_agent`, the agent who actually made that step; all_at_once often names a different agent from the
step it cites, as the paper's method does on long histories. The Who&When prompts' answer line is replaced by the
judge's description of the run's failure, since our runs have no ground-truth answer.

**docent.** A dry run writes the AgentRuns as JSON. An upload creates a collection unless `--collection-id` names one
and prints `<frontend>/dashboard/<id>`; the id is in `summary.json`. Every conversion gives new AgentRun ids, so an
upload repeated into the same collection duplicates the runs.

**hawk.** `hawk local scan` needs no server and no login; it builds a fresh virtualenv per scan under
`~/.cache/inspect-action/` (about 400 MB each, never deleted by Hawk) and calls the model with the provider key.
A deployed Hawk (`hawk scan run`, `HAWK_API_URL`, login) is not used.

## Caveats

- Every LLM tool (scout, transect, failures) is one sample at the model's default temperature. Repeated runs move
  a step or a label.
- Outputs accumulate: scout's `scans/`, transect's `scan_id=*` folders and hawk's `results/` keep every invocation;
  each `summary.json` names the current one.
- `tools/run_reader.py` reads board reads from `board_read` calls.
- No real run with ten or more agents has gone through contagion yet, so no non-degenerate fit on real data exists.
- Docent's upload path has never run against the server: the key on this machine is a placeholder.
