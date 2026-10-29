"""The failures tool: MAST failure modes and Who&When attribution over a run's merged trace.

    uv run --project tools python -m tools.failures.fetch_mast_taxonomy
    uv run --project tools python -m tools failures riemann_minimal [--model gpt-6-luna] [--max-steps 60]

It writes under runs/<name>/tools/failures/:

mast.json: {modes: {"1.1": "yes"|"no", ... "3.3"}, summary, task_completed: "yes"|"no"|null, modes_strict:
{"1.1": "yes"|"no"|null, ...}, disagreements: [mode], reply_names: {mode: name the reply gave|null}, renamed:
{mode: reply name differing from the prompt's}, raw, names: {mode: name in definitions.txt}, prompt_names: {mode:
name in the prompt}, trace_characters}

who_when.json: {method: {agent, step, round, step_agent, kind: "post"|"step", ref, reason, raw, calls, ...}} for
all_at_once, step_by_step (also capped, max_steps, history_length) and binary_search. step indexes the merged trace
from 0; step_agent is the agent of that entry; ref is the step's index in trajectory.steps or the post's board
number. When all_at_once's agent and step_agent differ, the model's agent and its step disagree.

summary.json: {tool, run, written_at, model, who_when_max_tokens, mast: {modes, summary, task_completed, yes,
modes_strict, yes_strict, disagreements, renamed} | null, who_when: {method: {agent, step, step_agent, round, kind,
calls, capped}} | null}

modes is the MAST notebook's parser verbatim. modes_strict reads each mode's own line; prefer it where the two
disagree (see tools/failures/mast.py). A 3.2 or 3.3 label means the mode named in renamed when renamed lists it.
"""
