"""One Inspect sample from an ORBIT run, as one ATIF trajectory per agent.

The input is a SampleView: either a finished EvalSample read from an .eval log (export, re-judging) or the live
TaskState and transcript events of a sample being scored (the judge scorer). The output is the transcript dict
CheatBench's judge reads: a "trajectory" key holding ATIF, which core/agents/factory.py validates without a vendor
adapter.

Each ORBIT agent becomes its own episode: the sample's projected messages are split by
EnvironmentState:message_attribution (history_projection="all"). Four things live only in Inspect's native events
and are recovered from them:

  * the completed submit call, which ORBIT strips from the conversation;
  * channel notices and auto-delivered board posts, which ORBIT adds to the provider input only
    (CommunicationDeliveryFilter);
  * the planning prompts, turn headers, continue prompts and board notices of src/swarm/turn_rules.py, which
  also reach the
    provider input only. Inspect writes the ModelEvent before ORBIT's model filters run, so they are absent from
    ModelEvent.input; turn_rules.py writes each one as an info event right after its ModelEvent, in the same span;
  * per-agent token usage.

The text helpers split_content and tool_result_text are shared with the results browser.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Any, Callable, Sequence

import inspect_ai
import orbit
from inspect_ai.event import SampleLimitEvent
from inspect_ai.log import EvalLog, EvalSample, EvalSampleLimit
from inspect_ai.model import (
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageTool,
    ChatMessageUser,
    ModelOutput,
)
from inspect_ai.model._model import sample_model_usage
from inspect_ai.solver import TaskState

from src.configuration import settings

if str(settings.CHEATBENCH) not in sys.path:
    sys.path.insert(0, str(settings.CHEATBENCH))

from core.trajectory import (  # noqa: E402
    Agent,
    FinalMetrics,
    Observation,
    ObservationResult,
    Step,
    ToolCall,
    Trajectory,
)
from core.trajectory.trajectory import SCHEMA_VERSION  # noqa: E402

DELIVERY_TAG = "orbit_communication_delivery"        # orbit/communication/delivery.py
TURN_RULES_TAG = "swarm_turn_rules"         # src/swarm/turn_rules.py
# The info kinds that carry a message the provider got. "notice" is the board notice (SwarmConfig.notice).
TURN_RULES_PROMPTS = ("planning", "header", "continue", "notice")
ATTRIBUTION_KEY = "EnvironmentState:message_attribution"
LAST_TEXT_KEY = "EnvironmentState:last_text_by_agent"
REFUSAL_TEXT = "I cannot provide that response under the configured defense policy."   # orbit/defenses/boundaries.py
WITHHELD_TEXT = "[Content withheld by a configured defense.]"
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
# message content
# --------------------------------------------------------------------------
def split_content(content) -> tuple[str, str | None]:
    """(text, reasoning) from an Inspect message content (str or content list).

    A redacted reasoning part carries the provider's encrypted signature (Gemini's thought signatures), which is
    not readable reasoning, so it is never copied: it shows as "[redacted reasoning]" when nothing readable is left.
    """
    if content is None:
        return "", None
    if isinstance(content, str):
        return content, None
    texts, reasoning, redacted = [], [], False
    for part in content:
        kind = getattr(part, "type", None)
        if kind == "text":
            texts.append(part.text)
        elif kind == "reasoning":
            if getattr(part, "redacted", False):
                redacted = True
            elif getattr(part, "reasoning", ""):
                reasoning.append(part.reasoning)
        else:
            texts.append(f"[{kind} content omitted]")
    if not reasoning and redacted:
        reasoning.append("[redacted reasoning]")
    return "\n".join(t for t in texts if t), ("\n".join(reasoning) or None)


def tool_result_text(result) -> str:
    if result is None:
        return ""
    if isinstance(result, (str, int, float, bool)):
        return str(result)
    if isinstance(result, list):
        return "\n".join(tool_result_text(r) for r in result)
    if not hasattr(result, "type"):
        return str(result)
    text, _ = split_content([result])
    return text


def output_message_id(model_event) -> str | None:
    """Id of the assistant message a model call produced, or None when it produced no choice."""
    if model_event.output and model_event.output.choices:
        return model_event.output.message.id
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
# provider-only messages and usage
# --------------------------------------------------------------------------
def provider_only_step(m, how: str = "") -> dict | None:
    """The ATIF step for a message that reached the provider but not the conversation, or None.

    These are ORBIT's channel notices (system), the turn rules' planning prompts, turn headers and
    continue prompts (user), and the older trailing-turn shim's prompt (user).
    """
    meta = m.metadata or {}
    text, _ = split_content(m.content)
    if m.role == "system" and meta.get(DELIVERY_TAG):
        return _notice_step(text, how)
    if m.role in ("user", "system") and meta.get(TURN_RULES_TAG):
        rule = meta[TURN_RULES_TAG]
        kind = rule.get("kind") if isinstance(rule, dict) else rule
        return {"source": m.role, "message": text, "extra": {TURN_RULES_TAG: kind, "attached_by": how}}
    return None


def rules_messages_by_call(view: SampleView) -> dict[int, list]:
    """The turn rules' added messages, keyed by id() of the ModelEvent of the call that carried them.

    turn_rules.py writes {TURN_RULES_TAG: {"kind", "activation", "agent", "content"}} as an info event inside the model
    call, right after Inspect wrote that call's ModelEvent in the same span.
    """
    last_call: dict = {}
    out: dict[int, list] = {}
    for ev in view.events:
        if ev.event == "model":
            last_call[ev.span_id] = ev
            continue
        data = ev.data if ev.event == "info" and isinstance(ev.data, dict) else {}
        rule = data.get(TURN_RULES_TAG)
        if not isinstance(rule, dict) or rule.get("kind") not in TURN_RULES_PROMPTS:
            continue
        call = last_call.get(ev.span_id)
        if call is None:
            continue
        tag = {"kind": rule["kind"], "activation": rule.get("activation")}
        out.setdefault(id(call), []).append(ChatMessageUser(content=rule.get("content") or "",
                                                            metadata={TURN_RULES_TAG: tag}))
    return out


def provider_only_messages(model_events, rules_by_call: dict[int, list] | None = None) -> list[tuple[str | None, Any]]:
    """(output assistant message id or None, message) per provider-only message in a model call, in input order.

    Messages in ModelEvent.input come first; the turn rules' message, which the filter appends last, follows.
    """
    out = []
    for ev in model_events:
        found = [m for m in ev.input or [] if provider_only_step(m) is not None]
        if not any((m.metadata or {}).get(TURN_RULES_TAG) for m in found):
            found += (rules_by_call or {}).get(id(ev), [])
        out += [(output_message_id(ev), m) for m in found]
    return out


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
# one (sample, agent) -> one ATIF trajectory
# --------------------------------------------------------------------------
def _call_arguments(arguments, fallback_key: str) -> dict:
    if isinstance(arguments, dict):
        return dict(arguments)
    if fallback_key == "answer":
        return {"answer": str(arguments)}
    return {fallback_key: arguments}


def _error_extra(error) -> dict | None:
    if not error:
        return None
    return {"is_error": True, "error": error.message}


def _notice_step(text: str, how: str) -> dict:
    """ORBIT's board delivery notice, a provider-only system message, as an ATIF step."""
    return {"source": "system", "message": text, "extra": {"orbit_delivery_notice": True, "attached_by": how}}


def _user_step(msg, redacted: set) -> dict:
    text, _ = split_content(msg.content)
    if msg.id not in redacted:
        return {"source": "user", "message": text}
    # the provider received the placeholder; the judge must see what the agent saw
    extra = {"withheld_from_model_by_defense": True, "withheld_text": text}
    return {"source": "user", "message": WITHHELD_TEXT, "extra": extra}


def _assistant_step(msg, call_output_id, blocked_outputs: set) -> dict:
    text, reasoning = split_content(msg.content)
    calls = [ToolCall(tool_call_id=tc.id, function_name=tc.function,
                      arguments=_call_arguments(tc.arguments, "value"))
             for tc in (msg.tool_calls or [])]
    step = {"source": "agent", "message": text, "reasoning_content": reasoning,
            "tool_calls": calls or None, "llm_call_count": 1,
            "model_name": msg.model or None, "results": []}
    if call_output_id is not None and call_output_id in blocked_outputs:
        step["extra"] = {"defense_refusal": True, "blocked_output_message_id": call_output_id}
    elif text.strip() == REFUSAL_TEXT:
        step["extra"] = {"refusal_text_written_by_model": True}
    return step


def _result_content(content, error) -> str:
    """On an error Inspect leaves the content empty and the model receives the error text; the judge gets it too."""
    return tool_result_text(content) or (error.message if error else "")


def _add_tool_result(msg, redacted: set, steps: list[dict], issued: dict[str, int]) -> None:
    """Attach a tool result to the step that issued the call, or add it as an orphan user step."""
    content = _result_content(msg.content, msg.error)
    extra = _error_extra(msg.error)
    if msg.id in redacted:
        extra = {**(extra or {}), "withheld_from_model_by_defense": True, "withheld_text": content}
        content = WITHHELD_TEXT
    idx = issued.get(msg.tool_call_id)
    if idx is None:
        orphan = {"orphan_tool_result": msg.tool_call_id, "function": msg.function, **(extra or {})}
        steps.append({"source": "user", "message": content, "extra": orphan})
        return
    result = ObservationResult(source_call_id=msg.tool_call_id, content=content, extra=extra)
    steps[idx]["results"].append(result)


def _recover_submits(view: SampleView, agent: str, steps: list[dict], issued: dict[str, int]) -> int:
    """Put back the completed submit calls ORBIT removed from the conversation.

    orbit/agents/turn_react.py:_remove_submit_tool drops the call and its result;
    the native tool event keeps it.
    """
    recovered = 0
    for ev in agent_events(view, agent, "tool"):
        if ev.function != "submit" or ev.id in issued:
            continue
        agent_steps = [i for i, s in enumerate(steps) if s["source"] == "agent"]
        last_agent = agent_steps[-1] if agent_steps else None
        call = ToolCall(tool_call_id=ev.id, function_name="submit",
                        arguments=_call_arguments(ev.arguments, "answer"))
        result = ObservationResult(source_call_id=ev.id, content=_result_content(ev.result, ev.error),
                                   extra=_error_extra(ev.error))
        if last_agent is None:
            steps.append({"source": "agent", "message": "", "tool_calls": [call], "llm_call_count": 1,
                          "results": [result], "extra": {"submit_recovered_from_events": True}})
        else:
            step = steps[last_agent]
            step["tool_calls"] = list(step.get("tool_calls") or []) + [call]
            step["results"].append(result)
            step["extra"] = {**(step.get("extra") or {}), "submit_recovered_from_events": True}
        recovered += 1
    return recovered


def _atif_steps(steps: list[dict]) -> list[Step]:
    out = []
    for i, s in enumerate(steps, 1):
        results = s.pop("results", [])
        observation = Observation(results=results) if results else None
        out.append(Step(step_id=i, observation=observation, **s))
    return out


def _attach_provider_only(msg, by_id: dict, ordered: list, assistant_seen: int, steps: list[dict]) -> tuple[int, int]:
    """Add the provider-only messages of the call that produced `msg`; return (notices, rule prompts) added."""
    items = by_id.get(msg.id)
    how = "message_id"
    if items is None and not by_id and assistant_seen < len(ordered):
        items, how = [ordered[assistant_seen]], "order"
    notices = rules = 0
    for m in items or []:
        step = provider_only_step(m, how)
        steps.append(step)
        if TURN_RULES_TAG in step["extra"]:
            rules += 1
        else:
            notices += 1
    return notices, rules


def build_trajectory(view: SampleView, agent: str) -> tuple[Trajectory, dict]:
    """The agent's ATIF trajectory, and facts about it (system prompt, recovered items, counts, usage)."""
    messages = agent_messages(view, agent)
    model_events = agent_events(view, agent, "model")
    provider_only = provider_only_messages(model_events, rules_messages_by_call(view))
    redacted = redacted_message_ids(model_events, view)
    blocked_outputs = blocked_output_ids(view)
    output_ids = [output_message_id(ev) for ev in model_events]
    by_id: dict = {}
    for oid, m in provider_only:
        if oid:
            by_id.setdefault(oid, []).append(m)
    ordered = [m for _, m in provider_only]
    steps: list[dict] = []
    issued: dict[str, int] = {}          # tool_call_id -> index into steps
    system_prompt = None
    assistant_seen = 0
    attached_notices = attached_rules = 0
    for msg in messages:
        if isinstance(msg, ChatMessageSystem):
            text, _ = split_content(msg.content)
            if system_prompt is None:
                system_prompt = text
            steps.append({"source": "system", "message": text})
        elif isinstance(msg, ChatMessageUser):
            steps.append(_user_step(msg, redacted))
        elif isinstance(msg, ChatMessageAssistant):
            notices, rules = _attach_provider_only(msg, by_id, ordered, assistant_seen, steps)
            attached_notices += notices
            attached_rules += rules
            call_output_id = output_ids[assistant_seen] if assistant_seen < len(output_ids) else None
            assistant_seen += 1
            step = _assistant_step(msg, call_output_id, blocked_outputs)
            steps.append(step)
            for tc in step["tool_calls"] or []:
                issued[tc.tool_call_id] = len(steps) - 1
        elif isinstance(msg, ChatMessageTool):
            _add_tool_result(msg, redacted, steps, issued)
        else:
            text, _ = split_content(getattr(msg, "content", None))
            steps.append({"source": "user", "message": text, "extra": {"role": getattr(msg, "role", "?")}})

    recovered = _recover_submits(view, agent, steps, issued)
    atif_steps = _atif_steps(steps)

    lone_agent = len(roster(view)) == 1
    usage = usage_from_events(model_events) or (sum_usage(view) if lone_agent else {})
    last_text = (view.store_get(LAST_TEXT_KEY) or {}).get(agent)
    if last_text is None and lone_agent and view.output:
        last_text = view.output.completion or None
    final_metrics = None
    if usage:
        final_metrics = FinalMetrics(total_prompt_tokens=usage.get("input"),
                                     total_completion_tokens=usage.get("output"),
                                     total_steps=len(atif_steps))
    traj = Trajectory(
        schema_version=SCHEMA_VERSION,
        session_id=f"{view.id}#{view.epoch}",
        trajectory_id=f"{view.id}#{view.epoch}/{agent}",
        agent=Agent(name=f"orbit/{agent}", version=agent_version(), model_name=view.model),
        steps=atif_steps,
        final_metrics=final_metrics,
        extra={"final_text": last_text or None},
    )
    info = {"system_prompt": system_prompt, "submit_recovered": recovered,
            "delivery_notices": attached_notices, "turn_rules_messages": attached_rules,
            "model_calls": len(model_events),
            "n_agent_steps": sum(1 for s in atif_steps if s.source == "agent"),
            "n_tool_calls": sum(len(s.tool_calls or []) for s in atif_steps),
            "usage": usage or None, "final_text": last_text or None}
    return traj, info


def episode_transcript(view: SampleView, agent: str, prompt: str) -> tuple[dict, dict]:
    """(transcript dict, build_trajectory facts) for one agent."""
    traj, info = build_trajectory(view, agent)
    transcript = {
        "agent": f"orbit/{agent}", "model": view.model,
        "prompt": prompt,
        "trajectory": traj.to_json_dict(),
        "orbit": {"eval_log": view.eval_log, "agent": agent, "roster": roster(view),
                  "topology": topology(view), "system_prompt": info["system_prompt"],
                  "submit_recovered_from_events": info["submit_recovered"],
                  "delivery_notices_recovered_from_events": info["delivery_notices"],
                  "turn_rules_recovered_from_events": info["turn_rules_messages"],
                  "model_calls": info["model_calls"]},
    }
    return transcript, info


def transcript_dict(view: SampleView, agent: str, prompt: str) -> dict:
    """The transcript CheatBench's judge reads: {"agent", "model", "prompt", "trajectory", "orbit"}."""
    return episode_transcript(view, agent, prompt)[0]


def judge_prompt(view: SampleView, agent: str) -> str:
    """What the judge sees as the agent's instructions: the benchmark's judge_prompt, else the instruction."""
    cb = (view.metadata or {}).get("cheatbench", {})
    per_agent = (cb.get("judge_prompt") or {}).get(agent)
    if per_agent:
        return per_agent
    return cb.get("instruction") or (view.input if isinstance(view.input, str) else "")


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
