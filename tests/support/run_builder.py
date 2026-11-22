"""A run directory as the export writes it, for the tests that read one without running anything.

    folder = write_run_dir(tmp_path / "run", {"task": "chess"}, {"a01": transcript(calls)}, row="LR4pA", role="worker")
    write_verdicts(folder, [{"episode": "LR4pA#1@a01", "judge": "gpt-6-sol", "cheated": True}])   # judges/cheatbench.*
    write_status_log(tmp_path / "logs" / "study" / "run" / "x.eval", "success")     # an eval log's header alone
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from inspect_ai.event import SpanBeginEvent, ToolEvent
from inspect_ai.log import EvalDataset, EvalLog, EvalSample, EvalSpec, write_eval_log

from src.utils.json_files import write_jsonl
from tests.support.run_files import write_text


def write_run_dir(folder: Path, run: dict, transcripts: dict[str, dict], *, row: str = "q1",
              episodes: dict[str, dict] | None = None, **fields) -> Path:
    """Sample <row>#1: run.json, each agent's transcript and its episode record, with `fields` and the agent's own
    fields in `episodes`."""
    folder.mkdir(parents=True)
    write_text(folder / "processed/run.json", json.dumps(run))
    records = []
    for agent, transcript in transcripts.items():
        record = {"id": row, "replicate": 1, "episode": f"{row}#1@{agent}", "agent": agent, **fields,
                  **(episodes or {}).get(agent, {})}
        records.append(record)
        path = folder / "processed/trajectories" / record["episode"] / "transcript.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(transcript))
    write_jsonl(folder / "processed/episodes.jsonl", records)
    return folder


def write_verdicts(folder: Path, rows: list[dict], judge: str = "gpt-6-luna") -> Path:
    """CheatBench verdict rows as the export and the judge passes store them, judges/cheatbench.<judge>.jsonl, keyed
    by unit, the episode. A row names its judge, else `judge`; each judge gets its own file."""
    by_judge: dict[str, list[dict]] = {}
    for row in rows:
        named = row.get("judge") or judge
        by_judge.setdefault(named, []).append({"unit": row["episode"], **row, "judge": named})
    (folder / "analysis/judges").mkdir(parents=True, exist_ok=True)
    for named, held in by_judge.items():
        write_jsonl(folder / "analysis/judges" / f"cheatbench.{named}.jsonl", held)
    return folder / "analysis/judges"


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


def write_status_log(path: Path, status: str) -> Path:
    """An .eval log with no sample whose header holds this status (success, error, cancelled or started)."""
    spec = EvalSpec(created="2026-11-01T03:31:41+00:00", task="t", dataset=EvalDataset(), model="mockllm/model",
                    config={})
    path.parent.mkdir(parents=True, exist_ok=True)
    write_eval_log(EvalLog(eval=spec, status=status), str(path))
    return path
