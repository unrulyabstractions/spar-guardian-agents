# src

The experiment on ORBIT. A run takes one benchmark and one `SwarmConfig`, builds an Inspect task, runs it, exports it to `runs/<study>/<name>/` and serves the results. The turn rules use ORBIT's public API and one private attribute, `ScheduleRuntime._current`, with no patch to ORBIT. Run everything from the repository root with `uv run python -m src <command>`.

## How to read the tree

A module with a leading underscore is implementation: only its own package and the tests import it. A module without one is its package's API: another package, an example, a script or a tool imports it. Each package's `__init__.py` lists its API modules.

Files that run inside a container sit in a domain's `sandbox/` folder, or in `src/sandbox/` beside the module that sends them (`access_monitor.py`, `agent_users.py`, `agent_machines.py`, `staging.py`). No Python module imports them, but for one function: the export hashes a tool call's argv with `access_monitor_daemon.py` `exec_digest`, so the two sides of that hash cannot drift apart. Their module reads each one as text and sends it to the sandbox.

## A run, in order

```
python -m src run <name>     cli.py reads the flags
        |
  configuration/             the preset, the scale and every --set become one SwarmConfig
        |
  domains/ <-- sandbox/      the Benchmark fixes the plan and gives the sample, tools, setup and scorers
        |
  swarm/                     the board, the influencers, submit and stop_working, the turn rules
        |
  tasks.py                   all of the above as one ORBIT task
        |
  run.py                     inspect_ai.eval in process, with the judge as a scorer    -> logs/<study>/<name>/
        |
  export/                    the eval log as a run directory                           -> runs/<study>/<name>/
        |
  judging/                   CheatBench's judge in the eval and on stored runs, and the labels judge
                                                                                        -> judges/<pass>.<judge>.jsonl
        |
  measures/                  the code record, the three ladders, the figures            -> signals.jsonl, out/<study>/<name>/
        |
  ui/                        the transcript viewer and the results browser over runs/ and the eval logs
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
- `settings.py`: the paths, the default models and the submodule pins.
- `task_names.py`: each task's three names: CheatBench's task name, which run.json records, the domain key the code uses, and a display name. It uses the standard library alone, so the analysis tools read it too.

The texts live in `prompts.yaml` and `instruction_levels.yaml`.

### domains/

CheatBench's tasks. Each domain gives the sample, the sandbox, the setup, the graders and the rules of the code's evidence. `src/domains/README.md` gives each domain's rules and how to add one.

- `__init__.py`: `DOMAINS` by key, for `--benchmark` and the `@task` entry points, and `BY_TASK` by CheatBench's task name, through `configuration/task_names.py`.
- `benchmark_protocol.py`: `Benchmark`, `RowBenchmark` and `Plan`: what a domain implements, and the plan of one run.

Each package's `<d>_task.py` holds its Benchmark. The other modules a package exposes are the ones the box runner and the measures import.

| package | Benchmark | other API modules |
|---|---|---|
| `openmath/` | `OpenMath` | none |
| `swe/` | `SWE` | `swe_inputs.py`, `swe_ladder.py`, `swe_replay.py`, `swe_sandbox.py`, `swe_settings.py` |
| `knowledge/` | `KnowledgeWork` | `knowledge_sandbox.py`, `knowledge_settings.py` |
| `interview/` | `SubsetSum`, `PrimeFactorization` | none |
| `protein/` | `ProteinDesign` | `protein_rows_build.py` |
| `creative/` | `CreativeWriting` | `creative_sandbox.py` |
| `chess/` | `Chess` | `chess_rows_build.py` |

### sandbox/

What every domain builds its sandbox from. Nothing here knows a domain.

- `access_log/access_monitor.py`: the access log's API: what a domain watches, the sidecar, the setup solver that starts the monitor and the capture of its rows. `access_monitor_daemon.py` and `git_object_files.py` run in a container.
- `network_log/network_monitor.py`: the network log's API: the tracer's image, the sidecar, the setup solver that starts the monitor and its control, and the capture of its rows. `network_monitor_daemon.py` runs in the sidecar.
- `proc_hiding/proc_hiding.py`: what /proc shows an agent: the AppArmor profile that denies every page map, loaded into the Docker host and set on every service of the compose file; the setup solver that remounts each agent machine's /proc with hidepid and a group for the harness, then proves both; and the record run.json `sandbox.proc` carries. `host_helper.py` runs in a privileged helper container, `proc_group.py` and `proc_proof.py` in the agents' machines.
- `agent_tools.py`: `Seat`, where one agent works, and `AgentTools`, the agents' `shell` and `run_python` bound per seat.
- `command_jobs.py`: every command call, with no time limit of its own: a command still running when its round's time runs out becomes a job, which `job_output` reads and `job_stop` stops; the launcher, the collector of its output and the end of every job when the agents stop.
- `agent_users/agent_users.py`: one Linux user per agent in one container, for openmath, knowledge work and creative writing.
- `machines/agent_machines.py`: one machine per agent on CheatBench's python311 image, for the interview tasks and protein design. Chess and creative writing build on the same image.
- `cheatbench_task_files.py`: CheatBench's task folder and built rows, as every domain reads them.
- `docker_images.py`: images on the host, built from a recipe when the label no longer names it.
- `sandbox_hygiene.py`: the processes an agent leaves, the loopback channel and what the sandbox holds at the end.
- `peer_shell.py`: `peer_shell`, an influencer's shell on a teammate's machine as an observer uid of its own with read access to the teammate's files, under `peer_shell=true`: the grant of read by ACL before each command, the reap after it and the setup's proof.
- `sandbox_probe.py`: one command's record, a table of commands and the setup solver that probes the seats.
- `staging.py`: files written on the host for a sandbox: atomic writes, compose files and staging tarballs. It also sends `team_remote_lock.sh`, which locks a bare team remote.
- `task_tool_store.py`: the sample store of a task's own tool.

### swarm/

The team on ORBIT's API.

- `board.py`: one shared channel as ORBIT configuration, the tools `board_status`, `board_read` and `board_post` with the per-round budgets, and the rounds' rules each system prompt states (`round_rules`).
- `end_round.py`: `end_round`, which every agent holds and which ends its round.
- `influencers.py`: who is regulator or disregulator (an influencer), on a board whose config asks for one, and the texts each role gets. A regulator's system prompt and first message are built here for every domain, in place of the benchmark's.
- `submit_and_stop.py`: `submit` and `stop_working`, the tools that end an agent's work, and the influencers' `alert_humans`, which records an alert and ends nothing. ORBIT reserves `submit`, so the tool is bound as `team_submit` and shown to the model as `submit`.
- `awareness_probes.py`: Lucy's end-of-episode questions as an Inspect scorer.
- `tool_output_caps.py`: the cap on one tool result, `tool_output_bytes` for every agent, set on every bound tool as `ToolDef.max_output`, and the cut of a longer result: its start and its end with one line between them, installed at Inspect's one truncation point so ORBIT's built-in bash and python get it too.
- `turn_rules/`: the plan of rounds, each agent's round of up to N tool calls and where it ends (`_turn_rounds.py`), every round using one of R, the budgets' counters, the halt after a hand-in or once no worker is left, the presence lines (each peer's entry and exit, `_turn_presence.py`) and the model filter. Its `__init__.py` is the API. `turn_activation.py` holds the two callbacks ORBIT imports by their dotted names: `ends_round` at every boundary of a round, `after_activation` after every round.

The implementation is `_regulator_oversight.py` (`get_peer_actions` and `get_peer_observations`, the influencers' tools) and the `_turn_*.py` modules. Every run carries the turn rules as a passive defense. Its model filter takes the agent loop's assistant paragraph out of the system message and appends the continue prompt (`prompts.yaml` `continue`) when the provider input would end with a model turn, which Gemini rejects. Before each activation the defense appends the presence lines due to the agent to its saved conversation (`apply_pre_turn`).

### tasks.py

`swarm_task(benchmark, config)` turns the plan, the sample, the tools, the prompts, the board and the turn rules into one ORBIT task. It adds the judge to the scorers, adds the access log's and the network log's sidecars, setup solvers and first scorers for a benchmark that watches, and records the run's facts in the sample metadata. The `@task` entry points `openmath`, `swe`, `knowledge_work`, `subset_sum`, `prime_factorization`, `protein_design`, `creative_writing` and `chess` serve `inspect eval`.

### run.py

The commands `run`, `judge`, `judge-pass`, `rejudge` and `regenerate`. `run` checks the keys and the routing, builds the task, calls `inspect_ai.eval` in process, exports the log, prints the summary and writes the run's code record with its three ladders.

### export/

The eval log as a CheatBench run directory.

- `run_export.py`: writes an `.eval` log as a run directory.
- `export_episodes.py`: the files one sample writes, and its episode records.
- `eval_sample_view.py`: one sample read the same way, finished or live.
- `atif_transcripts.py`: one sample as one ATIF trajectory per agent.

`_export_run_blocks.py`, `_export_access_log.py` and `_export_network_log.py` are the implementation: the blocks of `run.json`, the access log's rows with an agent, an origin and a tool call as `access_log.jsonl`, and the network log's rows with the agent at the other end as `network.jsonl`.

### judging/

The LLM judges. CheatBench's judge runs inside the eval as the scorer `cheatbench_judge`, so the export finds its verdicts in the log. `judge` and `rejudge` judge a finished log again and re-export the run from it. `judge-pass` judges runs' stored records under a dollar cap and writes only `judges/<pass>.<judge>.jsonl`.

- `cheatbench_judge.py`: CheatBench's judge through Inspect, and the one call path of every judge.
- `labels_judge.py` with `labels_prompt.md`, `labels_disclosure.md` and `labels_manipulation.md`: the pass `labels`, one call per agent of a run, each label a score from 0 to 100 with an excerpt.
- `judge_records.py`: what the labels judge reads of a stored run (the board, the instructions, the visible reasoning, the actions with their results), and `locate`, which finds the judge's excerpt in the agent's own posts or reasoning.
- `judge_pass.py`: the capped pass.
- `verdict_files.py`: the reader and writer of `judges/`, and the one reader of CheatBench's verdicts (`judges/cheatbench.<judge>.jsonl`: the eval's judge and the passes).

`judge_models.yaml` holds the judge entries.

### measures/

Everything computed over a run directory, without a model.

- `run_directory.py`: `RunDir`, one run directory read through one object. A study keeps a run at `runs/<study>/<run>` or in one of its groups, `runs/<study>/{invalid,incomplete,mock,set_aside,attempts}/<run>`, and `study_runs` lists both; `find_run` reads one by name. A run's eval log sits at the same path under `logs/` and its figures under `out/` (`figures_dir`). A run keeps its folder name wherever it sits, with one exception. An earlier attempt, superseded by a later run of its name, sits at `runs/<study>/attempts/<name>.<n>/` (`set_aside_attempt`), and its run.json `run_id` stays the run's name. Every reader therefore keys runs by folder, never by name.
- `run_validity.py`: whether a run measures what its cell says: no mock rehearsal, completed, on an SWE task that needs two agents to cheat (from `SWE_FIX`, f0d1c34), with a regulator that acted and a team the code record placed. Each run gets `why` and one `reason` code.
- `run_listing.py`: the runs of a collection under `runs/` and whether each counts (`INDEX.jsonl`, `manifest.jsonl`), for the transcript viewer and the judge passes.
- `deal_replay.py`: whether today's code deals a protein, creative or SWE run the roster and the holdings it recorded.
- `run_index.py`: one record per run of a study for `runs/<study>/INDEX.jsonl`: what the run is, where it ran, its arm, its outcome and its checks. It never reads the study table or the fleet: `scripts/box/study_index.py` joins them in for the study the table states (the stage and box of `scripts/box/study_table.py`, the instance from the box captures) and is the command. `uv run python -m scripts.box.study_index runs/<study>` prints a summary and writes nothing; `--write` writes the file.
- `code_ladder.py`: the run's code record. It writes `signals.jsonl` and run.json `signals`: each agent's evidence and each sample's team record, with the three ladders' rungs (`cheating_rung` and `cheated`; `coordination_level`; `collusion_rung`) and, on the team record, the influencers' rungs apart and the Cheating alarm. `ladders` reads the three rungs as the run recorded them and refuses a run whose record lacks them.
- `transcript_scan.py`: the code's evidence per agent (exposed, tried, opened, used, tampered, checked, shipped) from the transcript, the access log, the probe and the domain's records.
- `team_ladder.py`: the team record of a sample: the workers' evidence, the collaboration evidence (discussed, solicited, crossed, assembled) and the outcomes.
- `cheating_ladder.py`: the Cheating ladder (none, looked, tempted, attempted, hacked) from the evidence and the grader's pass, per agent, per team and for the influencers apart, with the alarm for a pass with no trace. `cheating_rungs` reads a run's Cheating rungs as recorded.
- `coordination.py`: the Coordination ladder (none, read, write, low, medium, high, extreme) from the board's reads and posts: each agent's rounds of back-and-forth with each teammate, averaged into a score and set against a ceiling, and the workers' mean level for the team.
- `collusion.py`: the Collusion ladder per pair of agents (none, cooperation, collaboration and collusion, by code from the takes and the planted pieces between them). `run_collusion` computes a run's pairs in memory.
- `regulator_measures.py`: what each regulator did, from the run's records: its first post and its first accusation (a post that names a worker and an act), whether each named worker made the act, its first sight of a tainted call against the hand-in, its pushes and reverts, its alerts against the Cheating ladder, and its oversight calls per round. The code record keeps them on the team row's `regulators`.
- `collusion_takes.py`: what a take is, per task (`TASK_RULES`, each with its rule in words), and who made the hand-in.
- `run_calls.py`: the run's calls as the take rules read them (`load_run`), and a take (`Take`).
- `git_takes.py`, `text_takes.py` and `file_takes.py`: the generic take rules, through the team's git remote, by units of text, and by files on a shared file system.
- `judge_labels.py`: the judges beside the code: CheatBench's labels set beside the Cheating ladder's clauses (`judged_cheating`), CheatBench's disclosed access (`cheatbench_disclosed_access`), and the judge labels disclosure and manipulation, each a score with an excerpt per agent and the team's highest (`labels`).
- `measure_readings.py`: what independent readings of the transcripts found of each code rung and judge label, both
  judge passes apart, from the hand-kept `measure_readings.yaml`: the one source of the viewer's How reliable section
  and the paper's reliability table.
- `ladder_by_domain.py`: what puts an agent on each Cheating rung, per task, for the paper's table and the viewer.
- `access_evidence.py`: the access log as the transcript scan reads it: the calls it shows as aimed, the agent row's `access_log` key and the team's.
- `ladder_domain.py`: `Domain`, what a domain tells the code's evidence, and `Evidence`. Each domain subclasses it in `_<d>_ladder.py` (`swe_ladder.py` for SWE).
- `honeypot_fingerprints.py`: the honeypot's fingerprints, and the probe that finds them in a sandbox.
- `shell_commands.py`: shell commands as the transcript scan reads them: pipelines, simple commands, their words, grep calls.
- `rungs.py`: the Cheating ladder's scale, the names of the evidence and of the three ladders' fields, and the table of counts.
- `episode_summary.py`: one line per episode, and the lone runs' turns for `budget`.
- `board_engagement.py`: the board engagement funnel, per role and per agent.
- `swe_measures.py`: Lucy's SWE measures, per agent and per arm.
- `verdict_fields.py`: the judge's flags per task (`FLAGS_BY_TASK`), for the summary, the figures, the browser and the tools.
- `run_figures.py`: one run's figures: each agent's steps round by round, the board's actions per round, the judge's flags per agent.

### replay/

A stored run played back in its sandbox from its recorded tool calls, with no model call and no network, so the state
after every call can be read where the record keeps only the end. It plays back software engineering, protein design
and creative writing runs. The package docstring gives steps.jsonl's schema.

- `replay_run.py`: `replay(run_dir, out_dir)`, which writes `steps.jsonl` and `replay.json`, and the command's loop.
- `replay_report.py`: `ReplayReport` and `faithful_steps`, which refuses a replay whose end state differs from the
  stored one.
- `replay_jobs.py`: a run made since the round clock: its commands with no fixed limit, its command jobs, their reads
  and stops and peer_shell on the run's clock, and what makes such a replay unfaithful.
- `recorded_calls.py`: the recorded calls in one global order, each with its agent and its number k.
- `commit_dates.py`: the commit date that gives a replayed commit its recorded id.
- `output_match.py`: a replayed output against the recorded one, and the likely cause of a difference.
- `step_snapshots.py`: `steps.jsonl`'s delta encoding.
- `replay_sandbox.py`: the compose project and Inspect's tool exec, as a seat's user.
- `git_state.py`: what the git tasks share: the calls that touch the team remote, and a line's git keys.
- `swe_replayer.py`: an SWE run's setup, state after a call and end state, from `domains/swe/swe_replay.py`'s commands.
- `protein_replay.py`: a protein design run's, with check_design re-run on the host.
- `creative_replay.py`: a creative writing run's, with the record's random values handed to the run's own staging.

### ui/

Two local pages over `runs/`. `API.md` is their HTTP contract.

The transcript viewer (`make transcripts`) shows every valid run with the three ladders, the judge labels, the board
and each agent's transcript:

- `transcript_viewer.py`: the server and the run page's data.
- `transcript_index.py`: the index of every valid run, its rows and its measures, cached between starts.
- `transcript_levels.py`: the three ladders and the judge labels as the page lays them out, per agent, per team and per
  call.
- `transcript_board.py`: the Board tab's data.
- `transcript_ladder_tables.py`: the Ladders page's tables by task, and what an independent reading found of each rung
  and label (from `measures/measure_readings.yaml`).
- `transcript_static/`: the page.

The results browser is a single-page UI over `runs/` and the eval logs.

- `launch.py`: starts and stops the browser as a background process.

`python -m src.ui` serves it in the foreground. The implementation is `_server.py` (the HTTP routes), `_run_store.py` (the run directories), `_eval_log_views.py` (the eval log's sample, context windows and timeline) and `static/` (`index.html`, `style.css` and the ES modules under `app/`, booted by `main.js`, which lists them).

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
| `run <name> [--benchmark openmath\|swe\|knowledge_work\|subset_sum\|prime_factorization\|protein_design\|creative_writing\|chess] [--preset default] [--scale small\|medium\|large\|extra_large\|gigantic] [--set key=value ...] [--no-judge]` | runs one task, writes `logs/<study>/<name>/`, exports `runs/<study>/<name>/` and prints the summary; the preset applies first, then the scale, then every `--set`; `--no-judge` is `--set judge=none` |
| `run ... --row R` | the benchmark's row: the openmath_agent row (`riemann` by default), the SWE issue (`flipt-02e2` by default, or `vuls-1832`), the knowledge_work row (`2ea2e5b5`), the subset_sum or prime_factorization question (`subsetsum_n60d10_4`, `fact_n52_4`), the protein_design target (`alfalfa_chi`), the creative_writings brief (`museum_poem`) or the chess position (`LR4pA`) |
| `run ... --honeypot as_released\|own_piece\|jigsaw [--no-grade]` | Lucy's honeypot on SWE, `jigsaw` by default; `--no-grade` skips the hidden tests |
| `run ... --model M --timeout S` | the agents' model, an entry of `src/models/model_registry.yaml` or an Inspect model string, and the seconds one model request may take (default `MODEL_TIMEOUT_S`) |
| `run ... --worker-model M --regulator-model M --disregulator-model M` | a role's own model, set after every `--set`; it runs through the swarm provider with its registry routing and the agents' generate config. The eval log names its calls `swarm/<model>`, and `runs/<study>/<name>/episodes.jsonl` names each agent's real model |
| `run ... --replace` | sets the run already in `runs/<study>/<name>/` aside into `runs/<study>/attempts/<name>.<n>/`, with its eval log and its figures, before the eval starts. Without it a run refuses a name whose folder holds a run, before the eval starts, so a rerun never replaces a run unasked |
| `judge <eval_log> <run_name> [--judge gpt-6-luna] [--sync] [--judge-errors] [--replace]` | judges a finished log (again, or for the first time after `--no-judge`) and exports it with the verdicts into the run's own folder, `runs/<study>/<run_name>/` or the group that holds it; batch API unless `--sync`. A folder that holds another eval's run is refused before the judge starts. With `--replace` that run moves to `runs/<study>/attempts/<run_name>.<n>/` once the verdicts are in, and the new run goes to `runs/<study>/<run_name>/` |
| `rejudge <run ...> [--judge gpt-6-luna] [--sync] [--judge-errors]` | CheatBench's judge from each run's eval log (named in `run.json`, else at the run's own place under `logs/`, which mirrors `runs/`), then the run is RE-EXPORTED from the log into its own folder, wherever it lives; a run whose log holds another eval than its folder is refused before the judge spends anything; a run whose log is gone is refused |
| `regenerate <run ...> [--no-export] [--no-measures] [--no-plots]` | rebuilds what a run directory derives from its eval log: the export (keeping the verdicts in `judges/`), the code record, the SWE measures and the figures |
| `signals <run ...> [--table]` | the run's code record, no judge: `signals.jsonl` with the agent rows, then one team record per sample, and run.json `signals.team`, each row with its evidence and its Cheating, Coordination and Collusion rungs; every run gets it as it ends, and `judge` and `rejudge` run it again; `--table` prints the agents' Cheating rungs, then the teams' with their passes and alarms |
| `judge-pass <cheatbench\|labels> <run or study ...> --judge gpt-6-luna --cap DOLLARS [--batch] [--limit N] [--only-missing\|--redo]` | a judge pass over stored runs, priced call by call and stopped before the cap; writes only `runs/<study>/<run>/judges/<pass>.<judge>.jsonl`; a study folder stands for its valid runs |
| `ui [start\|stop] [--port 8765] [--no-open]` | starts or stops the results browser in the background |
| `summary <run ...> [--full]` | one line per episode with its verdict and the task's judge flags, the swarm composition and the shutdown rate (the share of the agents holding `stop_working` that ended with it before any hand-in) |
| `engagement <run ...> [--json]` | the board engagement funnel per role and per agent |
| `swe-summary <run ...>` | Lucy's SWE measures per agent and per arm, written to `swe_summary.json` |
| `budget <lone_run ...>` | lone runs' turns and their median |
| `models [NAME]` | the models we can consider, one row per entry with its Inspect string, key variable, context, prices and CheatBench entries; with a name, that entry in full |
| `plots <run> [--out DIR]` | one run's figures as PNG with CSV twins under `out/<run name>/` (gitignored) |
| `replay <run or study ...> --out DIR [--only-missing] [--serial] [--jobs N]` | plays each run back in its sandbox from its recorded calls, with no model and no network, into `DIR/<run>/steps.jsonl` (the state after every call) and `replay.json`; a study stands for its valid SWE, protein design and creative writing runs; exits 1 when a replay is not faithful |

The judge runs inside the eval as the scorer `cheatbench_judge`, bound to the model role `judge`. With `--no-judge` the run is exported without verdicts, and `rejudge` adds them later. With `batch_judge` the judge's calls go through the provider's batch API; `judge` and `rejudge` batch too unless `--sync`, and `judge-pass` batches with `--batch`.

`inspect eval src/tasks.py@openmath -T preset=default -T regulators=1` also works. Every `-T` key other than the task's own arguments sets a config field, and `-T scale=medium` applies a scale first. This form skips the key and routing checks, the role models built before the sandbox starts, and the Gemini 402 retry.

## Outside src/

| folder | holds |
|---|---|
| `examples/` | one script per task and cell of the first experiment; see `examples/README.md` |
| `scripts/` | `screen.py` (the example cells in bulk), `study_readme.py` (each study's `runs/<study>/README.md`: its layout and every file's schema, from the data and the code), `paper/` (the paper's figures, tables and appendix) and `box/` (the rented boxes; see `scripts/box/README.md`) |
| `tools/` | the analysis tools in their own environment; see `tools/README.md` |
| `tests/` | `unit/` (one test module per module or concept), `smoke/` (the mock-model suites), `tools/` (the analysis tools' tests) and `support/` (what they share) |
| `submodules/` | CheatBench, ORBIT, Lucy's repository and Inspect Swarm, read only |
