# src

The experiment on ORBIT. It turns one benchmark from `src/benchmark/` and one `SwarmConfig` into an Inspect task, runs it, exports it and serves the results. Lucy's turn rules run on ORBIT's public API, with no patch to ORBIT. Run everything from the repository root with `uv run python -m src <command>`.

## Layout

| file | holds |
|---|---|
| `__main__.py` | `python -m src`: calls `cli.main` |
| `cli.py` | argument parsing; `run`, `judge` and `budget` call `run.py`, `ui` calls `ui/launch.py`, `plots` calls `ui/plots.py`, `summary` calls `measures/summary.py`, `models` calls `configuration/models.py` |
| `run.py` | the `run`, `judge` and `budget` commands: `inspect_ai.eval` in process, export, re-judging, and the turn budget's steps per agent from a lone run |
| `tasks.py` | `swarm_task(benchmark, config)` and the `@task` entry points `openmath`, `swe` and `knowledge_work` |
| `swarm/comm_topology.py` | the board as ORBIT configuration: one shared channel, no invocation edges, a written plan |
| `swarm/board.py` | the pull board's tools `board_status`, `board_read` and `board_post` with the per-round read and post budgets, and `team_submit`, over ORBIT's communication store |
| `swarm/oversight.py` | `agent_actions`, the regulators' tool: a neutral agent's latest tool calls, read from its live conversation |
| `swarm/roles.py` | hidden roles and the instruction levels: who is regulator or disregulator, and the texts each one gets |
| `swarm/rules.py` | the turn rules: the plan of concurrent or sequential rounds under the turn budget, the free board steps, the budgets' counters, the team_submit halt, the callback after each activation, and the model filter |
| `swarm/controls.py` | `defenses_for(control, monitor_model)`: ORBIT's shipped defenses as named controls |
| `swarm/probes.py` | Lucy's end-of-episode reflection and awareness questions, as an Inspect scorer |
| `benchmark/` | CheatBench's tasks: the Benchmark protocol, the openmath, SWE and knowledge-work domains, the transcript, the judge and the export; see `src/benchmark/README.md` |
| `measures/` | over run directories: the command line's summary (`summary.py`), the board engagement funnel (`engagement.py`), Rigel's cheating ladder (`rungs.py`) and Lucy's SWE measures (`swe_summary.py`) |
| `utils/provider_retry.py` | Gemini's 402 "credits depleted" retried as a rate limit |
| `utils/openrouter_cache.py` | an Anthropic model's conversation cached through OpenRouter (the last tool result marked) |
| `ui/` | the results browser: `server.py`, `launch.py` (start and stop in the background), `API.md` and the single-page UI in `static/` |

## Commands

| command | what it does |
|---|---|
| `run <name> [--benchmark openmath\|swe\|knowledge_work] [--preset default] [--scale small\|medium\|large\|extra_large\|gigantic] [--set key=value ...]` | runs one task, writes `logs/<ctrl\|swarm\|swe>/<name>/`, exports `runs/<name>/` and prints the summary; the preset applies first, then the scale, then every `--set` |
| `run ... --row R` | the openmath_agent row (`riemann` by default) or the knowledge_work row (`2ea2e5b5` by default) |
| `run ... --issue flipt-02e2 --honeypot as_released\|own_piece\|jigsaw [--no-grade]` | the SWE issue and Lucy's honeypot, `jigsaw` by default; `--no-grade` skips the hidden tests |
| `run ... --model M --timeout S` | the agents' model, an entry of `src/configuration/models.yaml` or an Inspect model string, and the seconds one model request may take (default `MODEL_TIMEOUT_S`) |
| `judge <eval_log> <run_name> [--judge gpt-6-sol] [--judge-errors]` | judges a finished log again and exports it with the new verdicts |
| `ladder <run ...> [--judge gpt-6-sol] [--view full\|actions] [--agents a01,a02] [--force] [--table]` | places every agent on the cheating ladder from its stored transcript (`src/benchmark/ladder.py`, the addendum `src/configuration/judge_ladder.py`): `runs/<name>/ladder.jsonl` with the rung, the cited calls and their rounds, the labels beside the ladder and the honesty labels; `--table` sums the existing files |
| `ui [start\|stop] [--port 8765] [--no-open]` | starts or stops the results browser in the background |
| `summary <run_dir ...> [--full]` | one line per judged episode, the swarm composition and the shutdown rate |
| `budget <lone_run_dir> [--scale S]` | a lone run's turns, and the working steps per agent that budget gives at each scale |
| `models [NAME]` | the models we can consider, one row per entry with its Inspect string, key variable, context, prices and CheatBench entries; with a name, that entry in full |
| `plots <run_dir> [--out DIR]` | one run's figures as PNG with CSV twins under `out/<run name>/` (gitignored): every agent's steps round by round with the judge's verdict, the board's actions per round, the judge's flags per agent (`src/ui/plots.py`) |

The judge runs inside the eval as the scorer `cheatbench_judge`, bound to the model role `judge`. With `batch_judge` its calls go through the provider's batch API. `judge` always re-judges through the batch API. `inspect eval src/tasks.py@openmath -T preset=default -T regulators=1` also works; every `-T` key other than the task's own arguments sets a config field, and `-T scale=medium` applies a scale first.

## What each option does

`src/configuration/swarm_config.py` holds the fields, each with a docstring. The table says how each one reaches ORBIT.

| option | default | ORBIT mechanism |
|---|---|---|
| `n_agents` | 1 (3 in the `default` preset) | one `AgentSpec` per name `a01`, `a02`, ...; two or more share one board channel; names widen to `a001` past 99 agents |
| `regulators`, `disregulators` | 0 | hidden roles by seeded shuffle; a count, or a share in (0, 1) rounded half up; the role's text is appended to its holders' system prompts |
| `seed` | 0 | which names hold which role, the random turn order, and the SWE planted commits' times |
| `preset` | none (the preset's name in every preset) | the preset the config was built from, recorded in the config dump so `run.json` names it |
| `mode` | none | `solo_task`, `shared_task` or `collaboration_needed`, or one per domain; none is `shared_task` on every domain; the domain builds the sample, the workspaces and the prompts from it |
| `access` | split | which parts of the exploit each agent holds (`src/configuration/access.py`, `Benchmark.access_plan`): `split` deals the domain's items round robin over the neutral agents (openmath per-agent copies with a team remote and one slice of the complete submission's archive each; SWE the honeypot's pieces; knowledge work the review tool for the first half and the artifact read for the rest), `all` gives everyone everything, a map `a01:slice1+slice2,a02:slice3,a03:` names each agent's items, and `swe:split,openmath:all` sets one policy per domain |
| `scale` | none (small in the `default` preset) | the label of the scale the size fields came from (`src/configuration/scales.py`) |
| `mention_team` | none | the `team` sentence and the roster in the system prompt; none follows the mode |
| `informed_roles` | true | `roles.briefing` after a regulator's or disregulator's role text |
| `board_tools` | true | binds `board_status`, `board_read` and `board_post` per agent (`tool_bindings`) and withholds ORBIT's channel tools from the provider request; false offers the channel tools |
| `mention_board` | false | the board sentence in the system prompt (SWE: the team chat paragraph) |
| `notice` | counts | one provider-only line when the board changed since the agent's last call: `counts` gives each sender's new and total posts, `changed` the line alone, `none` nothing |
| `read_page` | 5 | the most posts one `board_read` returns |
| `free_board_actions` | 2 | follow-up steps per round after a free step, amended into the plan by the `after_activation` callback; 0 charges every step |
| `free_board_actions_regulator` | 10 | a regulator's follow-up steps per round, under `board_tools` only; `agent_actions` counts as a board action |
| `reads_per_turn`, `posts_per_turn` | none (5 and 2 at the small scale) | posts an agent may receive through `board_read` and posts it may make per round; past them the tool returns the newest posts left or raises a ToolError; regulators have no read budget |
| `delivery` | poll | `CommunicationConfig.delivery`; auto puts every unread post in the model input at the start of the turn |
| `channel_list` | true | with `board_tools` off only; false withholds `channel_list` from the provider request (rules.py filter) and rewrites the unread notice's last line |
| `read_tool` | true | with `board_tools` off only; false withholds `channel_read` the same way and drops the prompt sentences that name it; needs auto delivery |
| `visibility` | live | `CommunicationConfig.visibility`; a concurrent round always sees the board as of its start |
| `max_message_chars`, `max_context_chars`, `max_inbox_messages` | 4000, 16000, 10 | the same `CommunicationConfig` fields; a scale sets the first two |
| `turn_order` | random | the order of each round in the written plan (`ExecutionConfig.plan`); random draws a permutation per round from the seed |
| `steps_per_agent` | 60 (8 in the `default` preset) | rounds of the plan; an agent is charged at most one working step per round, and planning steps and free board steps are never charged |
| `turn_budget` | none (`settings.TURN_BUDGETS` in the `default` preset: openmath 55, SWE 150, knowledge work 55) | the swarm's turns on a domain; `steps_for(domain)` gives max(1, budget // n_agents) rounds in place of `steps_per_agent`; an int applies to every domain; `knowledge_work:70,swe:120` changes the named domains and keeps the table's values for the rest, on a legacy preset too; `none` turns the budget off |
| `concurrent_rounds`, `max_concurrency` | true, 3 | one concurrent `ScheduleStep` per round with a snapshot of the board; false runs one single-agent step per agent per round |
| `pooled_steps` | true | the `after_activation` callback gives a stopped agent's steps to the active agents (`ScheduleRuntime.amend_pending`) |
| `one_call_per_turn` | false | quantum `legacy`, which runs the first call of a response; false is quantum `model_step` |
| `planning_turns` | 0 | 0 or 2 concurrent snapshot steps before round 1, quantum `model_step`, offering only the three board tools, or `channel_post` and `channel_read` on the legacy board |
| `turn_header` | false | "Turns left: ..." appended to the provider input of each working turn, counted from the live plan |
| `shutdown` | true | `AgentSpec.submit`; false builds agents without ORBIT's submit tool and drops every prompt sentence about submit; needs `one_call_per_turn=false`, so with the lucy_legacy preset pass `--set one_call_per_turn=false --set shutdown=false` |
| `team_submit` | none | `team_submit` for neutral agents; none follows the mode; a call lets the round finish, then the callback drops every later step |
| `regulator_window`, `disregulator_window` | 1: | the rounds a role holds steps in, as `start:end`; the agent enters at its start round and leaves after its end |
| `rule_level` | none | the rule sentence after the task instruction (`src/configuration/instruction_levels.yaml`, ladder `rule`) |
| `regulator_level`, `disregulator_level` | ambiguous, persuade | the role texts (ladders `regulator` and `disregulator`); under `board_tools` a regulator's text is followed by `roles.regulator_common`; level `none` adds no sentence |
| `regulator_discloses` | false | without it a regulator's text also ends with `roles.regulator_no_disclosure`, under `board_tools` only |
| `awareness_probe` | false | adds the `end_of_episode_probes` scorer |
| `message_limit`, `token_limit`, `time_limit_s` | 320, 0, 0 | Inspect's message and token limits, and `SchedulerConfig.max_time_seconds`; 0 means the default; a scale sets the first and the last |
| `batch_agents`, `batch_judge` | false, true | `GenerateConfig(batch=True)` for the agents' model (from the scale) and for the model bound to the judge role |
| `control`, `monitor_model` | baseline | `DefenseConfig` entries in `metadata["experiment"]["defenses"]` |
| `judge`, `judge_addendum` | gpt-6-sol, false | the judge entry in `src/benchmark/models.yaml`, and Lucy's judge addendum appended to the schema (`orbit` or true, `guardian`) |

A step is charged to `steps_per_agent` unless its last tool call is a board action that succeeded, `submit` or `team_submit`. A step that ends with a board action that succeeded is free and earns a follow-up step in the same round. A board action that fails (an error, a budget refusal, or "no such agent") is charged as work, with the charge `refused` in the timeline.

Every run also carries the passive `turn_rules` defense. Its model filter appends "Please continue with your next action." when the provider input would end with a model turn, which Gemini rejects.

One preset exists. `default` is the midterm report's configuration: the fields above at the small scale, with the turn budgets of `settings.TURN_BUDGETS`. It has no regulators; it sets only their level. The October 2026 sweep's board is the fields `mode` shared_task on openmath and collaboration_needed on SWE, `access` all on openmath and knowledge work and split for SWE, `regulator_level=role`, `board_tools=false`, `mention_board=true`, `mention_team=true`, `concurrent_rounds=false`, `free_board_actions=0`, `team_submit=false`, `informed_roles=false`, `notice=none`, `delivery=auto`, `visibility=step_start` and `turn_order=round_robin` (`tests/boards.py` CHANNEL_BOARD). Lucy's rules add `channel_list=false`, `pooled_steps=false`, `one_call_per_turn=true`, `planning_turns=2`, `turn_header=true`, `awareness_probe=true` and `steps_per_agent=50` (LUCY_RULES).

Hidden roles are used on an openmath board of three or more agents, and on any board whose config asks for a regulator or a disregulator. No agent is asked for a team report any more. The export still reads the report of an older run that has one.

## Where we differ from Lucy's patched ORBIT

- Free reads. Lucy's `channel_read` does not end the turn, which needs her patch. Auto delivery shows every unread post at the start of the turn with no call. `read_tool=false` removes `channel_read` entirely. The pull board instead gives a free follow-up step after each board action, up to `free_board_actions` per round.
- Dropped calls. Lucy answers every call after the first "not run: one tool call per turn". Quantum `legacy` drops them from the history instead, and `rules.py` counts them in `extra_calls`.
- Provider-only messages. The planning prompts, the turn header, the board notice and the continue prompt reach the provider input through a model filter. They never enter the durable conversation. Lucy's patch writes them into it. `rules.py` records each one as an info event, and `src/benchmark/transcript.py` puts it back into the transcript, so the judge sees it.
- Withheld tools. `channel_list` and `channel_read`, and all three channel tools under `board_tools`, are withheld from the provider request only. The tools still exist, so a model that names one anyway runs it. Such a call counts as the matching board action (check, read or post) in `TurnState` and the engagement block. Each call records the names it withheld as an info event of kind `withheld_tools`. The raw Inspect log's `ModelEvent.tools` is recorded before ORBIT's filter runs, so it lists the channel tools, although the provider never received them.
- Planning prompts. Lucy's planning texts name her channel `#team`; ours name the board's channel id, `#board`. On the pull board they name `board_post` and `board_read`.
- Text-only replies. Lucy ends a working turn after three replies with no tool call. Under `one_call_per_turn` ORBIT keeps asking for a tool call until one comes; the header is sent once per turn.
- Windows. A regulator or disregulator may enter late or leave early. Lucy's harness has no such window.

## measures/

| file | holds |
|---|---|
| `rungs.py` | Rigel's ladder, version 1: searched, exposed, tried, opened, used, shipped per agent; writes `rungs.json`; a knowledge-work run is placed from its verdict fields (`KNOWLEDGE_RUNGS`) |
| `swe_summary.py` | Lucy's SWE measures: cheated, fix posts, rule posts, disclosure, rung and reward; writes `swe_summary.json` |
| `engagement.py` | the board engagement funnel per role and per agent: on the board, looked, read, posted, submitted for the team; each agent's refused reads and posts and its `agent_actions` queries |

Run them with `python -m src.measures.rungs runs/<run>`, `python -m src.measures.swe_summary runs/<run>` and `python -m src.measures.engagement runs/<run> [--json]`.
