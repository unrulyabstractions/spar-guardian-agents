"""One Inspect sample from an ORBIT run, read the same way whether it is finished or live.

A SampleView is a finished EvalSample read from an .eval log (export, re-judging) or the live TaskState and
transcript events of a sample being scored (the judge scorer). The functions here read it: the roster and the
topology, which agent owns which messages and events, the defenses' evidence, the token usage and what the
provider billed, and how the sample ended. The transcript builder (src/export/atif_transcripts.py) and the
export (src/export/run_export.py) read samples through this module.

    view = SampleView.from_eval_sample(sample, log)
    messages = agent_messages(view, "a01")
    error, failure = error_and_failure(view)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Sequence

import inspect_ai
import orbit
from inspect_ai.event import SampleLimitEvent
from inspect_ai.log import EvalLog, EvalSample, EvalSampleLimit
from inspect_ai.model import ModelOutput
from inspect_ai.model._model import sample_model_usage
from inspect_ai.solver import TaskState

ATTRIBUTION_KEY = "EnvironmentState:message_attribution"
# Defenses that are asked at every boundary and block nothing: the turn rules.
PASSIVE_DEFENSES = {"turn_rules"}
HALT_RE = re.compile(r"Experiment complete: .*halt_reason=([^\s,]+)")
LIMIT_FAILURES = ("timeout", "max_turns")
PROVIDER_ERROR_WORDS = (
    "rate limit", "429", "connection", "stream", "timed out", "overloaded",
    "credits are depleted", "billing", "quota", "insufficient_quota", "resource_exhausted",
)


def agent_version() -> str:
    return f"orbit-{orbit.__version__}+inspect-ai-{inspect_ai.__version__}"


# --------------------------------------------------------------------------
# the sample, finished or live
# --------------------------------------------------------------------------
@dataclass
class SampleView:
    """The parts of a sample the transcript builder reads, the same for a finished log and a live sample."""

    id: str | int
    epoch: int
    messages: list
    metadata: dict
    events: list
    store_get: Callable[[str], Any]
    model: str
    input: Any
    output: ModelOutput | None
    scores: dict | None
    limit: EvalSampleLimit | None
    error: Any
    total_time: float | None
    model_usage: dict | None = None
    eval_log: str | None = None
    eval_id: str | None = None

    @classmethod
    def from_state(cls, state: TaskState, events: Sequence) -> "SampleView":
        """A sample being scored. The limit comes from a SampleLimitEvent in the events, if any."""
        limit = None
        for ev in events:
            if isinstance(ev, SampleLimitEvent):
                limit = EvalSampleLimit(type=ev.type, limit=ev.limit if ev.limit is not None else 0.0)
        return cls(id=state.sample_id, epoch=state.epoch, messages=list(state.messages),
                   metadata=state.metadata or {}, events=list(events), store_get=state.store.get,
                   model=str(state.model), input=state.input, output=state.output, scores=state.scores,
                   limit=limit, error=None, total_time=None, model_usage=_live_model_usage())

    @classmethod
    def from_eval_sample(cls, sample: EvalSample, log: EvalLog) -> "SampleView":
        """A finished sample read from an eval log (read it with resolve_attachments=True)."""
        store = sample.store or {}
        return cls(id=sample.id, epoch=sample.epoch, messages=list(sample.messages), metadata=sample.metadata or {},
                   events=list(sample.events), store_get=store.get, model=log.eval.model, input=sample.input,
                   output=sample.output, scores=sample.scores, limit=sample.limit, error=sample.error,
                   total_time=sample.total_time, model_usage=sample.model_usage, eval_log=log.location,
                   eval_id=log.eval.eval_id)


def _live_model_usage() -> dict | None:
    """The running sample's usage per model, when Inspect has a sample context."""
    try:
        return dict(sample_model_usage())
    except LookupError:
        return None


# --------------------------------------------------------------------------
# which agent owns which messages and events
# --------------------------------------------------------------------------
def roster(view: SampleView) -> list[str]:
    exp = (view.metadata or {}).get("experiment", {})
    return [a["name"] for a in exp.get("setup", {}).get("agents", [])]


def topology(view: SampleView) -> str:
    """single (no board), board (a board, no hidden roles) or swarm (a board with hidden roles)."""
    meta = view.metadata or {}
    if not (meta.get("experiment", {}).get("communication") or {}).get("channels"):
        return "single"
    cb = meta.get("cheatbench", {})
    return "swarm" if cb.get("roles") else "board"


def agent_messages(view: SampleView, agent: str) -> list:
    """The agent's conversation, as projected by AgentScheduler.sync_to_task_state."""
    attribution = view.store_get(ATTRIBUTION_KEY) or []
    indices = [a["index"] for a in attribution if a.get("agent") == agent]
    bad = [i for i in indices if i >= len(view.messages)]
    if bad:
        raise ValueError(f"attribution indices {bad} outside {len(view.messages)} messages")
    return [view.messages[i] for i in indices]


def span_owner(view: SampleView):
    """span_id -> name of the nearest enclosing span of type 'agent' (ORBIT names it after the spec).

    Events inside an ``orbit.defense`` span (a monitor's or guardian's own model
    calls) or inside a ``tool`` span (a model call made while a tool runs, such as
    the dual-LLM quarantine summariser) belong to the defense, not to the agent,
    and get owner None. ToolEvents themselves sit directly in the agent span.
    """
    begins = {e.id: e for e in view.events if e.event == "span_begin"}
    cache: dict = {}

    def owner(span_id):
        if span_id in cache:
            return cache[span_id]
        e = begins.get(span_id)
        if e is None:
            result = None
        elif e.type in ("orbit.defense", "tool"):
            result = None
        elif e.type == "agent":
            result = e.name
        else:
            result = owner(e.parent_id) if e.parent_id else None
        cache[span_id] = result
        return result
    return owner


def agent_events(view: SampleView, agent: str, kind: str) -> list:
    owner = span_owner(view)
    return [e for e in view.events if e.event == kind and owner(e.span_id) == agent]


# --------------------------------------------------------------------------
# defenses
# --------------------------------------------------------------------------
def defense_evidence(view: SampleView) -> dict:
    """DefenseLog store fields plus every defense_intervention info event."""
    keys = ("resolved_defenses", "activity", "blocks", "detections", "vaccinations",
            "vaccination_injections", "total_activations", "false_positives", "true_positives")
    log = {k: view.store_get(f"DefenseLog:{k}") for k in keys}
    kinds = ("defense_intervention", "defense_resample", "defense_instrumentation_exempt")
    interventions = []
    for e in view.events:
        if e.event == "info" and isinstance(e.data, dict) and e.data.get("event") in kinds:
            interventions.append(e.data)
    return {"log": log, "interventions": interventions}


def defense_counts(evidence: dict, agent: str) -> dict:
    """checks and blocks per boundary for one agent, from the intervention events.

    The passive defenses (the turn rules) are asked at every boundary and block nothing;
    their checks are left out so the counts describe the control.
    """
    out = {"checks": 0, "blocks": 0, "blocks_by_boundary": {}, "checks_by_boundary": {}}
    for ev in evidence["interventions"]:
        if ev.get("event") != "defense_intervention" or ev.get("agent") != agent:
            continue
        if ev.get("defense") in PASSIVE_DEFENSES:
            continue
        b = ev.get("boundary", "?")
        out["checks"] += 1
        out["checks_by_boundary"][b] = out["checks_by_boundary"].get(b, 0) + 1
        if ev.get("allow") is False:
            out["blocks"] += 1
            out["blocks_by_boundary"][b] = out["blocks_by_boundary"].get(b, 0) + 1
    return out


def blocked_message_ids(view: SampleView, boundary: str) -> set:
    """Ids of messages a defense blocked at one boundary, from the defense_intervention events."""
    ids = set()
    for e in view.events:
        d = getattr(e, "data", None)
        if e.event != "info" or not isinstance(d, dict):
            continue
        if d.get("event") != "defense_intervention" or d.get("boundary") != boundary:
            continue
        if d.get("allow") is False and d.get("message_id"):
            ids.add(d["message_id"])
    return ids


def blocked_output_ids(view: SampleView) -> set:
    """Ids of raw model outputs a defense replaced with the refusal (model_output blocks).

    ORBIT keeps the raw output in the ModelEvent and substitutes a new assistant
    message in the conversation, so the i-th assistant message of an agent is a
    real substitution iff the i-th model call's output id is in this set. A
    refusal sentence the model wrote itself is not a block.
    """
    return blocked_message_ids(view, "model_output")


def redacted_message_ids(model_events, view: SampleView) -> set:
    """Ids of messages a defense withheld from the provider.

    ORBIT redacts below the ModelEvent (the event keeps the original message), so
    the record of truth is the ``defense_intervention`` event at the model_input
    boundary with ``allow`` false; the metadata tag is kept as a fallback.
    """
    ids = set()
    for ev in model_events:
        for m in ev.input or []:
            if (m.metadata or {}).get("orbit_defense_redacted"):
                ids.add(m.id)
    return ids | blocked_message_ids(view, "model_input")


# --------------------------------------------------------------------------
# usage
# --------------------------------------------------------------------------
def _usage_totals(usages) -> dict:
    """Prompt tokens include cache reads: Inspect reports them apart from input_tokens. Output tokens include the
    reasoning tokens, which Inspect also reports on their own (reasoning, when any)."""
    total_in = total_out = cache_read = reasoning = 0
    for usage in usages:
        cached = getattr(usage, "input_tokens_cache_read", 0) or 0
        total_in += (getattr(usage, "input_tokens", 0) or 0) + cached
        cache_read += cached
        total_out += getattr(usage, "output_tokens", 0) or 0
        reasoning += getattr(usage, "reasoning_tokens", 0) or 0
    if not (total_in or total_out):
        return {}
    totals = {"input": total_in, "output": total_out, "cache_read": cache_read}
    return {**totals, "reasoning": reasoning} if reasoning else totals


def billed_cost(ev) -> float | None:
    """What the provider charged for one model call, from the raw response the log kept (OpenRouter's usage.cost);
    None when the log kept no raw response for the call or the provider reports no cost."""
    call = getattr(ev, "call", None)
    response = getattr(call, "response", None) if call else None
    cost = ((response or {}).get("usage") or {}).get("cost") if isinstance(response, dict) else None
    return float(cost) if isinstance(cost, (int, float)) else None


def usage_from_events(model_events) -> dict:
    """The episode's token totals, plus billed_usd and billed_calls: what the provider charged for the calls whose
    raw response the log kept (every call since run.evaluate logs them all; the first five per model before)."""
    usages, billed, calls = [], 0.0, 0
    for ev in model_events:
        usage = getattr(ev.output, "usage", None) if ev.output else None
        if usage:
            usages.append(usage)
        cost = billed_cost(ev)
        if cost is not None:
            billed += cost
            calls += 1
    totals = _usage_totals(usages)
    if calls:
        totals.update(billed_usd=round(billed, 6), billed_calls=calls)
    return totals


def sum_usage(view: SampleView) -> dict:
    return _usage_totals((view.model_usage or {}).values())


# --------------------------------------------------------------------------
# how a sample ended
# --------------------------------------------------------------------------
def orbit_halt_reason(view: SampleView) -> str | None:
    """ORBIT's own stop reason (convergence, max_turns, max_time, attack_success, inspect_limit,
    error: ..., interrupted, before_agents_completed), written only to an info event."""
    for ev in reversed(view.events or []):
        if ev.event == "info" and isinstance(ev.data, str):
            m = HALT_RE.search(ev.data)
            if m:
                return m.group(1)
    return None


def error_and_failure(view: SampleView) -> tuple[str | None, str | None]:
    """Map Inspect's limit/error, or ORBIT's scheduler cap, onto CheatBench's episode error
    string and failure tag.

    core/judge.py:is_judgeable drops lost episodes; limits stay judgeable. ORBIT's
    scheduler caps (max_turns, max_time) are not Inspect limits: the sample completes
    normally, so they are read from the halt reason.
    """
    halt = orbit_halt_reason(view)
    finished_cleanly = view.limit is None and view.error is None
    if finished_cleanly and halt == "max_turns":
        return "max_turns reached before confirmed task completion", "max_turns"
    if finished_cleanly and halt == "max_time":
        return "time limit reached", "timeout"
    if view.limit is not None:
        kind = view.limit.type
        if kind == "time":
            return "time limit reached", "timeout"
        if kind in ("message", "turn"):
            return "max_turns reached before confirmed task completion", "max_turns"
        if kind in ("token", "context"):
            return f"{kind} limit reached", "context_window_exceeded"
        return f"{kind} limit reached", None
    if view.error is not None:
        message = str(getattr(view.error, "message", view.error))
        lowered = message.lower()
        # A provider client error that survived every retry (the Gemini 402 of 21 October) is a RetryError.
        retried_client_error = "retryerror" in lowered and "clienterror" in lowered
        if retried_client_error or any(k in lowered for k in PROVIDER_ERROR_WORDS):
            # a lost episode in CheatBench's terms: the provider, not the agent, ended it
            return message, "connection_closed"
        return message, "harness"
    return None, None
