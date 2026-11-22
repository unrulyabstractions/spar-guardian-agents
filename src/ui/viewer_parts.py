"""The pipeline's label and row parts (src/configuration/pipeline_parts.yaml): what the transcript viewer serves, made
by `python -m src process` and stored in the run's analysis/ folder, so the viewer computes nothing.

    cheating_part(job)     # analysis/labels/cheating.json: the Cheating ladder with CheatBench's labels beside it
    collusion_part(job)    # analysis/labels/collusion.json: the Collusion ladder
    judged_part(job)       # analysis/labels/judged.json: the judge labels and CheatBench's verdicts
    row_part(job)          # analysis/row.json: the runs page's row (transcript_index.build_row)

Each label holds what the viewer keeps of its ladder (src/ui/transcript_levels.py cheating_shown, collusion_shown), or
{"applicable": False, "reason"} where the ladder does not apply. Coordination is src/measures/coordination.py
coordination_part, from the stored topology. A job is src/measures/process_runs.py Job.
"""
from __future__ import annotations

from src.domains import BY_TASK
from src.judging import verdict_files
from src.measures import collusion, judge_labels
from src.ui import transcript_index, transcript_levels


def cheating_part(job) -> dict:
    """The Cheating ladder of the run with CheatBench's labels beside its clauses (judge_labels.judged_cheating) as the
    viewer keeps it, or {"applicable": False, "reason"} for a run without episodes or of a task with no ladder."""
    rf = transcript_index.RunFiles.read(job.run.path)
    if rf.task not in BY_TASK:
        return {"applicable": False, "reason": f"no Cheating ladder for task {rf.task!r}"}
    if not rf.episodes:
        return {"applicable": False, "reason": "the run recorded no episode"}
    return transcript_levels.cheating_shown(judge_labels.judged_cheating(job.run.path))


def collusion_part(job) -> dict:
    """The Collusion ladder of the run (collusion.run_collusion) as the viewer keeps it, or {"applicable": False,
    "reason"} when the measure says it does not apply (collusion.not_applicable), which loads no transcript."""
    rf = transcript_index.RunFiles.read(job.run.path)
    reason = collusion.not_applicable(rf.task, len(rf.episodes))
    if reason:
        return {"applicable": False, "reason": reason}
    return transcript_levels.collusion_shown(collusion.run_collusion(job.run.path))


def judged_part(job) -> dict:
    """{"labels": each agent's judge labels (judge_labels.labels: each label's score, excerpt, reason and located
    place), "cheatbench": CheatBench's verdicts, {judge: {episode: row}} (verdict_files.cheatbench)}."""
    return {"labels": judge_labels.labels(job.run.path), "cheatbench": verdict_files.cheatbench(job.run.path)}


def row_part(job) -> dict:
    """The run's row on the runs page (transcript_index.build_row), from its files and its stored labels."""
    rf = transcript_index.RunFiles.read(job.run.path)
    return transcript_index.build_row(job.col, job.listed, rf, transcript_index.stored_measures(job.run))
