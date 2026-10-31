"""An Inspect .eval log from an ORBIT run, written as the run directory CheatBench's judge and our browser read.

    write_run(eval_log_path, run_dir, verdicts=None)

Writes
    <run_dir>/run.json                                 the run: task, model, roster, defenses, team, config, rules
    <run_dir>/episodes.jsonl                           one record per (sample, agent)
    <run_dir>/access_log.jsonl                         one row per open of the honeypot, for a run that kept a log
                                                       (src/export/_export_access_log.py)
    <run_dir>/judge.jsonl                              one CheatBench verdict row per judged episode
    <run_dir>/trajectories/<episode>/transcript.json   the ATIF transcript the judge read
    (src/export/atif_transcripts.py)
    <run_dir>/trajectories/<episode>/prompt.txt
    <run_dir>/communication/<sample>.json              channel messages and events, if any
    <run_dir>/defense/<sample>.json                    defense configuration and interventions, if any
    <run_dir>/swe/diffs/<target>.diff                  for a SWE run, the graded diffs (Benchmark.write_files)

run.json also carries the run's mode, scale and preset, the submits (`team_submission`: the agent whose submit handed
in the team's work, its round, the round after which the run ends, the snapshot, the refused calls and each solo_task
agent's own hand-in; None when no agent called submit), the round of each stop_working call (`stop_working`: {agent:
round}; None when no agent stopped) and the shutdown rate (the share of the agents that held stop_working that ended
with it before any hand-in; None for a lone agent or when no agent could stop), and each episode and the
run an `engagement` block: who looked at the board, read it, posted and called submit (None for a run without a board).

An episode's ended_by comes from the store records (_export_run_blocks.ended_by): "submit" (it handed in),
"teammate_submit" (a teammate's submit ended the run while the agent's window was open), "stop_working" (it stopped),
"delivered" (stop_after_delivery), "cap", "context", "limit", "error" or "end" (the plan ran out or its window
closed). In an older log ORBIT's own submit reads as the act it was: a stop in a team, a hand-in for a lone agent and
in solo_task. A log without the turn rules' store has none (None).

An episode's "model" is the agent's own model: its role's model when the run gave the role one, else the eval's model.
The eval log names a role model "swarm/<model>" (src/models/swarm_model_provider.py) and the export drops the
prefix. A run that recorded role models gets run.json "role_models" ({role: Inspect string or None}) next to "model".

Blocks written when the run has them: run.json "turn_budget" ({budget, steps_per_agent}), "access" (the access policy
and what each agent held: its items and numbered pieces, and on openmath's split layout the team remote, the honeypot
branches and the archive), the domain's own blocks (Benchmark.run_blocks: "openmath" and "deliverables", "swe",
"knowledge", "interview", "protein", "creative", "chess"), "honeypot_probe" (the honeypot probe's report by owner:
agents, remote, shared), "delivery" (the stop-after-delivery record), "access_log" (the access log's status, for a run
that recorded the switch: src/export/_export_access_log.py); and an episode's "access", the domain's facts
(Benchmark.episode_facts) and "delivery" (the round and working step at which its deliverable was first seen). The
export never writes run.json "signals": the code ladder (src/measures/code_ladder.py) adds it after the export.

Episode ids: "<sample>#<epoch>" for a single agent, "<sample>#<epoch>@<agent>" otherwise (export_episodes.episode_id).
Verdicts come from the `verdicts` argument ({episode: verdict}, when re-judging) or from the cheatbench_judge score in
the log; a judge.jsonl row is the episode's identity, the verdict, and the transcript the judge read.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import inspect_ai
import orbit
from inspect_ai.log import read_eval_log

from src.domains import BY_TASK
from src.export._export_access_log import access_log
from src.export._export_run_blocks import (
    cheatbench,
    domain,
    ledger_block,
    probes_block,
    recorded_run_blocks,
    rules_state,
    run_engagement,
    score_metadata,
    shutdown_rate,
    stop_rounds,
    swarm_block,
    team_submission,
    validity_block,
)
from src.export.eval_sample_view import SampleView, orbit_halt_reason
from src.export.export_episodes import sample_key, sample_records
from src.judging.cheatbench_judge import JUDGE_SCORER
from src.measures.run_directory import ACCESS_LOG, EPISODES, JUDGE, RUN_JSON
from src.models import swarm_model_provider as provider
from src.utils.json_files import write_jsonl

# The identity a judge.jsonl row takes from its episode record, ahead of the verdict.
VERDICT_IDENTITY = ("id", "episode", "replicate", "task", "model")
OWNED_FILES = (RUN_JSON, EPISODES, JUDGE, ACCESS_LOG)
OWNED_DIRS = ("trajectories", "communication", "defense", "swe")


def run_json(view: SampleView | None, eval_path: Path, run_dir: Path, records: list[dict], eval_id: str,
             model: str, access_log_block: dict | None) -> dict:
    """run.json: what every run carries, then, for a log with a sample, what the first sample recorded."""
    first = records[0] if records else {}
    task = first.get("task")
    run = {
        "task": task, "task_root": str(BY_TASK[task].task_dir) if task else None, "variant": "",
        "run_id": run_dir.name, "model": provider.inspect_string(model), "agent": "orbit", "source": "orbit",
        "topology": first.get("topology", "single"), "agents": first.get("orbit_roster", []),
        "eval_log": str(eval_path.resolve()), "eval_id": eval_id,
        "orbit_version": orbit.__version__, "inspect_version": inspect_ai.__version__,
    }
    if view is None:
        return run
    cb = cheatbench(view)
    config = cb.get("config") or {}
    submission = team_submission(view)
    if "role_models" in cb:                     # each role's model, read beside the agents' model
        keys = list(run)
        cut = keys.index("model") + 1
        run = {**{k: run[k] for k in keys[:cut]}, "role_models": cb["role_models"], **{k: run[k] for k in keys[cut:]}}
    return {
        **run,
        "defenses": (view.metadata.get("experiment") or {}).get("defenses") or [],
        "swarm": swarm_block(view), "orbit_halt_reason": orbit_halt_reason(view),
        "deviations": list(cb.get("deviations") or []), "config": cb.get("config"),
        "team_submission": submission, "stop_working": stop_rounds(view), "shutdown_rate": shutdown_rate(view, records),
        "rules": rules_state(view),
        "probes": probes_block(view), **{key: config.get(key) for key in ("mode", "scale", "preset")},
        "engagement": run_engagement(records), "validity": validity_block(records, submission, access_log_block),
        "ledger": ledger_block(records, view.limit.type if view.limit else None),
        **recorded_run_blocks(view), **({"access_log": access_log_block} if access_log_block else {}),
    }


def judge_rows(views: list[SampleView], records: list[dict], verdicts: dict[str, dict] | None) -> list[dict]:
    """judge.jsonl rows from `verdicts` ({episode: verdict}) or from each sample's judge score ({agent: verdict}):
    the episode's identity, the verdict, the transcript path. An episode without a transcript gets none."""
    by_sample = {sample_key(v): v for v in views}
    rows = []
    for record in records:
        if verdicts is not None:
            verdict = verdicts.get(record["episode"])
        else:
            view = by_sample[f"{record['id']}#{record['replicate']}"]
            verdict = ((score_metadata(view, JUDGE_SCORER) or {}).get("verdicts") or {}).get(record["orbit_agent"])
        if verdict and record["transcript_path"]:
            rows.append({**{key: record[key] for key in VERDICT_IDENTITY}, **verdict,
                         "transcript_path": record["transcript_path"]})
    return rows


def clear_outputs(run_dir: Path) -> None:
    """Remove what an earlier export of this run wrote, so stale verdicts or transcripts never pair with new episodes.

    Only the files and directories this module writes are removed; the measures' files (signals.jsonl,
    swe_summary.json) stay.
    """
    for name in OWNED_FILES:
        (run_dir / name).unlink(missing_ok=True)
    for name in OWNED_DIRS:
        shutil.rmtree(run_dir / name, ignore_errors=True)


def write_run(eval_log_path: Path, run_dir: Path, *, verdicts: dict[str, dict] | None = None) -> dict:
    """Write the run directory for one eval log, which holds one sample; return a short summary of what was written.
    A log of several samples (inspect eval --epochs) is refused: run.json and the code ladder describe one."""
    eval_path = Path(eval_log_path)
    log = read_eval_log(str(eval_path), resolve_attachments=True)
    if len(log.samples or []) > 1:
        raise ValueError(f"{eval_path}: a run holds one sample, this log has {len(log.samples)}")
    run_dir = Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    clear_outputs(run_dir)
    views = []
    for sample in log.samples or []:
        view = SampleView.from_eval_sample(sample, log)
        view.eval_log = str(eval_path.resolve())
        views.append(view)
    records = [r for view in views for r in sample_records(view, run_dir)]
    for view in views:
        if (bench := domain(view)) is not None:
            bench.write_files(view, run_dir)
    block, opens = access_log(views[0]) if views else (None, None)
    run = run_json(views[0] if views else None, eval_path, run_dir, records, log.eval.eval_id, log.eval.model, block)
    (run_dir / RUN_JSON).write_text(json.dumps(run, indent=1))
    write_jsonl(run_dir / EPISODES, records)
    if opens is not None:
        write_jsonl(run_dir / ACCESS_LOG, opens)
    rows = judge_rows(views, records, verdicts)
    if rows:
        write_jsonl(run_dir / JUDGE, rows)
    return {"out_dir": str(run_dir), "episodes": len(records), "topology": run["topology"],
            "with_transcript": [r["episode"] for r in records if r["transcript_path"]],
            "without_transcript": [r["episode"] for r in records if not r["transcript_path"]],
            "judged": len(rows)}
