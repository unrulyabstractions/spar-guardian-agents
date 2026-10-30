"""The results browser's views of one run's Inspect eval log: the sample facts, the context windows and the timeline.

`LogViews` reads the log through a `Store` and builds each view on request; the index of model calls is built once
per run. The eval-log helpers are the export's (src/export/eval_sample_view.py and atif_transcripts.py), so the
browser and the transcripts read owners, blocks and usage the same way.

    views = LogViews(Store(Path("runs")))
    views.context_call("riemann_minimal", 3)["input"]
    views.timeline("riemann_minimal")["first_opened"]
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from inspect_ai.event import ModelEvent

from src.export.atif_transcripts import REFUSAL_TEXT, WITHHELD_TEXT, output_message, split_content, tool_result_text
from src.export.eval_sample_view import blocked_message_ids, span_owners, usage_dict
from src.sandbox import task_tool_store
from src.swarm.turn_rules.turn_state import TURN_RULES_TAG
from src.ui.run_store import Store
from src.utils.json_files import read_json

# Message metadata keys that mark what the provider saw, and the flag each becomes.
FLAG_KEYS = {TURN_RULES_TAG: "turn_rules", "orbit_defense_redacted": "redacted"}
SAMPLE_STORE_KEYS = (
    "EnvironmentState:turn", "EnvironmentState:last_text_by_agent",
    "DefenseLog:blocks", "DefenseLog:activity", "DefenseLog:vaccination_injections",
    task_tool_store.STATES, task_tool_store.CALLS,
)
# The code ladder's moments the timeline places (src/measures/code_ladder.py): the call that first showed the agent a
# pointer to the honeypot, and the call that first showed it the honeypot's content.
SIGNAL_RUNGS = ("exposed", "opened")


def message_dict(m, redacted: set, blocked: set = frozenset(), own_output: bool = False) -> dict:
    """One message as the provider saw it.

    A withheld message carries the placeholder as text and the original under withheld_text. A refusal sentence is
    flagged output_blocked only when it is a call's own output that a defense blocked (its id is in `blocked`); in an
    input history the same sentence is refusal_in_history, since the substituted message carries a new id and the
    model may also write the sentence itself.
    """
    text, reasoning = split_content(m.content)
    if m.role == "tool":
        text = tool_result_text(m.content)
    flags = {flag for key, flag in FLAG_KEYS.items() if (m.metadata or {}).get(key)}
    d = {"role": m.role, "id": m.id, "text": text, "reasoning": reasoning}
    if m.id in redacted:
        flags.add("redacted")
        d.update(text=WITHHELD_TEXT, withheld_text=text)
    if m.role == "assistant":
        d["tool_calls"] = [{"id": tc.id, "function": tc.function, "arguments": tc.arguments}
                           for tc in (m.tool_calls or [])]
        d["model"] = m.model
        if own_output and m.id in blocked:
            flags.add("output_blocked")
        elif text.strip() == REFUSAL_TEXT:
            flags.add("refusal_in_history")
    if m.role == "tool":
        d.update(tool_call_id=m.tool_call_id, function=m.function, error=m.error.message if m.error else None)
    d["flags"] = sorted(flags)
    return d


@dataclass
class Call:
    """One model call of the eval log: its ModelEvent, its caller, and the ids the defenses withheld or blocked."""

    index: int
    event: ModelEvent
    agent: str | None
    defense: str | None
    redacted: set
    blocked: set

    @cached_property
    def summary(self) -> dict:
        """The call's row in the context index."""
        e, out = self.event, self.event.output
        msg = output_message(e)
        inputs = e.input or []
        output_blocked = bool(msg and msg.id in self.blocked)
        flags = {f for m in inputs for f in message_dict(m, self.redacted)["flags"]}
        return {
            "index": self.index,
            "agent": self.agent,
            "defense": self.defense,
            "model": e.model,
            "timestamp": e.timestamp.isoformat() if e.timestamp else None,
            "working_time": e.working_time,
            "n_input": len(inputs),
            "input_chars": sum(len(m.text or "") for m in inputs),
            "tools": [t.name for t in (e.tools or [])],
            "usage": usage_dict(out.usage) if out and out.usage else None,
            "output_preview": ((msg.text if msg else "") or "")[:200],
            "output_tool_calls": [tc.function for tc in (msg.tool_calls or [])] if msg else [],
            "stop_reason": out.choices[0].stop_reason if out and out.choices else None,
            "error": str(e.error)[:500] if e.error else None,
            "flags": sorted(flags | ({"output_blocked"} if output_blocked else set())),
        }

    def detail(self) -> dict:
        """The index row plus the exact provider input, the output and the tools offered."""
        e, msg = self.event, output_message(self.event)
        return {
            **self.summary,
            "input": [message_dict(m, self.redacted) for m in (e.input or [])],
            "output": message_dict(msg, self.redacted, self.blocked, own_output=True) if msg else None,
            "output_error": str(e.output.error) if e.output and e.output.error else None,
            "tool_schemas": [{"name": t.name, "description": t.description} for t in (e.tools or [])],
        }


def transcript_call_ids(transcript: dict) -> list[str]:
    """The tool call ids of an ATIF transcript in the code ladder's numbering: call k is the k-th tool call of the
    agent steps, from 0."""
    return [tc.get("tool_call_id") for step in transcript["trajectory"]["steps"] if step.get("source") == "agent"
            for tc in step.get("tool_calls") or []]


class LogViews:
    """The eval-log views of the runs a Store reads."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self._calls: dict[str, list[Call]] = {}
        self._lock = threading.Lock()

    def sample(self, name: str) -> dict:
        """The sample's facts: status, time, limit, error, usage per model, scores and the store keys the page shows."""
        log = self.store.eval_log(name)
        s = log.samples[0]
        return {
            "status": log.status,
            "model": log.eval.model,
            "eval_id": log.eval.eval_id,
            "sample_id": s.id,
            "epoch": s.epoch,
            "total_time": s.total_time,
            "working_time": s.working_time,
            "limit": {"type": s.limit.type, "limit": s.limit.limit, "reason": s.limit.reason} if s.limit else None,
            "error": str(s.error.message)[:4000] if s.error else None,
            "model_usage": {model: usage_dict(u) for model, u in (s.model_usage or {}).items()},
            "scores": {k: {"value": sc.value, "answer": sc.answer, "explanation": sc.explanation,
                           "metadata": sc.metadata} for k, sc in (s.scores or {}).items()},
            "store": {k: s.store.get(k) for k in SAMPLE_STORE_KEYS},
            "n_messages": len(s.messages),
            "n_events": len(s.events),
        }

    def calls(self, name: str) -> list[Call]:
        """Every model call in the eval log, built once per run."""
        with self._lock:
            if name in self._calls:
                return self._calls[name]
        s = self.store.eval_log(name).samples[0]
        owner = span_owners(s)
        redacted = blocked_message_ids(s, "model_input")
        blocked = blocked_message_ids(s, "model_output")
        events = [e for e in s.events if e.event == "model"]
        calls = [Call(i, e, *owner(e.span_id), redacted, blocked) for i, e in enumerate(events)]
        with self._lock:
            self._calls[name] = calls
        return calls

    def context_list(self, name: str) -> dict:
        calls = self.calls(name)
        return {"run": name, "agents": sorted({c.agent for c in calls if c.agent}),
                "defenses": sorted({c.defense for c in calls if c.defense}), "calls": [c.summary for c in calls]}

    def context_call(self, name: str, index: int) -> dict:
        calls = self.calls(name)
        if not 0 <= index < len(calls):
            raise KeyError(f"call {index}")
        return calls[index].detail()

    def timeline(self, name: str) -> dict:
        """Tool calls, model calls, defense interventions and board posts of one run, in seconds from the sample's
        start. Each board post carries the moment it was sent and who read it when; first_exposed and first_opened
        place the code ladder's exposed and opened calls of each agent on the same clock."""
        sample = self.store.eval_log(name).samples[0]
        owner = span_owners(sample)
        t0 = sample.events[0].timestamp
        eps = self.store.episodes(name)
        marks = self._signal_marks(eps)
        blocked = blocked_message_ids(sample, "model_output")
        events = []
        for e in sample.events:
            if e.event == "tool":
                events.append(_tool_item(e, owner(e.span_id)[0], t0, marks.get(e.id, set())))
            elif e.event == "model":
                events.append(_model_item(e, *owner(e.span_id), t0, blocked))
            elif e.event == "info" and isinstance(e.data, dict) and e.data.get("event") == "defense_intervention":
                events.append(_defense_item(e, t0))
        events.sort(key=lambda x: x["t"] if x["t"] is not None else 0)
        first = {rung: {} for rung in SIGNAL_RUNGS}
        for item in events:
            for rung in SIGNAL_RUNGS:
                if item.get(rung) and item["agent"] and item["t"] is not None:
                    first[rung].setdefault(item["agent"], item["t"])
        communication = self.store.run_dir(name) / "communication" / f"{sample.id}#{sample.epoch}.json"
        return {
            "run": name,
            "duration": sample.total_time,
            "agents": self.store.run_json(name).get("agents") or sorted({e["orbit_agent"] for e in eps}),
            "roles": {e["orbit_agent"]: e["role"] for e in eps if e.get("role")},
            "first_exposed": first["exposed"],
            "first_opened": first["opened"],
            "limit": sample.limit.type if sample.limit else None,
            "events": events,
            "board": _board(read_json(communication) or {}, events),
        }

    def _signal_marks(self, episodes: list[dict]) -> dict[str, set]:
        """Tool call id -> the code ladder's rungs that call first showed, from signals and transcripts."""
        marks: dict[str, set] = {}
        for ep in episodes:
            signals = ep["signals"] or {}
            calls = [signals.get(f"{rung}_call") for rung in SIGNAL_RUNGS]
            if not ep["has_transcript"] or all(k is None for k in calls):
                continue
            ids = transcript_call_ids(read_json(Path(ep["transcript_path"])))
            for rung, k in zip(SIGNAL_RUNGS, calls):
                if k is not None and k < len(ids):
                    marks.setdefault(ids[k], set()).add(rung)
        return marks


def _secs(ts, t0) -> float | None:
    return (ts - t0).total_seconds() if ts else None


def _tool_item(e, agent, t0, rungs: set) -> dict:
    args = e.arguments if isinstance(e.arguments, dict) else {}
    shown = args.get("command") or args.get("code") or args.get("message") or args.get("answer")
    err = e.error.message if e.error else None
    return {
        "t": _secs(e.timestamp, t0),
        "t_end": _secs(e.completed, t0),
        "agent": agent,
        "kind": "submit" if e.function == "submit" else "tool",
        "function": e.function,
        "id": e.id,
        "preview": str(shown or " ".join(str(v) for v in args.values()))[:160],
        "blocked": bool(err and err.startswith("Blocked by defense")),
        "error": bool(err),
        **{rung: rung in rungs for rung in SIGNAL_RUNGS},
    }


def _model_item(e, agent, defense, t0, blocked: set) -> dict:
    out = output_message(e)
    usage = usage_dict(e.output.usage) if e.output and e.output.usage else {}
    return {
        "t": _secs(e.timestamp, t0),
        "t_end": _secs(e.completed, t0),
        "agent": agent,
        "kind": "defense_call" if defense else "model",
        "defense": defense,
        "tokens_in": usage.get("input"),
        "tokens_out": usage.get("output"),
        "tool_calls": [tc.function for tc in (out.tool_calls or [])] if out else [],
        "output_blocked": bool(out and out.id in blocked),
        "refusal": bool(out and (out.text or "").strip() == REFUSAL_TEXT),
        "error": bool(e.error),
    }


def _defense_item(e, t0) -> dict:
    d = e.data
    return {
        "t": _secs(e.timestamp, t0),
        "agent": d.get("agent"),
        "kind": "defense",
        "defense": d.get("defense"),
        "boundary": d.get("boundary"),
        "allow": d.get("allow"),
        "reason": (d.get("reason") or "")[:200],
        "tool": d.get("tool"),
    }


def _board(comm: dict, events: list[dict]) -> list[dict]:
    """Board messages with the moment each was sent (its board_post call) and who read it when (their board_read
    calls), from the communication store's sent and read events."""
    tool_t = {ev["id"]: ev["t"] for ev in events if ev["kind"] in ("tool", "submit")}
    sent_t, reads = {}, {}
    for ev in comm.get("events", []):
        t = tool_t.get(ev.get("tool_call_id"))
        for m in ev.get("message_ids", []):
            if ev.get("kind") == "sent":
                sent_t[m] = t
            elif ev.get("kind") == "read":
                reads.setdefault(m, []).append({"agent": ev.get("agent_id"), "t": t})
    return [{"sequence": m.get("sequence"), "sender": m.get("sender"), "id": m.get("id"), "t": sent_t.get(m.get("id")),
             "content": m.get("content"), "read_by": reads.get(m.get("id"), [])}
            for m in sorted(comm.get("messages", []), key=lambda m: m.get("sequence", 0))]
