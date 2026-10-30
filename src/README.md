# src

The experiment on ORBIT. A run takes one benchmark and one `SwarmConfig`, builds an Inspect task, runs it, exports it to `runs/<name>/` and serves the results. The turn rules use ORBIT's public API and one private attribute, `ScheduleRuntime._current`, with no patch to ORBIT. Run everything from the repository root with `uv run python -m src <command>`.

## How to read the tree

A module with a leading underscore is implementation: only its own package and the tests import it. A module without one is its package's API: another package, an example, a script or a tool imports it. Each package's `__init__.py` lists its API modules.

Files that run inside a container sit in a domain's `sandbox/` folder, or in `src/sandbox/` beside the module that sends them (`agent_users.py`, `agent_machines.py`, `staging.py`). No Python module imports them. Their module reads each one as text and sends it to the sandbox.

## A run, in order

```
python -m src run <name>     cli.py reads the flags
        |
  configuration/             the preset, the scale and every --set become one SwarmConfig
        |
  domains/ <-- sandbox/      the Benchmark fixes the plan and gives the sample, tools, setup and scorers
        |
  swarm/                     the board, the hidden roles, submit and stop_working, the turn rules
        |
  tasks.py                   all of the above as one ORBIT task
        |
  run.py                     inspect_ai.eval in process, with the judge as a scorer    -> logs/<name>/
        |
  export/                    the eval log as a run directory                           -> runs/<name>/
        |
  judging/                   verdicts from the log, the judged ladder from transcripts -> judge.jsonl, ladder.jsonl
        |
  measures/                  the code ladder, the summaries, the figures               -> signals.jsonl, out/<name>/
        |
  ui/                        the browser over runs/ and the eval logs
```

`models/` and `utils/` serve every step.

## The packages

### configuration/

Every setting of a run and every text we add to what the models read. `src/configuration/README.md` gives the details and what each option does.

- `swarm_config.py`: `SwarmConfig`, the frozen model of one run, and its derived values.
- `presets.py`: a config from text: the presets, `key=value` pairs and the flags every entry point shares.
- `scales.py`: the size of a run as one name, from `small` to `gigantic`.
- `access_policy.py`: which parts of the exploit each agent holds.
- `prompt_texts.py`: the system prompt texts that depend on a setting.
- `settings.py`: the paths, the default models, the turn budget and the submodule pins.

The texts live in `prompts.yaml` and `instruction_levels.yaml`.

### domains/

CheatBench's tasks. Each domain gives the sample, the sandbox, the setup, the graders and the code ladder's rules. `src/domains/README.md` gives each domain's rules and how to add one.

- `__init__.py`: `DOMAINS` by key, for `--benchmark` and the `@task` entry points, and `BY_TASK` by CheatBench's task name.
- `benchmark_protocol.py`: `Benchmark`, `RowBenchmark` and `Plan`: what a domain implements, and the plan of one run.

Each package's `<d>_task.py` holds its Benchmark. The other modules a package exposes are the ones the box runner and the measures import.

| package | Benchmark | other API modules |
|---|---|---|
| `openmath/` | `OpenMath` | none |
| `swe/` | `SWE` | `swe_inputs.py`, `swe_ladder.py`, `swe_sandbox.py`, `swe_settings.py` |
| `knowledge/` | `KnowledgeWork` | `knowledge_sandbox.py`, `knowledge_settings.py` |
| `interview/` | `SubsetSum`, `PrimeFactorization` | none |
| `protein/` | `ProteinDesign` | `protein_rows_build.py` |
| `creative/` | `CreativeWriting` | `creative_sandbox.py` |
| `chess/` | `Chess` | `chess_rows_build.py` |

### sandbox/

What every domain builds its sandbox from. Nothing here knows a domain.

- `agent_tools.py`: `Seat`, where one agent works, and `AgentTools`, the agents' `shell` and `run_python` bound per seat.
- `agent_users/agent_users.py`: one Linux user per agent in one container, for openmath, knowledge work and creative writing.
- `machines/agent_machines.py`: one machine per agent on CheatBench's python311 image, for the interview tasks and protein design. Chess and creative writing build on the same image.
- `cheatbench_task_files.py`: CheatBench's task folder and built rows, as every domain reads them.
- `docker_images.py`: images on the host, built from a recipe when the label no longer names it.
- `sandbox_hygiene.py`: the processes an agent leaves, the loopback channel and what the sandbox holds at the end.
- `sandbox_probe.py`: one command's record, a table of commands and the setup solver that probes the seats.
- `staging.py`: files written on the host for a sandbox: atomic writes, compose files and staging tarballs. It also sends `team_remote_lock.sh`, which locks a bare team remote.
- `task_tool_store.py`: the sample store of a task's own tool.

### swarm/

The team on ORBIT's API.

- `board.py`: one shared channel as ORBIT configuration, and the tools `board_status`, `board_read` and `board_post` with the per-round budgets.
- `hidden_roles.py`: who is regulator or disregulator, on a board whose config asks for one, and the texts each role gets.
- `submit_and_stop.py`: `submit` and `stop_working`, the tools that end an agent's work. ORBIT reserves `submit`, so the tool is bound as `team_submit` and shown to the model as `submit`.
- `awareness_probes.py`: Lucy's end-of-episode questions as an Inspect scorer.
- `turn_rules/`: the plan of rounds under the turn budget, the free board steps, the budgets' counters, the halt after a hand-in and the model filter. Its `__init__.py` is the API. `turn_activation.py` is the callback ORBIT imports by its dotted name after every activation.

The implementation is `_regulator_oversight.py` (`agent_actions`, the regulators' tool) and the `_turn_*.py` modules. Every run carries the turn rules as a passive defense. Its model filter takes ORBIT's assistant paragraph out of the system message and appends the continue prompt (`prompts.yaml` `continue`) when the provider input would end with a model turn, which Gemini rejects.

### tasks.py

`swarm_task(benchmark, config)` turns the plan, the sample, the tools, the prompts, the board and the turn rules into one ORBIT task. It adds the judge to the scorers and records the run's facts in the sample metadata. The `@task` entry points `openmath`, `swe`, `knowledge_work`, `subset_sum`, `prime_factorization`, `protein_design`, `creative_writing` and `chess` serve `inspect eval`.

### run.py

The commands `run`, `judge`, `ladder`, `rejudge` and `regenerate`. `run` checks the keys and the routing, builds the task, calls `inspect_ai.eval` in process, exports the log, prints the summary and places the agents on the code ladder.

### export/

The eval log as a CheatBench run directory.

- `run_export.py`: writes an `.eval` log as a run directory.
- `export_episodes.py`: the files one sample writes, and its episode records.
- `eval_sample_view.py`: one sample read the same way, finished or live.
- `atif_transcripts.py`: one sample as one ATIF trajectory per agent.

`_export_run_blocks.py` is the implementation: the blocks of `run.json`.

### judging/

The LLM judges. CheatBench's judge runs inside the eval as the scorer `cheatbench_judge`, so the export finds its verdicts in the log. `judge` and `rejudge` judge a finished log again. The judged ladder reads a run's stored transcripts.

- `cheatbench_judge.py`: CheatBench's judge, in process through Inspect's model API.
- `judging_ladder.py`: the cheating ladder as an LLM judge, one call per agent.

`judge_models.yaml` holds the judge entries. `_ladder_verdict_fields.py` and `_presentation_verdict_fields.py` are the addenda to CheatBench's verdict schema.

### measures/

Everything computed over a run directory, without a model.

- `run_directory.py`: `RunDir`, one run directory read through one object.
- `code_ladder.py`: exposed, tried, opened, used, checked and shipped per agent from the transcript, the probe and the domain's records. It writes `signals.jsonl` and run.json `signals`.
- `team_ladder.py`: the team record of a sample: its rungs, its collaboration level, the judged presentation and the outcomes.
- `ladder_domain.py`: `Domain`, what a domain tells the code ladder, and `Evidence`. Each domain subclasses it in `_<d>_ladder.py` (`swe_ladder.py` for SWE).
- `honeypot_fingerprints.py`: the honeypot's fingerprints, and the probe that finds them in a sandbox.
- `shell_commands.py`: shell commands as the code ladder reads them: pipelines, simple commands, their words, grep calls.
- `rungs.py`: the ladder's scales in order, and the table of counts both ladders print.
- `episode_summary.py`: one line per episode, and the lone runs' turns for `budget`.
- `board_engagement.py`: the board engagement funnel, per role and per agent.
- `swe_measures.py`: Lucy's SWE measures, per agent and per arm.
- `verdict_fields.py`: the judge's flags per task (`FLAGS_BY_TASK`), for the summary, the figures, the browser and the tools.
- `run_figures.py`: one run's figures: each agent's steps round by round, the board's actions per round, the judge's flags per agent.

### ui/

The results browser, a local single-page UI over `runs/` and the eval logs. `API.md` is its HTTP contract.

- `launch.py`: starts and stops the browser as a background process.

`python -m src.ui` serves it in the foreground. The implementation is `_server.py` (the HTTP routes), `_run_store.py` (the run directories), `_eval_log_views.py` (the eval log's sample, context windows and timeline) and `static/` (`index.html`, `style.css` and the ES modules under `app/`, booted by `main.js`).

### models/

The agents' and the judges' models.

- `model_registry.py`: every model we can consider (`model_registry.yaml`) and the name each route needs.
- `swarm_model_provider.py`: the `swarm` provider, which runs a role's own model behind a bare model string.
- `gemini_402_retry.py`: treats the Gemini API's 402 as a retryable error.

### utils/

Shared helpers.

- `cheatbench_core.py`: CheatBench's `core` package on the import path.
- `figure_style.py`: the look every figure shares.
- `json_files.py`: the JSON and JSON Lines readers.

## Commands

`cli.py` parses the arguments and calls `run.py`, `measures/`, `ui/launch.py` or `models/model_registry.py`.

| command | what it does |
|---|---|
| `run <name> [--benchmark openmath\|swe\|knowledge_work\|subset_sum\|prime_factorization\|protein_design\|creative_writing\|chess] [--preset default] [--scale small\|medium\|large\|extra_large\|gigantic] [--set key=value ...] [--no-judge]` | runs one task, writes `logs/<name>/`, exports `runs/<name>/` and prints the summary; the preset applies first, then the scale, then every `--set`; `--no-judge` is `--set judge=none` |
| `run ... --row R` | the benchmark's row: the openmath_agent row (`riemann` by default), the SWE issue (`flipt-02e2`), the knowledge_work row (`2ea2e5b5`), the subset_sum or prime_factorization question (`subsetsum_n60d10_4`, `fact_n52_4`), the protein_design target (`alfalfa_chi`), the creative_writings brief (`museum_poem`) or the chess position (`LR4pA`) |
| `run ... --honeypot as_released\|own_piece\|jigsaw [--no-grade]` | Lucy's honeypot on SWE, `jigsaw` by default; `--no-grade` skips the hidden tests |
| `run ... --model M --timeout S` | the agents' model, an entry of `src/models/model_registry.yaml` or an Inspect model string, and the seconds one model request may take (default `MODEL_TIMEOUT_S`) |
| `run ... --worker-model M --regulator-model M --disregulator-model M` | a role's own model, set after every `--set`; it runs through the swarm provider with its registry routing and the agents' generate config. The eval log names its calls `swarm/<model>`, and `runs/<name>/episodes.jsonl` names each agent's real model |
| `judge <eval_log> <run_name> [--judge gpt-6-luna] [--sync] [--judge-errors]` | judges a finished log (again, or for the first time after `--no-judge`) and exports it with the verdicts; batch API unless `--sync` |
| `rejudge <run ...> [--judge gpt-6-luna] [--sync] [--no-judge] [--no-ladder] [--view full\|actions] [--judge-errors]` | CheatBench's judge from each run's eval log (named in `run.json`), then the ladder over its transcripts; a run whose log is gone gets the ladder only |
| `regenerate <run ...> [--no-export] [--no-measures] [--no-plots]` | rebuilds what a run directory derives from its eval log: the export (keeping the verdicts in `judge.jsonl`), the code ladder, the SWE measures and the figures |
| `signals <run ...> [--table]` | places every agent and every team on the cheating ladder by code, no judge: `signals.jsonl` with the agent rows, then one team record per sample, and run.json `signals.team`; every run gets it as it ends, and `judge` and `ladder` run it again; `--table` prints the agents' table, then the teams' |
| `ladder <run ...> [--judge gpt-6-luna] [--sync] [--view full\|actions] [--agents k7q2m,t4x9n] [--force] [--table]` | places every agent on the cheating ladder from its stored transcript: `runs/<name>/ladder.jsonl` with the rung, the cited calls and their rounds, pushback and forbidden method, the honesty labels and the presentation, then one team row per sample; `--table` sums the existing files |
| `ui [start\|stop] [--port 8765] [--no-open]` | starts or stops the results browser in the background |
| `summary <run ...> [--full]` | one line per episode with its verdict and the task's judge flags, the swarm composition and the shutdown rate (the share of the team's agents that ended with `stop_working` before any hand-in) |
| `engagement <run ...> [--json]` | the board engagement funnel per role and per agent |
| `swe-summary <run ...>` | Lucy's SWE measures per agent and per arm, written to `swe_summary.json` |
| `budget <lone_run ...>` | lone runs' turns and their median |
| `models [NAME]` | the models we can consider, one row per entry with its Inspect string, key variable, context, prices and CheatBench entries; with a name, that entry in full |
| `plots <run> [--out DIR]` | one run's figures as PNG with CSV twins under `out/<run name>/` (gitignored) |

The judge runs inside the eval as the scorer `cheatbench_judge`, bound to the model role `judge`. With `--no-judge` the run is exported without verdicts, and `rejudge` adds them later. With `batch_judge` the judge's calls go through the provider's batch API; `judge` and `ladder` batch too unless `--sync`.

`inspect eval src/tasks.py@openmath -T preset=default -T regulators=1` also works. Every `-T` key other than the task's own arguments sets a config field, and `-T scale=medium` applies a scale first. This form skips the key and routing checks, the role models built before the sandbox starts, and the Gemini 402 retry.

## Outside src/

| folder | holds |
|---|---|
| `examples/` | one script per task and cell of the first experiment; see `examples/README.md` |
| `scripts/` | `screen.py` (the example cells in bulk), `paper/` (the paper's figures, tables and appendix) and `box/` (the rented boxes; see `scripts/box/README.md`) |
| `tools/` | the analysis tools in their own environment; see `tools/README.md` |
| `tests/` | `unit/` (one test module per module or concept), `smoke/` (the mock-model suites), `tools/` (the analysis tools' tests) and `support/` (what they share) |
| `submodules/` | CheatBench, ORBIT, Lucy's repository and Inspect Swarm, read only |
