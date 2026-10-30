"""A run directory as the export writes it, for the tests that read one without running anything.

    folder = write_run_dir(tmp_path / "run", {"task": "chess"}, {"a01": transcript(calls)}, row="LR4pA", role="worker")
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from inspect_ai.event import SpanBeginEvent, ToolEvent
from inspect_ai.log import EvalDataset, EvalLog, EvalSample, EvalSpec, write_eval_log

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


def write_tool_windows(path: Path, windows: list[tuple[str, float, float | None]]) -> Path:
    """An .eval log of one sample with one shell call per window (agent, start, end, in seconds since the epoch, end
    None for a call that never finished), each in its agent's span, as ORBIT logs them."""
    def moment(seconds: float) -> datetime:
        return datetime.fromtimestamp(seconds, timezone.utc)

    agents = sorted({agent for agent, _, _ in windows})
    events = [SpanBeginEvent(id=agent, name=agent, type="agent") for agent in agents]
    events += [ToolEvent(id=f"call{k}", function="shell", arguments={}, span_id=agent, timestamp=moment(start),
                         completed=moment(end) if end else None) for k, (agent, start, end) in enumerate(windows)]
    spec = EvalSpec(created="2026-10-30T14:00:00+00:00", task="poem", dataset=EvalDataset(), model="mockllm/model",
                    config={})
    write_eval_log(EvalLog(eval=spec, samples=[EvalSample(id="museum_poem", epoch=1, input="", target="",
                                                          events=events)]), str(path))
    return path
