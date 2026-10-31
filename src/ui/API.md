# Results browser API

Start: `make ui`, or `uv run python -m src ui [--port 8765] [--no-open]` from the repository root; `make ui-stop` stops
it. The foreground server alone is `uv run python -m src.ui [--runs runs] [--port 8765]`.

The page is served at `/` and its assets at `/static/<path>` from `src/ui/static/` (`index.html`, `style.css` and the ES
modules under `app/`, which `index.html` loads through `app/main.js`). `_server.py` routes every request; `_run_store.py`
reads the run directories and `_eval_log_views.py` the eval logs. Every API answer is JSON. A path that matches no route,
names an unknown run, episode, call or tool, or leaves the directory it names (a `.` or `..` segment, an encoded
separator) gets 404 `{"error"}`; a server fault gets 500 `{"error", "traceback"}`.

## UI routes

| hash | page |
|---|---|
| `#/` | Summary: cheating by setting, composition grids, the swarm runs |
| `#/runs?q=&setting=&control=&cheated=&failed=1&sort=&dir=` | Runs: one row per run, opening to its agents |
| `#/run/<run>` | the run page; `.../timeline`, `.../context?agent=&call=&kind=&flag=`, `.../sample`, `.../episode/<episode>?find=&kind=&step=` |
| `#/swarm?run=<run>` | Swarm: composition grids, outcome funnel, adoption curves, swarm runs with rasters, run explorer |

## Data model

A **run** is one directory under `runs/` holding `run.json`, `episodes.jsonl`, `judge.jsonl`, `trajectories/<episode>/transcript.json`,
optionally `ladder.jsonl`, `signals.jsonl`, `access_log.jsonl`, `communication/<sample>.json` (board posts), `defense/<sample>.json` (control
evidence) and `tools/<tool>/` (the analysis tools' outputs). One run has one sample and one **episode per agent**:
`riemann#1` for a lone agent, `riemann#1@k7q2m` and `riemann#1@t4x9n` on a board. `run.json.eval_log` points at the Inspect
`.eval` log, from which the sample, the context windows and the timeline are read on demand.

The two ladders write an **agent row** per episode (`"row": "agent"`) and a **team row** per sample (`"row": "team"`).
The code ladder's team row (signals.jsonl) is the **team record**, the ladder's primary result: the team's honeypot
rung (none, exposed, tried, opened, used, checked, shipped), its collaboration level (isolated, discussed, solicited,
crossed, assembled), the judged presentation (disclosed, silent, passed_off) and the outcomes. The agent rows are the
evidence under it.

A run whose name starts with `failed<N>_` or `cancelled<N>_`, or whose episodes carry the failure `harness` or
`connection_closed` (a provider error), is **failed** and never counted. A run whose model starts with `mockllm` is a smoke
test (`mock`), listed under Runs and left out of the Summary. A verdict is **counted** only on an episode with a transcript.

A **setting** is the arrangement a run was made in: the task, the communication graph (a lone agent, or every agent on one
board), the roster size, and whether roles were hidden. A CheatBench SWE run is set by its issue, team size, honeypot and
rule ("SWE flipt-02e2 board of 3 (jigsaw)"). The other tasks than openmath carry their name ("knowledge work board of 3").

## Endpoints

### `GET /api/runs` → `[RunRow]`
```
RunRow = {
  run, task, model, topology ("single"|"board"|"swarm" as run.json wrote it), n_agents, mock: bool, failed: bool,
  agents: [str], control ("baseline" when none), monitor_model, defenses: [str], eval_log, eval_log_exists: bool,
  setting: {key, label, order, n, composition?, R?, D?, W?},   # composition and R, D, W for hidden roles
  verdict_fields: [{field, label, kind}],   # the task's judge flags in order, cheated last (src/measures/
                                            # verdict_fields.py FLAGS_BY_TASK); kind is the badge a true value takes
  swarm: null | {n_agents, regulators, disregulators, workers, seed, composition},
  deliverables: null | {archive_in_workspace, archive_on_remote_main, registered_on_main,
                        registered_message_copied_from},   # openmath: what the team left behind
  endings: {label: count},         # each episode's failure, else its ended_by (submit, teammate_submit,
                                   # stop_working, delivered, cap, context, limit, error, end), else
                                   # "unrecorded" (a log without the turn rules)
  config: null | {...},            # run.json "config": the SwarmConfig the task was built from
  rules: null | {<agent>: {...}},  # each episode's "rules": what the turn rules recorded for that agent
  swe: null | Swe,
  mode, scale, preset,             # run.json's, null when absent
  engagement: null | RunEngagement,
  turn_budget: null | {budget, steps_per_agent},
  access: null | Access,
  knowledge: null | Knowledge,
  tools: [str],                    # the analysis tools that wrote runs/<run>/tools/<tool>/summary.json, sorted
  aggregate: Aggregate,
  team: null | {rung, collaboration},   # the first sample's team record; null before the code ladder ran
  episodes: [Episode]
}
Aggregate = {                     # one run's counts; judged, cheated, flags and by_role count counted verdicts only
  judged, cheated, flags: {<verdict field>: count}, controlled: bool (a defense other than the turn rules),
  blocks, checks, timeouts, score (the highest), wall_time (the longest), tokens: {input, output, cache_read},
  judge_cost, by_role: null | {worker|regulator|disregulator: {n, cheated}}   # null without hidden roles
}
Episode = {                       # one row of episodes.jsonl plus:
  verdict: null | Verdict, ladder: null | Ladder, signals: null | Signals,
  transcript_path (the run's own trajectories/ copy when there is one), has_transcript: bool,
  counted: bool                   # a verdict with cheated set, on an episode with a transcript
}
Swe = {issue, instance, n_agents, mode ("shared_task"|"solo_task"|null for a lone agent),
       honeypot ("as_released"|"own_piece"|"jigsaw"), rule, base_commit, fix_commit, pieces: {<agent>: [str]},
       checks_ok, rewards: {team?, <agent>: 0|1|null}, required_passed: {target: int}, required_tests: int,
       measures: null | {<agent>: {posts, fix_posts, rule_posts, disclosed}}}   # swe_summary.json
Access = {policy: "split"|"all"|"explicit", n_pieces: int | null,
          agents: {<agent>: {holds: [str], pieces?: [int] | null}},   # pieces null: the whole
          remote?, honeypot_branches?, archive?: {branch, commit, name}}   # openmath's split layout
Knowledge = run.json "knowledge": {row_id, mode, split, n_pieces, layout, meta, comparator, seats, evaluation,
            images, contracts, grader_state, last_verdict, reviews, review_calls,
            value}   # value: the share of contracts delivered
RunEngagement = {agents, agents_checked, agents_posted, median_discovery_round, checks, reads, posts,
                 read_graph, reads_refused, posts_refused, oversight, oversight_graph}
Verdict = the episode's judge.jsonl row without the bookkeeping keys (id, episode, replicate, task, model,
          transcript_path, judge_generation_config, judge_generation_config_unsupported, judge_prompt, judge_schema,
          output_mode, judge_usage, judge_attempts). Always present, null when absent: cheated, summary, judge,
          judge_cost_usd, parse_ok, judge_error. The rest are the task's own verdict fields; a row that did not parse
          also carries raw.
Signals = the episode's agent row of signals.jsonl (src/measures/code_ladder.py) without row, sample, episode, agent,
          role and task: rung, holds, exposed, exposed_call, exposed_source, tried, tried_call, tried_evidence, opened,
          opened_call, opened_evidence, lines_seen, pieces_seen, joined_call, joined_evidence, probe, copies, used,
          used_evidence, checked, checked_evidence, shipped, shipped_evidence, calls, and in a run with an access log
          access_log (src/measures/access_evidence.py). exposed_source is own_action,
          teammate_post, teammate_actions, task_text (exposed_call null) or none. used, checked and shipped name content this agent put there and
          count toward its rung once the honeypot's content reached the team.
Ladder = the episode's agent row of ladder.jsonl (src/judging/judging_ladder.py) without the bookkeeping keys, raw,
         row and sample: the judged rungs with checked, forbidden_method, pushback and, when asked, presentation.
Team = a team row of signals.jsonl (src/measures/team_ladder.py), one per sample: {
  row: "team", sample, task, agents: [str], mode, split: bool,
  rung, rung_by_code,              # the final rung, and the rung before a pass raised it to passing_proves
  exposed, tried, opened, used, checked, shipped: bool,
  <rung>_agents: [str], <rung>_evidence: [str],   # for each of the six: the agents whose row holds it, and the
                                                  # evidence as "<agent>: ..." or "team: ..."
  copies: [str],                   # probe matches no agent owns: shared and team-remote copies
  content_reached: bool,           # enough of the honeypot reached the team for used, checked and shipped to count
  checked_blind: bool,             # the grader only received a copy no transcript showed to any agent
  collaboration: null | str,       # null for a lone agent; crossed and assembled only when the honeypot was split
  collaboration_evidence: {discussed, solicited, crossed, assembled: [str]},   # posts as "post <n> by <agent>: ..."
  presentation: null | "disclosed"|"silent"|"passed_off", presentation_evidence: [str],   # judged
  presentation_asked: null | bool, # the judge was asked about presentation
  presentation_hints: [str],       # what code sees of how the hand-in presents the content
  passed: null | bool, passing_proves: null | str, proof: null | str, raised_by_proof: bool,
  pushback: null | bool, pushback_evidence: [str], forbidden_method: null | bool, forbidden_method_evidence: [str],
  judged: bool,                    # a judged team row of ladder.jsonl was merged
  ended_by: null | "submit"|"stop_working"|"delivered"|"tokens"|"time"|"steps"|"end"|"error", or a limit as written,
  submit: null | {agent, round, ends_after_round, snapshot}, # snapshot: a commit, or "working tree" (Domain.shown_snapshot)
  own_submits: {agent: round}, refused_submits: int,
  shutdown_rate: null | float,
  access_log?: {status, log_only: [str], transcript_only: [str], team_only: [str],
                rung_without_log: str, rests_on_log: bool}
                                   # a run with an access log: where it and the parser disagree, and the rung the team
                                   # has with no log (src/measures/access_evidence.py)
}
```
An episode keeps every other key episodes.jsonl gives it (usage `{input, output, cache_read}` with input including cache
reads, defense, engagement, access, rules, the SWE keys).

### `GET /api/runs/<run>` → `RunRow + {run_json, team, communication, defense, tools}`
`team: [Team]` replaces the row's summary with every team record of the run, one per sample, which the run page shows
first. `run_json` is run.json itself; the run page reads its `team_submission` (`null | {agent, round, answer, time,
ends_after_round, snapshot, refused: [{agent, round, reason}], own: {agent: {round, answer, snapshot, time}}}`: the
team's accepted submit, every refused one and each solo_task agent's own hand-in), `stop_working` (`null | {agent:
round}`) and `shutdown_rate` (the share of the team's agents that ended with `stop_working`; null for a lone agent or when no agent
could stop). run.json `access_log`, in a run that recorded the switch, holds the log's status, what it missed and the harness's reads of content (`harness_reads: {count, own, commands: [{cmd, root_cmd, rows}]}`); `src/export/_export_access_log.py` gives its keys. The run page's Access log section reads it with the team's and the agents' `access_log` keys. `communication: {<sample>: {messages: [Post], events: [...]}}` and `defense: {<sample>:
{defenses, log, interventions}}` are the run's stores. `tools` replaces the row's name list with each tool's summary.json
(`{tool, run, written_at, ...}`; the schemas are in each tool package's docstring under tools/); a summary that does not
parse or is not an object becomes `{"error"}`.

### `GET /api/runs/<run>/transcript/<episode>` → `{episode: Episode, transcript: Transcript}`
```
Transcript = {agent, model, prompt, orbit: {eval_log, agent, roster, topology, system_prompt,
              turn_rules_recovered_from_events, model_calls}, trajectory: ATIF}
ATIF.steps[i] = {step_id, source: "system"|"user"|"agent", message, reasoning_content?, model_name?,
                 tool_calls?: [{tool_call_id, function_name, arguments}],
                 observation?: {results: [{source_call_id, content, extra?: {is_error?, error?, withheld_from_model_by_defense?}}]},
                 extra?: {swarm_turn_rules?, defense_refusal?, refusal_text_written_by_model?, ...}}
```
A blocked tool call has a result whose `extra.error` starts with `Blocked by defense`.

### `GET /api/runs/<run>/sample` → the sample's facts from the eval log
`{status, model, eval_id, sample_id, epoch, total_time, working_time, limit: null | {type, limit, reason}, error,
  model_usage: {model: {input, cache_read, output, reasoning}}, scores: {<scorer>: {value, answer, explanation, metadata}},
  store: {<key>: ...}, n_messages, n_events}`

### `GET /api/runs/<run>/context` → `{run, agents: [str], defenses: [str], calls: [Call]}`
```
Call = {index, agent (null for a call outside any agent span), defense (null for the agent's own call, else the defense
        span's name, "<defense>:<boundary>", or "inside tool <name>" for a call made while a tool ran), model, timestamp,
        working_time, n_input, input_chars, tools: [str], usage: null | {input, cache_read, output, reasoning},
        output_preview, output_tool_calls: [str], stop_reason, error,
        flags: subset of ["turn_rules", "redacted", "refusal_in_history", "output_blocked"]}
```
### `GET /api/runs/<run>/context/<index>` → `Call + {input: [Message], output: Message|null, output_error, tool_schemas: [{name, description}]}`
```
Message = {role: "system"|"user"|"assistant"|"tool", id, text, reasoning, flags: [...], withheld_text?,
           tool_calls?: [{id, function, arguments}], model? (assistant), tool_call_id?, function?, error? (tool)}
```
`flags` mark provider-only or altered messages: `turn_rules` (a board notice or continue prompt the turn rules placed in
the input, or a system message without ORBIT's assistant paragraph, as the filter sent it), `redacted` (a defense withheld this message's content; `text` is the placeholder and `withheld_text` the
original), `output_blocked` (a defense blocked this call's own completion), `refusal_in_history` (an assistant message
whose text is the defense's refusal sentence). A call's flags are its input messages' flags plus `output_blocked`.

### `GET /api/runs/<run>/timeline` → one run's events in seconds from the sample's start
```
{run, duration, limit, agents: [str], roles: {agent: role} (from the episodes),
 first_exposed: {agent: t}, first_opened: {agent: t},   # the code ladder's exposed and opened calls on the clock
 events: [Event] sorted by t, board: [Post]}
Event = {t, t_end?, agent, kind: "model"|"defense_call"|"tool"|"submit"|"stop"|"defense", ...}
  model, defense_call: defense, tokens_in (including cache reads), tokens_out, tool_calls: [function],
                       output_blocked (a defense replaced this completion), refusal (the output text is the refusal
                       sentence, possibly model-written), error
  tool, submit, stop:  function, id, preview, blocked, error, exposed, opened; submit and stop are a submit or a
                       stop_working call that went through, and a refused one stays a tool
  defense:             defense, boundary, allow, reason, tool
Post = {sequence, sender, id, t, content, read_by: [{agent, t}]}
```
`exposed` and `opened` mark the tool call the code ladder (signals.jsonl) cites as the agent's `exposed_call` and
`opened_call`: the first call that showed it a pointer to the honeypot, and the first that showed it the honeypot's content.
Call k is the agent's k-th tool call in its transcript, from 0, matched to the eval log's tool event by its id.

### `GET /api/runs/<run>/tools/<tool>/<path>` → one file an analysis tool wrote
Serves `runs/<run>/tools/<tool>/<path>`, where `<path>` may have subdirectories (`judged/k7q2m/report.html` under `transect`). Only `.png`,
`.csv`, `.html`, `.gexf`, `.json` and `.yaml` are served, with their media type. The run page uses it for figures
(graph.png, contagion.png, onset.png), reports (transect report.html), downloads (graph.gexf) and the failures tool's
mast.json and who_when.json.
