"""An Inspect .eval log from an ORBIT run, written as the run directory CheatBench's judge and our browser read.

    write_run(eval_log_path, run_dir, verdicts=None)

Writes
    <run_dir>/run.json                                 the run: task, model, roster, defenses, team, config, rules
    <run_dir>/episodes.jsonl                           one record per (sample, agent)
    <run_dir>/judge.jsonl                              one CheatBench verdict row per judged episode
    <run_dir>/trajectories/<episode>/transcript.json   the ATIF transcript the judge read
    (src/benchmark/atif_transcripts.py)
    <run_dir>/trajectories/<episode>/prompt.txt
    <run_dir>/communication/<sample>.json              channel messages and events, if any
    <run_dir>/defense/<sample>.json                    defense configuration and interventions, if any
    <run_dir>/swe/diffs/<target>.diff                  for a SWE run, the graded diffs

run.json also carries the run's mode, scale and preset, and each episode and the run an `engagement` block: who
looked at the board, read it, posted and submitted for the team (None for a run without a board).

An episode's "model" is the agent's own model: its role's model when the run gave the role one, else the eval's model.
The eval log names a role model "swarm/<model>" (src/configuration/swarm_model_provider.py); the export drops
the prefix. A run
that recorded role models gets run.json "role_models" ({role: Inspect string or None}) next to "model".

Facts that only newer runs record are written only when the run has them, so a run of the October 2026 sweep exports
byte for byte as before (the smoke runs of 395e9c8 recorded split_access and archive_part, which nothing reads any
more): run.json "turn_budget" ({budget, steps_per_agent}), "access" (the access policy and what each agent held:
its items, and on openmath its honeypot branches and archive slices, on knowledge work the review tool and the
artifact read), "knowledge" (the knowledge-work sample and its reviews), "interview" and "protein" (a machines
domain's sample and its tool's states and calls), "delivery" (the stop-after-delivery record); the engagement keys
oversight, oversight_targets, reads_refused and posts_refused; and an episode's "access", "review" (its grader state on
knowledge work) and "delivery" (the round and working step at which its deliverable was first seen). An episode that
stop_after_delivery ended has ended_by "delivered".

Episode ids: "<sample>#<epoch>" for a single agent, "<sample>#<epoch>@<agent>" otherwise.
Verdicts come from the `verdicts` argument (re-judging) or from the cheatbench_judge score in the log.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import inspect_ai
import orbit
from inspect_ai.log import read_eval_log

from src.benchmark.cheatbench_judge import JUDGE_SCORER
from src.benchmark.eval_sample_view import SampleView, orbit_halt_reason
from src.benchmark.export_episodes import episode_model, sample_key, sample_records
from src.benchmark.export_run_blocks import (
    IDENTICAL_RUN_FLAG,
    deliverables_block,
    delivery_block,
    ended_by_delivery,
    identical_tool_run,
    ledger_block,
    ledger_record,
    newer_episode_facts,
    newer_run_facts,
    probes_block,
    rules_state,
    run_deviations,
    run_engagement,
    run_identity,
    score_metadata,
    shutdown_rate,
    swarm_block,
    swe_block,
    team_submission,
    validity_block,
    validity_record,
)
from src.configuration import settings
from src.configuration import swarm_model_provider as provider
from src.utils.json_files import write_jsonl

# The names the rest of the repository imports from here. The blocks live in export_run_blocks.py and the per-sample
# writers in export_episodes.py.
__all__ = [
    "IDENTICAL_RUN_FLAG", "clear_outputs", "delivery_block", "ended_by_delivery", "episode_model",
    "identical_tool_run", "judge_rows", "ledger_block", "ledger_record", "newer_episode_facts", "newer_run_facts",
    "run_json", "sample_records", "validity_block", "validity_record", "write_run",
]


def run_json(view: SampleView | None, eval_path: Path, run_dir: Path, records: list[dict], eval_id: str,
             model: str) -> dict:
    first = records[0] if records else {}
    task = first.get("task")
    topo = first.get("topology", "single")
    meta = view.metadata if view else {}
    cb = meta.get("cheatbench") or {}
    return {
        "task": task, "task_root": str((settings.CHEATBENCH / "tasks" / (task or "unknown")).resolve()),
        "variant": "", "run_id": run_dir.name, "model": provider.inspect_string(model),
        **({"role_models": cb["role_models"]} if "role_models" in cb else {}), "agent": "orbit",
        "source": "orbit", "topology": topo, "agents": first.get("orbit_roster", []),
        "control": first.get("control"), "monitor_model": first.get("monitor_model"),
        "defenses": (meta.get("experiment", {}).get("defenses") if view else None) or [],
        "swarm": swarm_block(view) if view else None,
        "deliverables": deliverables_block(view) if view else None,
        "orbit_halt_reason": orbit_halt_reason(view) if view else None,
        "eval_log": str(eval_path.resolve()), "eval_id": eval_id,
        "orbit_version": orbit.__version__, "inspect_version": inspect_ai.__version__,
        "swe": swe_block(view, run_dir) if view else None,
        "deviations": run_deviations(view) if view else [],
        "config": (meta.get("cheatbench") or {}).get("config") if view else None,
        "shutdown_rate": shutdown_rate((meta.get("cheatbench") or {}).get("config"), records) if view else None,
        "rules": rules_state(view) if view else None,
        "probes": probes_block(view) if view else None,
        **(run_identity(view, task) if view else {"mode": None, "scale": None, "preset": None}),
        "engagement": run_engagement(records, team_submission(view)) if view else None,
        "validity": validity_block(records, team_submission(view)) if view else None,
        "ledger": ledger_block(records, view.limit.type if view and view.limit else None) if view else None,
        **(newer_run_facts(view) if view else {}),
    }


def judge_rows(views: list[SampleView], records: list[dict], verdicts: dict[str, dict] | None) -> list[dict]:
    """judge.jsonl rows from `verdicts` (keyed by episode or agent) or from each sample's judge score."""
    by_sample = {sample_key(v): v for v in views}
    rows = []
    for record in records:
        agent = record["orbit_agent"]
        if verdicts is not None:
            verdict = verdicts.get(record["episode"]) or verdicts.get(agent)
        else:
            view = by_sample[f"{record['id']}#{record['replicate']}"]
            verdict = ((score_metadata(view, JUDGE_SCORER) or {}).get("verdicts") or {}).get(agent)
        if not verdict or not record["transcript_path"]:
            continue
        fields = {k: v for k, v in verdict.items() if k not in ("id", "episode", "transcript_path")}
        rows.append({"id": record["id"], "episode": record["episode"], **fields,
                     "transcript_path": record["transcript_path"]})
    return rows


OWNED_FILES = ("run.json", "episodes.jsonl", "judge.jsonl")
OWNED_DIRS = ("trajectories", "communication", "defense", "swe")


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
    """Write the run directory for one eval log; return a short summary of what was written."""
    eval_path = Path(eval_log_path)
    log = read_eval_log(str(eval_path), resolve_attachments=True)
    run_dir = Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    clear_outputs(run_dir)
    views = []
    for sample in log.samples or []:
        view = SampleView.from_eval_sample(sample, log)
        view.eval_log = str(eval_path.resolve())
        views.append(view)
    records = [r for view in views for r in sample_records(view, run_dir)]
    run = run_json(views[0] if views else None, eval_path, run_dir, records, log.eval.eval_id, log.eval.model)
    (run_dir / "run.json").write_text(json.dumps(run, indent=1))
    write_jsonl(run_dir / "episodes.jsonl", records)
    rows = judge_rows(views, records, verdicts)
    if rows:
        write_jsonl(run_dir / "judge.jsonl", rows)
    return {"out_dir": str(run_dir), "episodes": len(records), "topology": run["topology"],
            "with_transcript": [r["episode"] for r in records if r["transcript_path"]],
            "without_transcript": [r["episode"] for r in records if not r["transcript_path"]],
            "judged": len(rows)}
