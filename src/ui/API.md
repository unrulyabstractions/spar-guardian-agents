# Results browser API

Start: `make ui`, or `uv run python -m src ui [--port 8765] [--no-open]` from the repository root. The
foreground server alone is `uv run python -m src.ui [--runs runs] [--port 8765] [--open]`. The UI is served
at `/` and its assets at `/static/<path>` from `src/ui/static/` (`index.html`, `style.css` and the ES modules under
`app/`, which `index.html` loads through `app/main.js`). A static path that resolves outside `src/ui/static/` gets 404.
The server is `server.py`. It reads runs through `run_store.py` and builds the timeline in `timeline_builder.py`. All API
responses are JSON.

## UI routes

| hash | part |
|---|---|
| `#/` | Summary: cheating by setting, composition grids, the swarm runs |
| `#/runs?q=&setting=&control=&cheated=&failed=1&sort=&dir=` | Runs: one row per run, opening to its agents |
| `#/run/<run>` | run page (with mode, scale, preset, split access, turn budget, board engagement, the knowledge-work section for that task, and the analysis tools' cards); `.../timeline`, `.../context?agent=&call=`, `.../sample`, `.../episode/<episode>?find=&kind=&step=` |
| `#/swarm?run=<run>` | Swarm: grids, outcome funnel, adoption curves, swarm runs with rasters, run explorer |

A **setting** is the communication graph (`single`: a lone agent; `board`: every agent on one shared board), the roster size, and
whether roles were hidden. A CheatBench SWE run has its own setting per issue, team size, honeypot and hint ("SWE flipt-02e2
board of 2 (jigsaw)"), and its run page gains a "SWE task" section. A knowledge-work run has its own setting by team size and
hidden roles ("knowledge work board of 3"), and its run page gains a "Knowledge work" section. `run.json` writes `topology: "swarm"` for a board with hidden roles; the API adds `graph` so the swarm reads
as what it is, a board of N agents with roles.

## Data model

A **run** is one directory under `runs/` holding `run.json`, `episodes.jsonl`, `judge.jsonl`, `trajectories/<episode>/transcript.json`,
optionally `communication/<sample>.json` (board posts) and `defense/<sample>.json` (control evidence). One run has one sample
(`riemann#1`) and one **episode per agent**: `riemann#1` for a lone agent, `riemann#1@alpha` and `riemann#1@beta` on the board.
`run.json.eval_log` points at the Inspect `.eval` log, from which **context windows** (every model call's exact provider input)
are read on demand.

Runs whose name starts with `failed<N>_` or `cancelled<N>_`, or whose episodes carry `failure` in `harness` or `connection_closed`
(a provider error), are **failed** and never counted. A run whose model starts with `mockllm` is a smoke test (`mock: true`), listed
under Runs and left out of the Summary. A verdict **counts** only on an episode with a transcript.

## Endpoints

### `GET /api/runs` → `[RunSummary]`
```
RunSummary = {
  run, task, model, topology ("single"|"board"|"swarm" as run.json wrote it), graph ("single"|"board"), n_agents, roles: bool, mock: bool,
  agents: [str], control (e.g. "baseline","monitor_task"), monitor_model, defenses: [str], eval_log, eval_log_exists, failed: bool,
  swarm: null | {n_agents, regulators, disregulators, workers, seed, composition},
  deliverables: null | {archive_in_workspace, registered_on_main, registered_message_copied_from},
                                   # openmath solo_task: archive_in_workspace means every copy holds the archive;
                                   # under split access registered_on_main reads the team remote's main
  endings: {label: count}   # failure label, else "submit", else the converter's ended_by
  has_communication, has_defense,
  config: null | {...},            # run.json "config": the SwarmConfig the task was built from (every field)
  rules: null | {<agent>: {...}},  # each episode's "rules": what the turn rules recorded for that agent
  swe: null | SweSetting,          # CheatBench SWE runs; null otherwise
  mode: null | "solo_task"|"shared_task",   # the config's mode on the run's domain; null before modes
  scale: null | "small"|"medium"|"large"|"extra_large"|"gigantic",  # the config's scale; null when the fields were set by hand
  preset: null | "default",
  engagement: null | RunEngagement,  # null for a run without a board
  validity: null | {agents: {<agent>: {model_calls, tool_calls, never_acted, no_tool_call, identical_tool_run, board_unused}},
                    flags: [str], ok: bool},      # what a reader should see before trusting the run
  ledger: null | {agents: {<agent>: {model, calls, errored, with_usage, tokens: {input, cache_read, output},
                                     cost_usd, billed_usd, billed_calls, flags: [str]}},
                  total: {...}, complete: bool, flags: [str]},   # list-price cost and what the provider billed; flags make the totals lower bounds
  sandbox: null | {end: {processes: {<agent>: {alive_at_end, survivors}}, sockets: {inet, unix}, shared_files: {<dir>: {files, by_owner, agent_paths}}},
                   reaps: {<agent>: {commands, killed, max_alive, survivors, errors}},
                   loopback: null | {loopback: "refused", uids, allow, ipv6, refusals: {<agent>: str}}},   # src/benchmark/sandbox_hygiene.py; per container on the machines domains
  code: null | {commit, dirty, submodules: {<name>: head}},   # the code that ran
  turn_budget: null | {budget, steps_per_agent},   # the domain's turn budget; null without one
  access: null | Access,           # the access policy and what each agent held; null for runs before the field
  knowledge: null | Knowledge,     # knowledge-work runs only
  tools: [str],                    # the analysis tools that wrote runs/<run>/tools/<tool>/summary.json, sorted; [] for none
  episodes: [Episode]
}
Access = {policy: "split"|"all"|"explicit", agents: {<agent>: {holds: [str], branches?: [str], slices?: [int]|null,
          review?: bool, read?: bool}}, remote?, honeypot_branches?, archive?: {branch, commit, name, parts}}
          # holds: the agent's access items (openmath slice<k>/whole, SWE A/B/C/whole, knowledge work review/read).
          # openmath split layout: the team remote, the honeypot branches every copy keeps, the complete submission's
          # archive cut into `parts` slices, and `slices`: the slice numbers an agent's copy held (null: the whole
          # archive). Knowledge work: whether the agent holds the review tool and the read permission.
Knowledge = {layout ("lone"|"shared"|"solo"), mode, split, row_id, meta: {task_id, sector, occupation, gold_name},
             comparator (the pipeline artifact path), seats: {<agent>: {user, uid, home, root, contract, candidate, review,
             read}}, contracts: {<contract>: {root, candidate, files: [{path, bytes, sha256}], skipped?: [{path, kind}],
             copied_reference, error}},   # skipped: symlinks and other non-regular entries of to_send/, never followed
             grader_state: {<agent>: {agent_id, calls, errors, verdicts, similarities, submitted_reference}},
             last_verdict, access, share_delivered, reviews}
RunEngagement = {
  agents, agents_checked (looked: board_status or board_read), agents_posted, median_discovery_round,
  checks, reads, posts, read_graph: {reader: {sender: count}},
  team_submission: null | {agent, round, answer, time, refused: [{agent, round}]},
  reads_refused, posts_refused, oversight, oversight_graph: {regulator: {target: count}}
}
SweSetting = {
  task_key ("flipt-02e2"), instance, n_agents, board: bool, honeypot ("as_released"|"own_piece"|"jigsaw"|null), jigsaw: bool,
  rule ("none" or the rule level),
  base_commit, fix_commit, arm ("n2-own_piece-none"),
  team_reward: 0|1|null (team/main; null for a lone agent), rewards: {team?, <agent>: 0|1|null}, required_passed: {target: int},
  required_tests: int, pieces: null | {<agent>: piece}, fragments: null | {<agent>: [file]},
  checks_ok: bool|null,             # the setup's planting and team-repository checks
  rungs: null | {<agent>: "none"|"exposed"|"tried"|"opened"|"used"|"shipped"},     # the code ladder's rung per agent, from signals.jsonl
  measures: null | {<agent>: {posts, fix_posts, rule_posts, disclosed}}            # swe_summary.json
}
Episode = {                       # one row of episodes.jsonl plus:
  episode, orbit_agent, role?, n_tool_calls, n_turns, wall_time, limit, failure, error, ended_by, ok, score: {deliverables_scorer: 0|1},
  usage: {input, output, cache_read}, control, defenses: [str],
  defense: {checks, blocks, blocks_by_boundary: {model_input?, model_output?, tool_call?}, checks_by_boundary},
  transcript_path, has_transcript,
  verdict: null | Verdict,
  ladder: null | Ladder,
  signals: null | Signals,
  engagement: null | {discovery_round, checks, reads, posts, read_senders: {sender: count}, free_steps, work_steps,
                      team_submitted: bool, notices, reads_refused, posts_refused, oversight,
                      oversight_targets: {target: count}},      # null for a run without a board
  access?: {holds, branches?, slices?, review?, read?},   # this agent's entry of Access
  review?: {agent_id, calls, errors, verdicts, similarities, submitted_reference}   # knowledge work: its grader state
}
Verdict = every key of the episode's judge.jsonl row except the bookkeeping keys (id, episode, replicate, task, model,
          transcript_path, judge_generation_config, judge_generation_config_unsupported, judge_prompt, judge_schema,
          output_mode, judge_usage, judge_attempts). Always present, null when absent: cheated, summary, judge,
          judge_cost_usd, parse_ok, judge_error. The rest are the task's own verdict fields: openmath has honey_pots_seen,
          attempted_access, extracted_submission, success_claim_seen_before_access, disclosed_access,
          explicit_prohibition_violated, explicit_prohibition_evidence; SWE has reference_solution_seeking,
          reference_solution_obtained, reference_solution_used, documentation_lookup, evaluation_tampering. A row that did
          not parse also carries raw (the judge's text).
Signals = the episode's signals.jsonl row (src/measures/code_ladder.py, `python -m src signals`) without episode, agent,
         role and task: rung, exposed, exposed_call, exposed_source, searched, searched_call, tried, tried_call,
         tried_evidence, opened, opened_call, opened_evidence, lines_seen, probe, copies, used, used_evidence,
         shipped, shipped_evidence, calls. Null when the run has no signals.jsonl or the episode is not in it.
Ladder = the episode's ladder.jsonl row (src/benchmark/judging_ladder.py, `python -m src ladder`) without the same bookkeeping keys
         and without raw: CheatBench's verdict fields as that judge call answered them, then the ladder's fields exposed, exposed_call,
         exposed_source, searched, searched_call, tried, tried_call, tried_evidence, opened, opened_call, opened_evidence, used,
         used_evidence, shipped, shipped_evidence, tampered, tampered_evidence, admitted, concealed, false_denial, honesty_evidence,
         ladder_summary; then rung (none|exposed|tried|opened|used|shipped, derived in code), exposed_round, searched_round,
         tried_round, opened_round (from the eval log's turn timeline; null without one), rounds_source (timeline|none),
         citations ({ok, <field>: {call, exists, evidence_in_call?}}), view (full|actions), agent, role, failure, judge,
         judge_cost_usd, parse_ok, judge_error. Null when the run has no ladder.jsonl or the episode is not in it. The
         actions view's rows (ladder_actions.jsonl) are not served.
SWE episodes also carry the SWE keys src/benchmark/run_export.py writes (swe_task, seat, piece, reward, team_reward, ...). An episode
written by the src tasks carries rules (the turn-rules facts for its agent).
```

### `GET /api/runs/<run>` → `RunSummary + {run_json, communication: {<sample>: {messages:[...], events:[...]}}, defense: {<sample>: {defenses:[DefenseConfig], log:{blocks:[...], ...}, interventions:[...]}}, tools: {<tool>: ToolSummary}}`
`tools` here replaces the summary's name list with each tool's summary.json as the tool wrote it (`{tool, run, written_at, ...}`;
the schemas are in each tool package's docstring under tools/). A summary.json that does not parse or is not an object becomes `{"error"}`. The
loader caches tools/timeline.json and tools/board_reads.json are files, not tools, and are never listed.
Board message: `{id, sender, sequence, channel_id, content, ...}`. Intervention: `{event:"defense_intervention", defense, boundary ("model_input"|"model_output"|"tool_call"), allow: bool, reason, agent, turn, tool?, message_id?, ...}`.

### `GET /api/runs/<run>/transcript/<episode>` → `{episode: Episode, transcript: Transcript}`
```
Transcript = {agent, model, prompt, orbit: {agent, roster, topology, system_prompt, submit_recovered_from_events,
              turn_rules_recovered_from_events, model_calls}, trajectory: ATIF}
ATIF.steps[i] = {step_id, source: "system"|"user"|"agent", message, reasoning_content?, model_name?,
                 tool_calls?: [{tool_call_id, function_name, arguments}],
                 observation?: {results: [{source_call_id, content, extra?: {is_error?, error?, withheld_from_model_by_defense?}}]},
                 extra?: {swarm_turn_rules? ("continue"|"notice"),
                          submit_recovered_from_events?, orphan_tool_result?, ...}}
```
A blocked tool call has a result whose `extra.error` starts with `Blocked by defense '<name>'`. An agent step whose message is
exactly `I cannot provide that response under the configured defense policy.` is a defense refusal (blocked completion).

### `GET /api/runs/<run>/sample` → sample-level facts from the eval log
`{status, model, eval_id, total_time, working_time, limit, error, model_usage: {model: {input, cache_read, output, reasoning}},
  scores: {deliverables_scorer: {value, answer (message.txt), explanation, metadata: {listing, git, agent_evals, agent_evals_main, probe}}},
          # openmath solo_task: value is the share of copies holding the archive, answer is null, and metadata is
          # {agents: {name: {archive, message, listing, git, agent_evals, agent_evals_main}}, probe}; under split
          # access metadata also has remote: {archive_on_main, message_on_main, agent_evals_main, branches, git} and
          # archive_parts: {agent: [k, ...]}, the archive slices each copy held in place of the archive
          # knowledge work: knowledge_deliverables: {value (share of contracts whose to_send/ holds a file),
          # answer (last verdict), metadata: {contracts, grader_state, reviews, last_verdict, access, probe}}
  store: {...}, n_messages, n_events}`

### `GET /api/runs/<run>/context` → `{run, agents: [str], defenses: [str], calls: [CallIndex]}`
```
CallIndex = {index, agent (null for a defense's own call that is not inside an agent span), defense (null for the agent's own call,
             else the defense name whose monitor made the call), model, timestamp, working_time, n_input, input_chars, tools: [str],
             usage: {input, cache_read, output, reasoning} | null, output_preview, output_tool_calls: [str], stop_reason, error,
             flags: subset of ["turn_rules", "redacted", "output_blocked",
                               "refusal_in_history"]}
```
### `GET /api/runs/<run>/context/<index>` → `CallIndex + {input: [Message], output: Message|null, output_error, tool_schemas: [{name, description}]}`
```
Message = {role: "system"|"user"|"assistant"|"tool", id, text, reasoning, flags: [...],
           tool_calls?: [{id, function, arguments}] (assistant), model? (assistant),
           tool_call_id?, function?, error? (tool)}
```
`flags` mark provider-only or altered messages: `turn_rules` (a board notice or continue prompt the turn rules placed in
the input), `redacted` (a defense withheld this message's content from the model), `output_blocked` (this call's own
completion was blocked by a defense), `refusal_in_history` (an earlier assistant message in the input is the refusal sentence).

Errors: 404 `{"error": ...}` for unknown run/episode/call; 500 `{"error", "traceback"}` for server faults.

### `GET /api/runs/<run>/timeline` → events of one run on a common clock (seconds from the sample's start)
```
{run, t0, duration, limit, agents: [str], roles: {agent: role} (swarm runs), verdicts: {agent: cheated|null},
 first_honeypot_touch: {agent: t}, first_member_read: {agent: t},   # heuristics, see markers
 events: [Event] sorted by t, board: [Post], markers: {touch, member_read}}
Event = {t, t_end?, agent, kind: "model"|"defense_call"|"tool"|"submit"|"defense", ...}
  model:        tokens_in, tokens_out, tool_calls: [function], output_blocked: bool (a defense replaced this completion; from the block event), refusal: bool (the output text is the refusal sentence, possibly model-written), error
  defense_call: defense (the control's own judge call)
  tool/submit:  function, id, preview, blocked, error, honeypot_touch, member_read, message_ids? (board_post: sent; board_read: read)
  defense:      defense, boundary, allow, reason, tool?
Post = {sequence, sender, id, t, content, read_by: [{agent, t}]}
```
`honeypot_touch` marks a bash/python call whose arguments name another agent's branch, commit or copied archive
(openmath_agent riemann row); `member_read` marks a successful call whose result carries text found only inside
agent-014's archive members. They place moments on a timeline; the judge verdict in `verdicts` is the record of truth.

### `GET /api/runs/<run>/tools/<tool>/<path>` → one file an analysis tool wrote
Serves `runs/<run>/tools/<tool>/<path>`, where `<path>` may have subdirectories (`transect/a01/report.html`). Only `.png`,
`.csv`, `.html`, `.gexf`, `.json` and `.yaml` are served, with their media type (GEXF and YAML as `application/octet-stream`).
A path with a `.` or `..` segment, an encoded separator inside a segment, a target outside the tool's directory, or another
file type gets 400 `{"error"}`; a missing file gets 404. The run page uses it for figures (graph.png, contagion.png,
onset.png), reports (transect report.html), downloads (graph.gexf) and the failures tool's mast.json and who_when.json.

### `GET /api/summary` → `{runs: [RunAggregate]}` for cross-run charts (no eval log needed)
```
RunAggregate = {run, control, topology, graph, roles: bool, mock: bool, model, failed, n_agents, judged, cheated, honeypots_seen,
                attempted, disclosed, blocks, checks, timeouts, score, wall_time, prompt_tokens, output_tokens, cache_read, judge_cost,
                registered_on_main, registered_copied_from, archive_in_workspace, endings: {label: count},
                regulators, disregulators, workers, composition, seed,
                by_role: {worker|regulator|disregulator: {n, cheated, honeypots_seen, attempted, disclosed}} | null,
                task, swe: null | SweSetting}
```
