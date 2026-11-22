"""A run's measures as processing stores them (src/ui/transcript_index.py stored_measures), made in memory by the
pipeline's own parts, for tests that read a real run without writing it."""
from __future__ import annotations

from pathlib import Path

from src.measures import coordination, topology_fact
from src.measures.process_runs import Job
from src.measures.run_directory import RunDir
from src.measures.run_listing import Listed
from src.ui import viewer_parts


def measures_now(run_dir: Path) -> dict:
    """{"cheatbench", "cheating", "coordination", "collusion", "labels"} of the run, nothing written."""
    job = Job(RunDir(run_dir), "c", Listed(Path(run_dir).name, Path(run_dir), "valid"))
    fact = topology_fact.topology(job.run)
    judged = viewer_parts.judged_part(job)
    return {"cheatbench": judged["cheatbench"], "cheating": viewer_parts.cheating_part(job),
            "coordination": coordination.coordination_of(topology_fact.boards(fact), topology_fact.roles(fact)),
            "collusion": viewer_parts.collusion_part(job), "labels": judged["labels"]}
