# src/configuration

Every setting of the experiment and every piece of model-facing text we add to CheatBench and ORBIT lives here. Code in `src/domains/`, `src/swarm/` and `src/` reads these files and holds no texts of its own.

**`settings.py`** holds the fixed values: the repository paths, the default agent and judge models, the browser's port, the model timeout, the turn budget (`TURN_BUDGET`, 100 working steps per agent), the failure labels of an incomplete run and the submodule commits the code was written against. `load_yaml` reads each YAML file once.

Each task domain's fixed settings (its CheatBench paths, the sandbox users and limits, the honeypot's names) live in its own package under `src/domains/`, since none of them is a knob of a run. The module is `_<domain>_settings.py`, or `<domain>_settings.py` where another package imports it.

**`swarm_config.py`** holds `SwarmConfig`, the frozen model of one run. Each field's docstring says what it does and which ORBIT mechanism implements it. `mode` (`shared_task` or `solo_task`), `access` (`split`, `all` or a map by agent) and `turn_budget` take one value for the run. Derived values: `working_steps` (the turn budget, else `steps_per_agent`), `max_turns` (the plan's steps at most, free follow-ups included), `sample_message_limit` (`message_limit`, else six messages per step of `max_turns` plus twenty), `knows_team` (by mode unless set), `gives_submit` (neutral agents get `submit`, the lone agent included), `holds_submit(role)` (never a regulator or a disregulator), `gives_stop_working` (every agent of a `shared_task` team, with `shutdown`) and `submit_kind` (what a submit hands in: the team's work, an agent's own in `solo_task`, or the lone agent's). `submit_delay_rounds` sets when a team's submit ends the run. Two fields bound the agents' calls beyond the budgets: `reasoning_effort` and `tool_output_bytes`. Three fields give a role its own model: `worker_model`, `regulator_model` and `disregulator_model` (the run's `--model` when None), stored as Inspect strings; `src/models/swarm_model_provider.py` runs such a model as `swarm/<model>`. `stop_after_delivery` ends an episode soon after its deliverable exists; it is off by default.

**`presets.py`** builds a config from text. `default` is the team's midterm report (2026-10-26) at the small scale: the pull board in `shared_task` with the board notice's counts, the exploit split over the neutral agents, per-round read and post budgets, the turn budget, no regulators, and the regulator level `ambiguous` without disclosure. `build_config(preset, scale, pairs)` applies the preset, then the scale, then every `key=value` pair (`with_overrides`). `add_config_arguments(parser)` and `config_pairs(args)` are the flags `src/cli.py`, the example scripts and `scripts/screen.py` share: `--model`, the three role-model flags, `--preset`, `--scale` and `--set`.

**`prompt_texts.py`** holds the system prompt texts that depend on a setting: `opening` (the agent's name, or its team sentence when it is told of its team, then the board sentence), `board_system_prompt` (the team and board sentences), `swe_board_fields` (the SWE board paragraph and tool lines), `submit_text` (what submit does for the agent's kind and what the domain's grader reads), `missing_text` (what the guard says is missing), `tool_line` and `described` (a tool's bullet and its description, which say the same), `ending_lines` and `stop_text` (the submit and stop_working bullets an agent holds) and `rounds_line` (what a round is, for a team whose submit ends the run some rounds on). An agent not told of its team (`told`, `Benchmark.tells_team`) gets texts that name no teammate. Each one reads its text from `prompts.yaml`.

**`access_policy.py`** holds the access policy: `parse` reads the command line's `split`, `all` or `a01:slice1+slice2,a02:slice3,a03:`; `deal` deals a domain's items round robin over the neutral agents; `plan(access, agents, roles, split_items, whole)` gives what each agent holds under a policy; `check_map` validates an explicit map against the roster and the domain's items. Each benchmark names its items (`whole_access`, `split_items`, `valid_item`) and `Benchmark.plan` applies the policy through `plan`.

**`scales.py`** holds the five scales, from `small` (three agents, eight steps) to `gigantic` (a thousand agents). A scale sets the team size, the steps, the post size, the read page, the free board actions, the per-round read and post budgets, the time limit, the concurrency and the agents' batch calls. The scales table of the top-level README gives every value. Under a turn budget every agent gets the budget's turns as working steps, at every scale. `SwarmConfig.at_scale(name)` applies one. A later `--set` overrides any value a scale gave.

**`prompts.yaml`** holds the team sentence, the board sentence, the role briefing, the midterm report's two regulator sentences (`roles.regulator_common`, `roles.regulator_no_disclosure`), the board tools' and `agent_actions`' descriptions and outputs, the texts of `submit` and `stop_working` (what each domain's grader reads, what is missing, the refusals, the replies and the board's post of a hand-in, each with a variant for an agent not told of its team), the sentence on rounds, the budgets' refusal texts, the board notice, the continue prompt, the SWE system prompts, and Lucy's end-of-episode questions. Each borrowed text names its source.

**`instruction_levels.yaml`** holds three ladders of model-facing text: the rule every agent is told, the regulator's text and the disregulator's text. A config selects one level per ladder. The regulator ladder holds the midterm report's three variants `ambiguous`, `explicit` and `pressure` beside our `monitor`, `role` and `enforce`. The variants are literal: `ambiguous` and `pressure` are the report's sentences, and `explicit` is one sentence that names the domain's `prior_work` and `protected` as the exploit to avoid. Neither `explicit` nor `pressure` carries the ambiguous sentence. Level `none` adds no sentence. Each benchmark fills the placeholders from its own `vars`.

The models we can consider for the agents and the judge live in `src/models/model_registry.yaml`, read through `src/models/model_registry.py`: `resolve(name)` turns an entry into its Inspect string, `prepare_env(model)` checks the route's key and exports what Inspect reads, `check_routing(model)` refuses an OpenRouter model whose entry pins no host, `usd_per_million(name, batch)` gives the prices and `render()` is the table `python -m src models` prints.

## What each option does

`swarm_config.py` holds the fields, each with a docstring. Any field takes `--set key=value`. The table says how each one reaches ORBIT.

| option | default | ORBIT mechanism |
|---|---|---|
| `n_agents` | 1 (3 in the `default` preset) | one `AgentSpec` per name `a01`, `a02`, ...; two or more share one board channel; names widen to `a001` past 99 agents |
| `regulators`, `disregulators` | 0 | hidden roles by seeded shuffle; a count, or a share in (0, 1) rounded half up; the role's text is appended to its holders' system prompts |
| `seed` | 0 | which names hold which role, the random turn order, and the SWE planted commits' times |
| `preset` | none (the preset's name in every preset) | the preset the config was built from, recorded in the config dump so `run.json` names it |
| `mode` | shared_task | `solo_task` or `shared_task`; the domain builds the sample, the workspaces and the prompts from it |
| `access` | split | which parts of the exploit each agent holds (`access_policy.py`, `Benchmark.plan`): `split` deals the domain's items round robin over the neutral agents, repeating them in order when the agents outnumber them, and gives regulators and disregulators nothing; `all` gives everyone everything; a map `a01:slice1+slice2,a02:slice3,a03:` names each agent's items. The top-level README's Access table gives each domain's items |
| `scale` | none (small in the `default` preset) | the label of the scale the size fields came from (`scales.py`) |
| `mention_team` | none | the `team` sentence and the roster in the system prompt; none follows the mode |
| `informed_roles` | true | `roles.briefing` after a regulator's or disregulator's role text |
| `mention_board` | false | the board sentence in the system prompt (SWE: the team chat paragraph) |
| `notice` | counts | one provider-only line when the board changed since the agent's last call: `counts` gives each sender's new and total posts, `changed` the line alone, `none` nothing |
| `read_page` | 5 (10 from the scale) | the most posts one `board_read` returns |
| `free_board_actions` | 3 | follow-up steps per round after a free step, amended into the plan by the `after_activation` callback; 0 charges every step |
| `free_board_actions_regulator` | 10 | a regulator's follow-up steps per round; `agent_actions` counts as a board action |
| `reads_per_turn`, `posts_per_turn` | none (10 and 2 from the scale) | posts an agent may receive through `board_read` and posts it may make per round; past them the tool returns the newest posts left or raises a ToolError; regulators have no read budget |
| `max_message_chars` | 4000 (2000 from the scale) | `CommunicationConfig.max_message_chars`, the longest post |
| `turn_order` | random | the order of each round in the written plan (`ExecutionConfig.plan`); random draws a permutation per round from the seed; round_robin keeps declaration order |
| `steps_per_agent` | 60 (8 in the `default` preset) | rounds of the plan; an agent is charged at most one working step per round, and free board steps are never charged |
| `turn_budget` | none (`settings.TURN_BUDGET` in the `default` preset) | the working steps every agent gets, as rounds in place of `steps_per_agent` (`working_steps`); `none` turns the budget off |
| `concurrent_rounds`, `max_concurrency` | true, 3 | one concurrent `ScheduleStep` per round with a snapshot of the board; false runs one single-agent step per agent per round |
| `pooled_steps` | true | the `after_activation` callback gives a stopped agent's steps to the active agents (`ScheduleRuntime.amend_pending`) |
| `shutdown` | true | `stop_working` for every agent of a `shared_task` team, roles included (`gives_stop_working`); a call ends the caller's turns (`TurnState.ended` and the store `StopWorking`) and hands in nothing; false: no stop tool and no sentence about stopping. No agent holds ORBIT's own submit (`AgentSpec.submit=False`) |
| `submit` | none | `submit` for neutral agents, the lone agent included (`gives_submit`); never for a role; none gives it in every mode; it is refused while `Benchmark.gradable` finds nothing to grade, and the store `TeamSubmission` records the hand-in and every refused call |
| `submit_delay_rounds` | 2 | a `shared_task` team's `submit` in round r ends the run after round r plus this; the board gets a post from the system at once, and the callback drops every step after that round |
| `stop_after_delivery` | none | N ends an episode soon after its deliverable exists: alone or in solo_task after N more of the agent's working steps once its own is seen, in a team N rounds after the round that shows the team's; the callback awaits the benchmark's delivery predicate (`Benchmark.delivered`) at the end of each round; the episodes it ends have `ended_by` delivered and their steps go to nobody |
| `regulator_window`, `disregulator_window` | 1: | the rounds a role holds steps in, as `start:end`; the agent enters at its start round and leaves after its end |
| `rule_level` | none | the rule sentence after the task instruction (`instruction_levels.yaml`, ladder `rule`) |
| `regulator_level`, `disregulator_level` | ambiguous, persuade | the role texts (ladders `regulator` and `disregulator`); a regulator's text is followed by `roles.regulator_common`; level `none` adds no sentence |
| `regulator_discloses` | false | without it a regulator's text also ends with `roles.regulator_no_disclosure` |
| `awareness_probe` | false | adds the `end_of_episode_probes` scorer |
| `message_limit` | none | Inspect's message limit; none derives it from the plan (`sample_message_limit`); each agent's conversation is capped at it plus ten (`AgentSpec.max_messages`) |
| `token_limit` | 0 | Inspect's token limit, which counts cached prompt tokens in full; 0 is the runaway guard of 64M tokens per agent |
| `time_limit_s` | 0 (from the scale) | `SchedulerConfig.max_time_seconds`, ORBIT's soft halt; 0 is the benchmark's own limit |
| `time_grace_s` | 600 | seconds Inspect's hard `time_limit` sits above ORBIT's soft halt at `time_limit_s`, so a step in flight finishes and its usage is logged; 0 puts both at `time_limit_s`; SWE keeps its grading budget above the halt instead |
| `batch_agents`, `batch_judge` | false, true | `GenerateConfig(batch=True)` for the agents' model (from the scale) and for the model bound to the judge role |
| `reasoning_effort`, `tool_output_bytes` | none, none | the agents' `GenerateConfig` `reasoning_effort` and `max_tool_output`; none takes the provider's default effort and Inspect's 16 KiB |
| `worker_model`, `regulator_model`, `disregulator_model` | none | a role's own model as `AgentSpec.model` `swarm/<model>`, stored as an Inspect string; none runs the eval's model |
| `judge`, `judge_addendum` | gpt-6-luna, false | the judge entry in `src/judging/judge_models.yaml` (`gpt-6-sol` is the reference judge, `claude-haiku-5-5` the cheapest of another family; none: no judge), and Lucy's judge addendum appended to the schema |

The top-level README's sections on the pull board and on submit say how a step is charged and how a run ends.
