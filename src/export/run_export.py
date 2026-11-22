"""An Inspect .eval log from an ORBIT run, written as the run directory CheatBench's judge and our browser read.

    write_run(eval_log_path, run_dir, verdicts=None, replace=False)

Writes
    <run_dir>/run.json                                 the run: task, model, roster, defenses, team, config, rules
    <run_dir>/episodes.jsonl                           one record per (sample, agent)
    <run_dir>/access_log.jsonl                         one row per open of the honeypot, for a run that kept a log
                                                       (src/export/_export_access_log.py)
    <run_dir>/network.jsonl                            one row per socket call in the agents' machines, for a run that
                                                       kept a network log (src/export/_export_network_log.py)
    <run_dir>/judges/cheatbench.<judge>.jsonl          one CheatBench verdict row per judged episode, by the judge
                                                       that ran in the eval (src/judging/verdict_files.py)
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
"delivered" (stop_after_delivery), "rounds" (it used up its rounds), "cap" (the step cap of a run before 2026-11-21),
"context", "limit", "error" or "end" (the plan ran out or its window closed). In an older log ORBIT's own submit reads
as the act it was: a stop in a team, a hand-in for a lone agent and in solo_task. A log without the turn rules' store
has none (None).

An episode's "model" is the agent's own model: its role's model when the run gave the role one, else the eval's model.
The eval log names a role model "swarm/<model>" (src/models/swarm_model_provider.py) and the export drops the
prefix. A run that recorded role models gets run.json "role_models" ({role: Inspect string or None}) next to "model".

Blocks written when the run has them: run.json "turn_budget" ({budget, steps_per_agent}, a run before 2026-11-21),
"access" (the access policy and what each agent held: its items and numbered pieces, and on openmath's split layout the
team remote, the honeypot branches and the archive), the domain's own blocks (Benchmark.run_blocks: "openmath" and
"deliverables", "swe", "knowledge", "interview", "protein", "creative", "chess"), "honeypot_probe" (the honeypot probe's
report by owner: agents, remote, shared), "delivery" (the stop-after-delivery record), "access_log" (the access log's
status, for a run that recorded the switch: src/export/_export_access_log.py), "network" (the network log's status, its
counts by agent and op and the cross-agent connections, for a run that recorded the switch:
src/export/_export_network_log.py), "tmp_snapshots" (the status of the run's own /tmp snapshots, written beside it
as tmp_snapshots.json: src/export/_export_tmp_snapshots.py); and an episode's "access", the domain's facts
(Benchmark.episode_facts) and "delivery" (the round at which its deliverable was first seen, and its rounds used up
by then). The export never writes run.json "signals": the code record (src/measures/code_ladder.py) adds it after the
export.

Paths are relative, so a run reads the same in any checkout and in the dataset: run.json "eval_log" is the log's path
from the checkout (logs/<study>/[<group>/]<run>/<file>.eval, run_directory.log_link), and an episode's or a verdict's
"transcript_path" is the transcript's path from the run's folder.

run.json's orbit_version and inspect_version, and each episode's and transcript's agent version, name what ran the
eval, not what exports it: Inspect's version from the log (EvalSpec.packages), ORBIT's from an earlier export of the
same eval (earlier_export; the log does not record it), else the running ones.

Episode ids: "<sample>#<epoch>" for a single agent, "<sample>#<epoch>@<agent>" otherwise (export_episodes.episode_id).
Verdicts come from the `verdicts` argument ({episode: verdict}, when re-judging), which replace the judge's stored rows
of those episodes, or from the cheatbench_judge score in the log, which only fill episodes the judge's file lacks, so a
re-export never undoes a judge pass. A verdict row is the episode's identity (unit = the episode, and its agent and
role), the verdict, and the transcript the judge read; judges/ holds the judge passes' files too and the export never
clears it.

A run directory holds one eval's run. write_run refuses a directory whose run.json records another eval
(RunFolderTaken), so a rerun under a name in use never replaces a run unasked. The same eval's log exports into its
own directory again (regenerate, rejudge). replace=True first sets the earlier run aside into
runs/<study>/attempts/<name>.<n>/ with its eval log and its figures (run_directory.set_aside_attempt), so no file of
the earlier eval stays beside the new one and nothing is deleted. run.json's run_id is the run's own name
(run_directory.run_name), also for an attempt exported again; a re-export keeps the run_id the earlier export of the
same eval recorded (earlier_export).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import orbit
from inspect_ai.log import read_eval_log

from src.export._export_access_log import access_log
from src.export._export_network_log import NETWORK_LOG, network_log
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
from src.export._export_tmp_snapshots import write_tmp_snapshots
from src.export.atif_transcripts import model_key
from src.export.eval_sample_view import SampleView, logged_inspect_version, orbit_halt_reason
from src.export.export_episodes import sample_key, sample_records
from src.judging import verdict_files
from src.judging.cheatbench_judge import JUDGE_SCORER
from src.measures.run_directory import ACCESS_LOG, EPISODES, RUN_JSON, log_link, run_name, set_aside_attempt
from src.models import swarm_model_provider as provider
from src.utils.json_files import read_json, write_jsonl

# The identity a verdict row takes from its episode record, ahead of the verdict.
VERDICT_IDENTITY = ("id", "episode", "replicate", "task", "model", "model_key", "agent", "role")
OWNED_FILES = (RUN_JSON, EPISODES, ACCESS_LOG, NETWORK_LOG)
OWNED_DIRS = ("trajectories", "communication", "defense", "swe")


def run_json(view: SampleView | None, eval_path: Path, run_dir: Path, records: list[dict], eval_id: str,
             model: str, access_log_block: dict | None, versions: tuple[str, str], run_id: str,
             network_block: dict | None = None) -> dict:
    """run.json: what every run carries, then, for a log with a sample, what the first sample recorded. versions is
    (ORBIT's, Inspect's) that ran the eval and run_id the run's name, as write_run reads them; network_block flags the
    validity, and write_run adds it last."""
    first = records[0] if records else {}
    task = first.get("task")
    run = {
        "task": task, "run_id": run_id, "model": provider.inspect_string(model),
        "model_key": model_key(provider.inspect_string(model)),
        "topology": first.get("topology", "single"), "agents": first.get("orbit_roster", []),
        "eval_log": log_link(eval_path, run_dir), "eval_id": eval_id,
        "orbit_version": versions[0], "inspect_version": versions[1],
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
        "engagement": run_engagement(records),
        "validity": validity_block(records, submission, access_log_block, network_block),
        "ledger": ledger_block(records, view.limit.type if view.limit else None),
        **recorded_run_blocks(view), **({"access_log": access_log_block} if access_log_block else {}),
    }


def judge_rows(views: list[SampleView], records: list[dict], verdicts: dict[str, dict] | None) -> list[dict]:
    """The verdict rows of the judge that ran in the eval, from `verdicts` ({episode: verdict}) or from each sample's
    judge score ({agent: verdict}): unit (the episode), the episode's identity (VERDICT_IDENTITY), the verdict, the
    transcript the judge read (its path from the run's folder) and the keys verdict_files.ROW_KEYS asks for, None where
    the eval recorded none (an eval's judge keeps no input hash). An episode without a transcript gets none."""
    by_sample = {sample_key(v): v for v in views}
    rows = []
    for record in records:
        meta = score_metadata(by_sample[f"{record['id']}#{record['replicate']}"], JUDGE_SCORER) or {}
        if verdicts is not None:
            verdict = verdicts.get(record["episode"])
        else:
            verdict = (meta.get("verdicts") or {}).get(record["agent"])
        if verdict and record["transcript_path"]:
            spec = meta.get("spec") or {}
            row = {"unit": record["episode"], **{key: record.get(key) for key in VERDICT_IDENTITY}, **verdict}
            row.update({key: row.get(key, spec.get(key)) for key in verdict_files.ROW_KEYS})
            rows.append({**row, "transcript_path": record["transcript_path"]})
    return rows


def write_verdicts(run_dir: Path, rows: list[dict], *, replace: bool) -> int:
    """Write the eval judge's rows into judges/cheatbench.<judge>.jsonl, by judge. replace: each row replaces the stored
    row of its episode (verdicts just made); else a row only fills an episode the file lacks (verdicts read from the
    log, which a judge pass may have redone). Returns the rows written."""
    written = 0
    for judge in sorted({row["judge"] for row in rows}):
        mine = [row for row in rows if row["judge"] == judge]
        if not replace:
            held = verdict_files.by_unit(verdict_files.rows(run_dir, verdict_files.CHEATBENCH_PASS, judge))
            mine = [row for row in mine if row["unit"] not in held]
        if mine:
            verdict_files.write(run_dir, verdict_files.CHEATBENCH_PASS, judge, mine)
            written += len(mine)
    return written


def clear_outputs(run_dir: Path) -> None:
    """Remove what an earlier export of this run wrote, so stale verdicts or transcripts never pair with new episodes.

    Only the files and directories this module writes are removed; the measures' files (signals.jsonl,
    swe_summary.json) stay.
    """
    for name in OWNED_FILES:
        (run_dir / name).unlink(missing_ok=True)
    for name in OWNED_DIRS:
        shutil.rmtree(run_dir / name, ignore_errors=True)


PYTHON_REMEDY = "Pass replace=True to set the earlier run aside into runs/<study>/attempts/<name>.<n>/ first."
"""The remedy a caller of write_run in Python reads."""


class RunFolderTaken(ValueError):
    """The run directory holds the run of another eval, and the caller did not ask to replace it. `base` names the
    folder and both evals. The message adds a remedy: PYTHON_REMEDY, unless a command puts its own after `base`."""

    def __init__(self, base: str, remedy: str = PYTHON_REMEDY) -> None:
        super().__init__(f"{base} {remedy}")
        self.base = base


def earlier_export(run_dir: Path, eval_id: str) -> dict:
    """run_dir's run.json when it is an earlier export of this same eval, else {}. A re-export keeps two of its
    fields: orbit_version, which the eval log does not record (the one written when the run was first exported, right
    after its eval), and run_id, the run's name, which a collection that names its folders otherwise (apart_controls'
    <model>/<task>/<cell>/s<seed>) does not give."""
    path = Path(run_dir) / RUN_JSON
    held = (read_json(path) or {}) if path.is_file() else {}
    return held if held.get("eval_id") == eval_id else {}


def refuse_replacing(run_dir: Path, eval_id: str | None) -> None:
    """Raise RunFolderTaken when run_dir holds the run of an eval other than eval_id: its run.json records another
    eval_id, or none. eval_id None stands for an eval not made yet, which no run.json can hold. A folder without
    run.json holds no run."""
    path = Path(run_dir) / RUN_JSON
    if not path.is_file():
        return
    held = (read_json(path) or {}).get("eval_id")
    if eval_id is not None and held == eval_id:
        return
    holds = f"the run of eval {held}" if held else "a run with no eval_id"
    new = f"eval {eval_id}" if eval_id else "a new eval"
    raise RunFolderTaken(f"{run_dir} holds {holds}. Writing {new} there would replace it.")


def write_run(eval_log_path: Path, run_dir: Path, *, verdicts: dict[str, dict] | None = None,
              replace: bool = False) -> dict:
    """Write the run directory for one eval log, which holds one sample; return a short summary of what was written. A
    log of several samples (inspect eval --epochs) is refused: run.json and the code's evidence describe one. A
    directory that holds another eval's run is refused before anything is written (refuse_replacing). With replace, that
    earlier run is set aside into runs/<study>/attempts/<name>.<n>/ first, its eval log and figures with it; the log
    being exported stays where it is."""
    eval_path = Path(eval_log_path)
    log = read_eval_log(str(eval_path), resolve_attachments=True)
    if len(log.samples or []) > 1:
        raise ValueError(f"{eval_path}: a run holds one sample, this log has {len(log.samples)}")
    run_dir = Path(run_dir).resolve()
    try:
        refuse_replacing(run_dir, log.eval.eval_id)
    except RunFolderTaken:
        if not replace:
            raise
        set_aside_attempt(run_dir, keep=eval_path)
    run_dir.mkdir(parents=True, exist_ok=True)
    earlier = earlier_export(run_dir, log.eval.eval_id)
    versions = (earlier.get("orbit_version") or orbit.__version__, logged_inspect_version(log))
    clear_outputs(run_dir)
    views = [SampleView.from_eval_sample(sample, log, orbit_version=versions[0]) for sample in log.samples or []]
    records = [r for view in views for r in sample_records(view, run_dir)]
    for view in views:
        if (bench := domain(view)) is not None:
            bench.write_files(view, run_dir)
    block, opens = access_log(views[0]) if views else (None, None)
    network, sockets = network_log(views[0]) if views else (None, None)
    run = run_json(views[0] if views else None, eval_path, run_dir, records, log.eval.eval_id, log.eval.model, block,
                   versions, earlier.get("run_id") or run_name(run_dir), network)
    if network:
        run["network"] = network
    (run_dir / RUN_JSON).write_text(json.dumps(run, indent=1))
    write_jsonl(run_dir / EPISODES, records)
    if opens is not None:
        write_jsonl(run_dir / ACCESS_LOG, opens)
    if sockets is not None:
        write_jsonl(run_dir / NETWORK_LOG, sockets)
    rows = judge_rows(views, records, verdicts)
    tmp = write_tmp_snapshots(views[0] if views else None, run_dir)
    if tmp is not None:
        run["tmp_snapshots"] = tmp
        (run_dir / RUN_JSON).write_text(json.dumps(run, indent=1))
    return {"out_dir": str(run_dir), "episodes": len(records), "topology": run["topology"],
            "with_transcript": [r["episode"] for r in records if r["transcript_path"]],
            "without_transcript": [r["episode"] for r in records if not r["transcript_path"]],
            "judged": write_verdicts(run_dir, rows, replace=verdicts is not None)}
