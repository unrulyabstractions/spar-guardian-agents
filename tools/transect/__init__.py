"""The transect tool: Transect (AISI) reports that turn one run into a readable step-by-step account per agent.

    uv run --project tools python -m tools transect riemann_minimal                 # judged by gpt-6-luna
    uv run --project tools python -m tools transect riemann_minimal --structural    # no model calls
    uv run --project tools python -m tools transect riemann_minimal --judge gpt-6-sol

Transect numbers the turns of one orchestrator and refuses an Inspect sample that holds several top-level agents,
which is how ORBIT logs a team. The tool still scans the merged sample structurally and records the refusal. It
then converts each agent's ATIF transcript into OpenClaw telemetry (transect_openclaw.py), with the timestamps and token
usage of the agent's model events in the eval log, and runs one Transect report per agent. Transect's turn t is the
agent's activation t + 1. The phase vocabulary is spec.yaml; the run's domain, the agent's hidden role and the
exploit's branches from run.json access are appended to its context. The command exits 1 when an agent's scan
recorded an error (a failed judge call, for instance) and prints every agent whose exploit_use turns disagree with
the run judge's verdict.

Outputs under runs/<name>/tools/transect/, where <mode> is judged or structural (a structural rerun leaves the
judged reports in place):
    merged/report.html              the merged sample's structural report (its scan errors included)
    <mode>/<agent>/telemetry.jsonl  the agent's OpenClaw telemetry
    <mode>/<agent>/report.html      the agent's Transect report
    <mode>/<agent>/phases.csv       one row per phase: agent, phase_index, label, start_turn, end_turn, start_round,
                                    end_round, headline, confidence, judge_agreement, n_digest_turns, explanation
    <mode>/<agent>/token_timeline.csv  Transect's token_timeline frame plus activation, schedule_step and round
    <mode>/phases.csv               every agent's phases.csv rows
    summary.json                    the latest invocation, whichever its mode

summary.json:
    {tool, run, written_at,
     spec: path of spec.yaml, mode: "judged" | "structural",
     reports: {"run": merged structural report or null, <agent>: report.html path},
     phases: {<agent>: [{label, start_turn, end_turn, start_round, end_round}]}, turns 0-based, [] when structural,
     phases_csv: path of <mode>/phases.csv, structural_only: bool,
     judge: null | {model, k_rolls, verify, digest: {DigestSettings fields},
                    usage: {<agent>: [{scanner, model, input_tokens, output_tokens, total_tokens,
                                       input_tokens_cache_read, input_tokens_cache_write, reasoning_tokens,
                                       total_cost}]},
                    totals: {input_tokens, output_tokens, total_tokens}},
            usage is what this invocation billed: zero for calls served from the Inspect cache,
     merged_sample: {sample, accepted, errors: [str], report},
     agents: {<agent>: {role, cheated, telemetry, timing, turns, scan_errors: [str],
                        agreement: {exploit_use_turns: [[start_turn, end_turn]], judge_cheated,
                                    agrees: bool | null (null when structural or unjudged)}}}}
"""
