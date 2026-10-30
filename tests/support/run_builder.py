"""A run directory as the export writes it, for the tests that read one without running anything.

    folder = write_run_dir(tmp_path / "run", {"task": "chess"}, {"a01": transcript(calls)}, row="LR4pA", role="worker")
"""
from __future__ import annotations

import json
from pathlib import Path

from src.utils.json_files import write_jsonl


def write_run_dir(folder: Path, run: dict, transcripts: dict[str, dict], *, row: str = "q1",
              episodes: dict[str, dict] | None = None, **fields) -> Path:
    """Sample <row>#1: run.json, each agent's transcript and its episode record, with `fields` and the agent's own
    fields in `episodes`."""
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(json.dumps(run))
    records = []
    for agent, transcript in transcripts.items():
        record = {"id": row, "replicate": 1, "episode": f"{row}#1@{agent}", "orbit_agent": agent, **fields,
                  **(episodes or {}).get(agent, {})}
        records.append(record)
        path = folder / "trajectories" / record["episode"] / "transcript.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(transcript))
    write_jsonl(folder / "episodes.jsonl", records)
    return folder
