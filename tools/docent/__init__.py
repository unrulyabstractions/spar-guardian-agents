"""The docent tool: each agent episode of a run as a Docent AgentRun, so questions across runs can be asked in Docent.

    uv run --project tools python -m tools docent riemann_minimal --dry-run
    uv run --project tools python -m tools docent riemann_minimal worktime_minimal --board
    uv run --project tools python -m tools docent flipt_minimal --collection-id <id>

An agent's AgentRun is its ATIF transcript converted by docent.sdk.integrations.convert_atif_to_agent_run. Each
message's metadata gains round, activation, schedule_step, turn and activations from the turn rules' timeline. The
AgentRun's metadata holds kind "agent", run, agent, role, domain, task, model, preset, mode, scale, roster, roles,
cheated_by_agent, median_discovery_round, read_graph, rounds, access_policy, holds, access, each of the task's judge
flags (src/measures/verdict_fields.py), judge_summary, judge (the whole judge.jsonl row), discovery_round,
engagement, board_posts, n_board_posts, ended_by, usage and score, next to the converter's own atif block. With
--board each run also gets one AgentRun of kind "board": a system message naming the roster, then one user message
per post, labelled with sender, role, board number and round.

Outputs under runs/<name>/tools/docent/:
    agent_runs.json  the AgentRuns as model_dump(mode="json") gives them, in upload order
    summary.json     {tool, run, written_at,
                      dry_run: bool, board: bool, n_agent_runs: int, agent_runs_file: "agent_runs.json",
                      agent_runs: [{name, id, kind, agent, cheated, messages}],
                      and after an upload also collection_id, collection_name (null with --collection-id),
                      collection_url, agent_run_ids (Docent's ids, the same as each AgentRun's id),
                      upload_status, job_ids}

The API key is found as the SDK finds it: a docent.env in the current directory or a parent, else
~/.docent/docent.env, else DOCENT_API_KEY in the environment. Without one an upload exits 2.
"""
