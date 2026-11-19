"""Every tool call of a stored run in one global order, from its record alone: the transcripts give each call's
agent and its number k (as the transcript scan and signals.jsonl number it), the eval log's tool events give its
arguments, output, error and times.

    calls = recorded_calls(RunDir(path))       # [RecordedCall(n=1, agent="k7q2m", k=0, ...), ...]

The global order is each agent's calls in k order, merged by the eval log's start time (then the agent, then k), so no
agent's calls ever change order. n counts from 1. Pure: nothing is written.
"""
from __future__ import annotations

import heapq
import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from src.measures.run_directory import RunDir, episode_agent
from src.measures.shell_commands import results_of, tool_calls_of

ATTACHMENT = "attachment://"


class ReplayRefused(RuntimeError):
    """The record cannot be replayed as it stands: the reason says what is missing or different."""


class RecordedCall(NamedTuple):
    """One recorded tool call. error is the eval log's error type ("timeout", "parsing", ...) or None; t0 and t1
    the start and end in seconds since the epoch."""
    n: int
    agent: str
    k: int
    id: str
    tool: str
    arguments: dict
    result: str
    error: str | None
    t0: float
    t1: float


def resolve(value, attachments: dict[str, str]):
    """The value with every attachment://<hash> replaced by its text, at any depth."""
    if isinstance(value, str) and value.startswith(ATTACHMENT):
        return attachments.get(value[len(ATTACHMENT):], value)
    if isinstance(value, dict):
        return {k: resolve(v, attachments) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, attachments) for v in value]
    return value


def result_text(value) -> str:
    """A tool result as text: a string as it is, content blocks joined by their text."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(str(block.get("text", "")) if isinstance(block, dict) else str(block) for block in value)
    return "" if value is None else str(value)


def seconds(stamp: str | None) -> float | None:
    return datetime.fromisoformat(stamp).timestamp() if stamp else None


def tool_events(eval_log: Path) -> dict[str, dict]:
    """call id -> {tool, arguments, result, error, t0, t1} from the eval log's one sample. More than one sample is
    refused: a replay plays back one sandbox."""
    with zipfile.ZipFile(eval_log) as archive:
        names = [n for n in archive.namelist() if n.startswith("samples/")]
        if len(names) != 1:
            raise ReplayRefused(f"{eval_log.name} holds {len(names)} samples; a replay plays back one")
        sample = json.loads(archive.read(names[0]))
    attachments = sample.get("attachments") or {}
    out = {}
    for event in sample.get("events") or []:
        if event.get("event") != "tool":
            continue
        error = event.get("error")
        out[event["id"]] = {"tool": event["function"], "arguments": resolve(event.get("arguments") or {}, attachments),
                            "result": result_text(resolve(event.get("result"), attachments)),
                            "error": (error or {}).get("type") if error else None,
                            "t0": seconds(event.get("timestamp")), "t1": seconds(event.get("completed"))}
    return out


def agent_calls(run: RunDir) -> dict[str, list[tuple[int, str, str, dict, str]]]:
    """agent -> [(k, call id, tool, arguments, transcript result)] from the stored transcripts."""
    out: dict[str, list[tuple[int, str, str, dict, str]]] = {}
    for episode in run.with_transcripts():
        transcript = run.transcript(episode["episode"])
        results = results_of(transcript)
        out[episode_agent(episode)] = [(k, tc["tool_call_id"], tc["function_name"], tc.get("arguments") or {},
                                        results.get(tc["tool_call_id"], ""))
                                       for k, tc in tool_calls_of(transcript)]
    return out


def merge_by_start(per_agent: dict[str, list[tuple[float, int, object]]]) -> list[object]:
    """Each agent's items (start, k, item), already in k order, merged into one list by start, then agent, then k. An
    agent's items keep their order even where a later one started first."""
    streams = [[(start, agent, k, i, item) for i, (start, k, item) in enumerate(items)]
               for agent, items in sorted(per_agent.items())]
    heads = [(stream[0], s, 0) for s, stream in enumerate(streams) if stream]
    heapq.heapify(heads)
    out = []
    while heads:
        (start, agent, k, i, item), s, j = heapq.heappop(heads)
        out.append(item)
        if j + 1 < len(streams[s]):
            nxt = streams[s][j + 1]
            # a later call of the same agent never sorts before this one
            heapq.heappush(heads, ((max(nxt[0], start), *nxt[1:]), s, j + 1))
    return out


def recorded_calls(run: RunDir) -> list[RecordedCall]:
    """The run's calls in global order (merge_by_start). A call the transcripts hold and the eval log does not is
    refused, as is a run without an eval log."""
    log = run.eval_log
    if log is None:
        raise ReplayRefused(f"{run.path.name}: no eval log (run.json eval_log names none that exists)")
    events = tool_events(log)
    per_agent: dict[str, list[tuple[float, int, dict]]] = {}
    for agent, calls in agent_calls(run).items():
        rows = []
        for k, cid, tool, arguments, transcript_result in calls:
            event = events.get(cid)
            if event is None or event["t0"] is None:
                raise ReplayRefused(f"{run.path.name}: {agent}'s call {k} ({cid}) has no tool event with a start time")
            rows.append((event["t0"], k, {"agent": agent, "k": k, "id": cid, "tool": tool,
                                          "arguments": event["arguments"] or arguments,
                                          "result": event["result"] if event["result"] or not transcript_result
                                          else transcript_result,
                                          "error": event["error"], "t0": event["t0"],
                                          "t1": event["t1"] if event["t1"] is not None else event["t0"]}))
        per_agent[agent] = rows
    return [RecordedCall(n=n, **row) for n, row in enumerate(merge_by_start(per_agent), 1)]
