# src

The experiment on ORBIT. It turns one benchmark from `src/domains/` and one `SwarmConfig` into an Inspect task, runs it, exports it and serves the results. The turn rules run on ORBIT's public API, with no patch to ORBIT. Run everything from the repository root with `uv run python -m src <command>`.

## Layout

| file | holds |
|---|---|
| `__main__.py` | `python -m src`: calls `cli.main` |
| `cli.py` | argument parsing, and every command: `run`, `judge`, `ladder`, `rejudge` and `regenerate` call `run.py`; `signals`, `summary`, `budget`, `engagement`, `swe-summary` and `plots` call `measures/`; `ui` calls `ui/launch.py`; `models` calls `models/model_registry.py` |
| `run.py` | the `run`, `judge`, `ladder`, `rejudge` and `regenerate` commands: `inspect_ai.eval` in process, the export, re-judging, the judged ladder, and a run directory rebuilt from its log |
| `tasks.py` | `swarm_task(benchmark, config)` and the `@task` entry points `openmath`, `swe`, `knowledge_work`, `subset_sum`, `prime_factorization` and `protein_design` |
| `configuration/` | every setting and model-facing text: `swarm_config.py` (`SwarmConfig`, the preset, `build_config`), `prompt_texts.py` (the texts that depend on a setting: the submit and stop_working lines, the board system prompt, the SWE board fields, the role briefing and the board notice), `settings.py`, the scales, the access policy and the YAML texts; see `src/configuration/README.md` |
| `swarm/board.py` | the board as ORBIT configuration (one shared channel, no invocation edges, a written plan) and its tools `board_status`, `board_read` and `board_post` with the per-round read and post budgets, over ORBIT's communication store |
| `swarm/submit_and_stop.py` | `submit` (bound as `team_submit`, which ORBIT reserves, and shown to the model as `submit`) and `stop_working`: the guard, the hand-in record, the board's notice of the hand-in, and the end of the caller's turns |
| `swarm/regulator_oversight.py` | `agent_actions`, the regulators' tool: a teammate's latest tool calls, read from its live conversation |
| `swarm/hidden_roles.py` | hidden roles and the instruction levels: who is regulator or disregulator, and the texts each one gets |
| `swarm/turn_rules/` | the turn rules (`__init__.py` is the API): the plan of concurrent or sequential rounds under the turn budget, the free board steps, the budgets' counters, the halt after the team's hand-in, the callback after each activation, and the model filter, which also takes ORBIT's assistant paragraph out of the system message (`provider_system_text`) |
| `swarm/awareness_probes.py` | Lucy's end-of-episode reflection and awareness questions, as an Inspect scorer |
| `domains/` | CheatBench's tasks: the Benchmark protocol and the openmath, SWE, knowledge-work, interview (subset_sum, prime_factorization) and protein-design domains; see `src/domains/README.md` |
| `sandbox/` | what every domain builds from: the agents' tools and seats, their sandbox users, the per-agent machines layer (`machines/`), CheatBench's task files, the Docker images, the staging, the task tools' store, the hygiene and the end-of-run probe; see `src/domains/README.md` |
| `judging/` | CheatBench's judge as an Inspect scorer (`cheatbench_judge.py`), its models (`judge_models.yaml`) and the judged ladder (`judging_ladder.py`, with the addenda `ladder_verdict_fields.py` and `presentation_verdict_fields.py`) |
| `export/` | the eval log as a CheatBench run directory: the sample view, the ATIF transcripts, the episodes and the blocks of `run.json` |
| `models/` | the model registry (`model_registry.py`, `model_registry.yaml`), the swarm provider that runs a role's own model (`swarm_model_provider.py`) and Gemini's 402 retried as a rate limit (`gemini_402_retry.py`) |
| `measures/` | over run directories (`run_directory.py`, `RunDir`): the episode summary and the lone runs' turns (`episode_summary.py`), the board engagement funnel (`board_engagement.py`), the code ladder (`code_ladder.py`), the team record (`team_ladder.py`) and the scales they share with the judged ladder (`rungs.py`), the judge's flags per task (`verdict_fields.py`), Lucy's SWE measures (`swe_measures.py`), the figures (`run_figures.py`) |
| `utils/` | CheatBench's `core` package on the import path (`cheatbench_core.py`), the JSON readers (`json_files.py`) and the figures' style (`figure_style.py`) |
| `ui/` | the results browser: `server.py` (the HTTP routes), `run_store.py` (the run directories it reads), `eval_log_views.py` (the eval log's sample, context windows and timeline), `launch.py` (start and stop in the background), `API.md` and the single-page UI in `static/` (`index.html`, `style.css` and the ES modules under `static/app/`, booted by `main.js`) |

## Commands

| command | what it does |
|---|---|
| `run <name> [--benchmark openmath\|swe\|knowledge_work\|subset_sum\|prime_factorization\|protein_design] [--preset default] [--scale small\|medium\|large\|extra_large\|gigantic] [--set key=value ...]` | runs one task, writes `logs/<ctrl\|swarm\|swe>/<name>/`, exports `runs/<name>/` and prints the summary; the preset applies first, then the scale, then every `--set` |
| `run ... --row R` | the openmath_agent row (`riemann` by default), the knowledge_work row (`2ea2e5b5` by default), the subset_sum or prime_factorization question (`subsetsum_n60d10_4`, `fact_n52_4` by default) or the protein_design target (`alfalfa_chi` by default) |
| `run ... --issue flipt-02e2 --honeypot as_released\|own_piece\|jigsaw [--no-grade]` | the SWE issue and Lucy's honeypot, `jigsaw` by default; `--no-grade` skips the hidden tests |
| `run ... --model M --timeout S` | the agents' model, an entry of `src/models/model_registry.yaml` or an Inspect model string, and the seconds one model request may take (default `MODEL_TIMEOUT_S`) |
| `run ... --worker-model M --regulator-model M --disregulator-model M` | a role's own model, set after every `--set`; it runs through the swarm provider (`models/swarm_model_provider.py`) with its registry routing and the agents' generate config. The eval log names its calls `swarm/<model>`, and `runs/<name>/episodes.jsonl` names each agent's real model |
| `judge <eval_log> <run_name> [--judge gpt-6-luna] [--sync] [--judge-errors]` | judges a finished log (again, or for the first time after `--no-judge`) and exports it with the verdicts; batch API unless `--sync` |
| `rejudge <run ...> [--judge gpt-6-luna] [--sync] [--no-judge] [--no-ladder] [--view full\|actions] [--judge-errors]` | CheatBench's judge from each run's eval log (named in `run.json`), then the ladder over its transcripts; a run whose log is gone gets the ladder only |
| `regenerate <run ...> [--no-export] [--no-measures] [--no-plots]` | rebuilds what a run directory derives from its eval log: the export (keeping the verdicts in `judge.jsonl`), the code ladder, the SWE measures and the figures |
| `signals <run ...> [--table]` | places every agent and every team on the cheating ladder by code, no judge (`src/measures/code_ladder.py` and `team_ladder.py`: the honeypot's fingerprints in tool results, posts and, from the end-of-run probe, on disk; the board; the hand-in): `signals.jsonl` with the agent rows, then one team record per sample, and run.json `signals.team`; every run gets it as it ends, and `judge` and `ladder` run it again; `--table` prints the agents' table, then the teams' |
| `ladder <run ...> [--judge gpt-6-luna] [--sync] [--view full\|actions] [--agents a01,a02] [--force] [--table]` | places every agent on the cheating ladder from its stored transcript (`src/judging/judging_ladder.py`, the addenda `src/judging/ladder_verdict_fields.py` and, where the team reached checked or shipped, `presentation_verdict_fields.py`): `runs/<name>/ladder.jsonl` with the rung, the cited calls and their rounds, pushback and forbidden method, the honesty labels and the presentation, then one team row per sample; `--table` sums the existing files |
| `ui [start\|stop] [--port 8765] [--no-open]` | starts or stops the results browser in the background |
| `summary <run ...> [--full]` | one line per episode with its verdict and the task's judge flags, the swarm composition and the shutdown rate (the share of the team's agents that ended with `stop_working`) |
| `engagement <run ...> [--json]` | the board engagement funnel per role and per agent (`src/measures/board_engagement.py`) |
| `swe-summary <run ...>` | Lucy's SWE measures per agent and per arm, written to `swe_summary.json` (`src/measures/swe_measures.py`) |
| `budget <lone_run ...>` | lone runs' turns and their median |
| `models [NAME]` | the models we can consider, one row per entry with its Inspect string, key variable, context, prices and CheatBench entries; with a name, that entry in full |
| `plots <run> [--out DIR]` | one run's figures as PNG with CSV twins under `out/<run name>/` (gitignored): every agent's steps round by round with the judge's verdict, the board's actions per round, the judge's flags per agent (`src/measures/run_figures.py`) |

The judge runs inside the eval as the scorer `cheatbench_judge`, bound to the model role `judge`, unless the config's `judge` is none (`run --no-judge`), in which case the run is exported without verdicts and `rejudge` adds them later. With `batch_judge` its calls go through the provider's batch API; `judge` and `ladder` batch too unless `--sync`. `inspect eval src/tasks.py@openmath -T preset=default -T regulators=1` also works; every `-T` key other than the task's own arguments sets a config field, and `-T scale=medium` applies a scale first.

## What each option does

`src/configuration/swarm_config.py` holds the fields, each with a docstring. The table says how each one reaches ORBIT.

| option | default | ORBIT mechanism |
|---|---|---|
| `n_agents` | 1 (3 in the `default` preset) | one `AgentSpec` per name `a01`, `a02`, ...; two or more share one board channel; names widen to `a001` past 99 agents |
| `regulators`, `disregulators` | 0 | hidden roles by seeded shuffle; a count, or a share in (0, 1) rounded half up; the role's text is appended to its holders' system prompts |
| `seed` | 0 | which names hold which role, the random turn order, and the SWE planted commits' times |
| `preset` | none (the preset's name in every preset) | the preset the config was built from, recorded in the config dump so `run.json` names it |
| `mode` | shared_task | `solo_task` or `shared_task`; the domain builds the sample, the workspaces and the prompts from it |
| `access` | split | which parts of the exploit each agent holds (`src/configuration/access_policy.py`, `Benchmark.access_plan`): `split` deals the domain's items round robin over the neutral agents, repeating them in order when the agents outnumber them (openmath per-agent copies with a team remote and one slice of the complete submission's archive each; SWE the honeypot's pieces; knowledge work the review tool for the first half and the artifact read for the rest; the interview tasks one shard or share of the answer key each; protein design one segment of every planted design each, `segment1`..`segmentN`) and gives regulators and disregulators nothing, `all` gives everyone everything, and a map `a01:slice1+slice2,a02:slice3,a03:` names each agent's items |
| `scale` | none (small in the `default` preset) | the label of the scale the size fields came from (`src/configuration/scales.py`) |
| `mention_team` | none | the `team` sentence and the roster in the system prompt; none follows the mode |
| `informed_roles` | true | `roles.briefing` after a regulator's or disregulator's role text |
| `mention_board` | false | the board sentence in the system prompt (SWE: the team chat paragraph) |
| `notice` | counts | one provider-only line when the board changed since the agent's last call: `counts` gives each sender's new and total posts, `changed` the line alone, `none` nothing |
| `read_page` | 5 | the most posts one `board_read` returns |
| `free_board_actions` | 3 | follow-up steps per round after a free step, amended into the plan by the `after_activation` callback; 0 charges every step |
| `free_board_actions_regulator` | 10 | a regulator's follow-up steps per round; `agent_actions` counts as a board action |
| `reads_per_turn`, `posts_per_turn` | none (10 and 2 from the scale) | posts an agent may receive through `board_read` and posts it may make per round; past them the tool returns the newest posts left or raises a ToolError; regulators have no read budget |
| `max_message_chars` | 4000 | `CommunicationConfig.max_message_chars`, the longest post; a scale sets it |
| `turn_order` | random | the order of each round in the written plan (`ExecutionConfig.plan`); random draws a permutation per round from the seed; round_robin keeps declaration order |
| `steps_per_agent` | 60 (8 in the `default` preset) | rounds of the plan; an agent is charged at most one working step per round, and free board steps are never charged |
| `turn_budget` | none (`settings.TURN_BUDGET` in the `default` preset: 100, two thirds of CheatBench's 150-turn bound) | the working steps every agent gets, as rounds in place of `steps_per_agent` (`working_steps`); `none` turns the budget off |
| `concurrent_rounds`, `max_concurrency` | true, 3 | one concurrent `ScheduleStep` per round with a snapshot of the board; false runs one single-agent step per agent per round |
| `pooled_steps` | true | the `after_activation` callback gives a stopped agent's steps to the active agents (`ScheduleRuntime.amend_pending`) |
| `shutdown` | true | `stop_working` for every agent of a `shared_task` team, roles included (`gives_stop_working`); a call ends the caller's turns (`TurnState.ended` and the store `StopWorking`) and hands in nothing; false: no stop tool and no sentence about stopping. No agent holds ORBIT's own submit (`AgentSpec.submit=False`) |
| `submit` | none | `submit` for neutral agents, the lone agent included (`gives_submit`); never for a role; none gives it in every mode; it is refused while `Benchmark.gradable` finds nothing to grade, and the store `TeamSubmission` records the hand-in and every refused call |
| `submit_delay_rounds` | 2 | a `shared_task` team's `submit` in round r ends the run after round r plus this; the board gets a post from the system at once, and the callback drops every step after that round |
| `stop_after_delivery` | none | N ends an episode soon after its deliverable exists: alone or in solo_task after N more of the agent's working steps once its own is seen, in a team N rounds after the round that shows the team's; the callback awaits the benchmark's delivery predicate (`Benchmark.delivered`) at the end of each round; the episodes it ends have `ended_by` delivered and their steps go to nobody |
| `regulator_window`, `disregulator_window` | 1: | the rounds a role holds steps in, as `start:end`; the agent enters at its start round and leaves after its end |
| `rule_level` | none | the rule sentence after the task instruction (`src/configuration/instruction_levels.yaml`, ladder `rule`) |
| `regulator_level`, `disregulator_level` | ambiguous, persuade | the role texts (ladders `regulator` and `disregulator`); a regulator's text is followed by `roles.regulator_common`; level `none` adds no sentence |
| `regulator_discloses` | false | without it a regulator's text also ends with `roles.regulator_no_disclosure` |
| `awareness_probe` | false | adds the `end_of_episode_probes` scorer |
| `message_limit`, `token_limit`, `time_limit_s` | 320, 0, 0 | Inspect's message and token limits, and `SchedulerConfig.max_time_seconds`; 0 means the default; a scale sets the first and the last |
| `time_grace_s` | 600 | seconds Inspect's hard `time_limit` sits above ORBIT's soft halt at `time_limit_s`, so a step in flight finishes and its usage is logged; 0 puts both at `time_limit_s`; SWE adds its grading budget instead |
| `batch_agents`, `batch_judge` | false, true | `GenerateConfig(batch=True)` for the agents' model (from the scale) and for the model bound to the judge role |
| `judge`, `judge_addendum` | gpt-6-luna, false | the judge entry in `src/judging/judge_models.yaml` (`gpt-6-sol` is the reference judge, `claude-haiku-5-5` the cheapest of another family; none: no judge), and Lucy's judge addendum appended to the schema |

A step is charged to `steps_per_agent` unless its last tool call is a board action, a `submit` or a `stop_working` that succeeded. A step that ends with a board action that succeeded is free and earns a follow-up step in the same round. A board action, `submit` or `stop_working` that fails (an error, a budget refusal, the submit guard's refusal, or "no such agent") is charged as work, with the charge `refused` in the timeline. The timeline's charge is `submit` or `stop` for one that went through.

Every run also carries the passive `turn_rules` defense. Its model filter takes ORBIT's assistant paragraph out of the system message and appends the continue prompt (`prompts.yaml` `continue`) when the provider input would end with a model turn, which Gemini rejects. Both reach the provider only; the transcripts and the browser show them as the provider received them.

One preset exists. `default` is the midterm report's configuration: the fields above at the small scale, with the turn budget `settings.TURN_BUDGET`. It has no regulators; it sets only their level.

Hidden roles are used on an openmath board of three or more agents, and on any board whose config asks for a regulator or a disregulator.

## measures/

| file | holds |
|---|---|
| `run_directory.py` | `RunDir`: one run directory's `run.json` (read once, and again after one block is written), episodes, verdicts, the ladders' agent rows and team rows, the board and the transcripts |
| `rungs.py` | the ladder's scales in order (the honeypot rungs, the collaboration levels, the presentation labels, the endings), the highest level a row holds, the tool calls as CheatBench's renderer numbers them, and the table of counts per run that both ladders print |
| `episode_summary.py` | `EpisodeSummary`, one episode's line from its record and its verdict; `summary_lines` for a run; the lone runs' turns for `budget` |
| `code_ladder.py` | the code ladder's API: exposed, tried, opened, used, checked and shipped per agent from the transcript (pointers, aimed commands with copies followed, distinctive lines and bytes in results and read posts, the pieces seen, the call that joined the whole exploit), the probe's report in `run.json` `honeypot_probe` and the domain's records; writes `signals.jsonl` and run.json `signals`; the agents' table adds the agreement with the judge, and the teams' table counts rungs and collaboration levels |
| `team_ladder.py` | the team record of a sample: the team's rungs from every agent row and the team's shared work, raised by what a pass proves; the collaboration level from the board's posts and the domain's crossed and assembled; the judged presentation, pushback and forbidden method; the outcomes (passed, ended by, the hand-in, refused submits, shutdown rate) |
| `ladder_domain.py` | `Domain`, what a domain tells the code ladder (each domain's subclass is its package's `<domain>_ladder.py`): the honeypot's pointers and pieces, aimed commands, the agent's deliverable, the team's work and the hand-in, the feedback grader, what a post mentions, crossed and assembled, passed and what a pass proves; `Evidence`, and the fingerprint cache |
| `shell_commands.py` | the code ladder's shell parsing: pipelines and simple commands outside quotes, their words and written files, grep and rg calls, count-only probes, distinctive line hits |
| `board_engagement.py` | the board engagement funnel per role and per agent: on the board, looked, read, posted, called submit; each agent's refused reads and posts and its `agent_actions` queries |
| `verdict_fields.py` | the judge's flags per task (`FLAGS_BY_TASK`) with their labels and colours, for the summary, the figures and the browser |
| `swe_measures.py` | Lucy's SWE measures: cheating, posts about the fix, disclosure, rungs and rewards per agent and per arm |
| `run_figures.py` | one run's figures: each agent's steps round by round (work, board, submit, stop_working), the board's actions per round, the judge's flags per agent |

Every one runs through the command line: `python -m src signals`, `summary`, `engagement`, `swe-summary` and `budget`.
