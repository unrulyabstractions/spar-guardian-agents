"""One Inspect sample from an ORBIT run, as one ATIF trajectory per agent.

The input is a SampleView (src/export/eval_sample_view.py): either a finished EvalSample read from an .eval log
(export, re-judging) or the live TaskState and transcript events of a sample being scored (the judge scorer). The
output is the transcript dict CheatBench's judge reads: a "trajectory" key holding ATIF, which
core/agents/factory.py validates without a vendor adapter.

    transcript, facts = episode_transcript(view, "a01")

Each ORBIT agent becomes its own episode: the sample's projected messages are split by
EnvironmentState:message_attribution (history_projection="all"). Three things live only in Inspect's native events
and are recovered from them:

  * the completed submit call, which ORBIT strips from the conversation;
  * the continue prompts and board notices of the turn rules' model filter (src/swarm/turn_rules/), which reach the
    provider only. Inspect writes the ModelEvent before ORBIT's model filters run, so they are absent from
    ModelEvent.input; turn_rules_filter.py writes each one as an info event right after its ModelEvent, in the same
    span;
  * per-agent token usage.

The results browser (src/ui) reads eval logs through the same helpers: split_content, tool_result_text,
output_message and the defense texts here, span_owners, blocked_message_ids and usage_dict in eval_sample_view.py.
"""
from __future__ import annotations

from inspect_ai.model import ChatMessageAssistant, ChatMessageSystem, ChatMessageTool, ChatMessageUser

from src.export.eval_sample_view import (
    SampleView,
    agent_events,
    agent_messages,
    agent_version,
    blocked_message_ids,
    redacted_message_ids,
    roster,
    sum_usage,
    topology,
    usage_from_events,
)
from src.swarm.turn_rules.turn_state import TURN_RULES_TAG
from src.utils.cheatbench_core import trajectory as atif

# The info kinds that carry a message the provider got. "notice" is the board notice (SwarmConfig.notice).
TURN_RULES_PROMPTS = ("continue", "notice")
LAST_TEXT_KEY = "EnvironmentState:last_text_by_agent"
REFUSAL_TEXT = "I cannot provide that response under the configured defense policy."   # orbit/defenses/boundaries.py
WITHHELD_TEXT = "[Content withheld by a configured defense.]"


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


def output_message(model_event):
    """The assistant message a model call produced, or None when it produced no choice."""
    if model_event.output and model_event.output.choices:
        return model_event.output.message
    return None


def output_message_id(model_event) -> str | None:
    message = output_message(model_event)
    return message.id if message else None


# --------------------------------------------------------------------------
# the turn rules' prompts, which reach the provider only
# --------------------------------------------------------------------------
def rules_prompts_by_output(view: SampleView, model_events) -> dict[str, list[dict]]:
    """The turn rules' continue prompts and board notices ({TURN_RULES_TAG: {"kind", "activation", "agent",
    "content"}}), keyed by the id of the assistant message the call that carried them produced.

    turn_rules_filter.py writes each one as an info event inside the model call, right after Inspect wrote that call's
    ModelEvent in the same span. A call that produced no message keeps its prompts out of the transcript.
    """
    last_call: dict = {}
    by_call: dict[int, list[dict]] = {}
    for ev in view.events:
        if ev.event == "model":
            last_call[ev.span_id] = ev
            continue
        rule = (ev.data or {}).get(TURN_RULES_TAG) if ev.event == "info" and isinstance(ev.data, dict) else None
        call = last_call.get(ev.span_id)
        if isinstance(rule, dict) and rule.get("kind") in TURN_RULES_PROMPTS and call is not None:
            by_call.setdefault(id(call), []).append(rule)
    out: dict[str, list[dict]] = {}
    for ev in model_events:
        if output_message_id(ev) and id(ev) in by_call:
            out.setdefault(output_message_id(ev), []).extend(by_call[id(ev)])
    return out


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


def _user_step(msg, redacted: set) -> dict:
    text, _ = split_content(msg.content)
    if msg.id not in redacted:
        return {"source": "user", "message": text}
    # the provider received the placeholder; the judge must see what the agent saw
    extra = {"withheld_from_model_by_defense": True, "withheld_text": text}
    return {"source": "user", "message": WITHHELD_TEXT, "extra": extra}


def _assistant_step(msg, call_output_id, blocked_outputs: set) -> dict:
    text, reasoning = split_content(msg.content)
    calls = [atif.ToolCall(tool_call_id=tc.id, function_name=tc.function,
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
    result = atif.ObservationResult(source_call_id=msg.tool_call_id, content=content, extra=extra)
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
        call = atif.ToolCall(tool_call_id=ev.id, function_name="submit",
                             arguments=_call_arguments(ev.arguments, "answer"))
        result = atif.ObservationResult(source_call_id=ev.id, content=_result_content(ev.result, ev.error),
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


def _atif_steps(steps: list[dict]) -> list[atif.Step]:
    out = []
    for i, s in enumerate(steps, 1):
        results = s.pop("results", [])
        observation = atif.Observation(results=results) if results else None
        out.append(atif.Step(step_id=i, observation=observation, **s))
    return out


def build_trajectory(view: SampleView, agent: str) -> tuple[atif.Trajectory, dict]:
    """The agent's ATIF trajectory, and facts about it (system prompt, recovered items, counts, usage)."""
    messages = agent_messages(view, agent)
    model_events = agent_events(view, agent, "model")
    rules_prompts = rules_prompts_by_output(view, model_events)
    redacted = redacted_message_ids(model_events, view)
    blocked_outputs = blocked_message_ids(view, "model_output")
    output_ids = [output_message_id(ev) for ev in model_events]
    steps: list[dict] = []
    issued: dict[str, int] = {}          # tool_call_id -> index into steps
    system_prompt = None
    assistant_seen = 0
    attached_rules = 0
    for msg in messages:
        if isinstance(msg, ChatMessageSystem):
            text, _ = split_content(msg.content)
            if system_prompt is None:
                system_prompt = text
            steps.append({"source": "system", "message": text})
        elif isinstance(msg, ChatMessageUser):
            steps.append(_user_step(msg, redacted))
        elif isinstance(msg, ChatMessageAssistant):
            for rule in rules_prompts.get(msg.id, []):
                steps.append({"source": "user", "message": rule.get("content") or "",
                              "extra": {TURN_RULES_TAG: rule["kind"]}})
                attached_rules += 1
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
        final_metrics = atif.FinalMetrics(total_prompt_tokens=usage.get("input"),
                                          total_completion_tokens=usage.get("output"),
                                          total_steps=len(atif_steps))
    traj = atif.Trajectory(
        schema_version=atif.SCHEMA_VERSION,
        session_id=f"{view.id}#{view.epoch}",
        trajectory_id=f"{view.id}#{view.epoch}/{agent}",
        agent=atif.Agent(name=f"orbit/{agent}", version=agent_version(), model_name=view.model),
        steps=atif_steps,
        final_metrics=final_metrics,
        extra={"final_text": last_text or None},
    )
    info = {"system_prompt": system_prompt, "submit_recovered": recovered,
            "turn_rules_messages": attached_rules,
            "model_calls": len(model_events),
            "n_agent_steps": sum(1 for s in atif_steps if s.source == "agent"),
            "n_tool_calls": sum(len(s.tool_calls or []) for s in atif_steps),
            "usage": usage or None, "final_text": last_text or None}
    return traj, info


def episode_transcript(view: SampleView, agent: str) -> tuple[dict, dict]:
    """(transcript, build_trajectory's facts) for one agent. The transcript is what CheatBench's judge reads:
    {"agent", "model", "prompt", "trajectory", "orbit"}."""
    traj, info = build_trajectory(view, agent)
    transcript = {
        "agent": f"orbit/{agent}", "model": view.model,
        "prompt": judge_prompt(view, agent),
        "trajectory": traj.to_json_dict(),
        "orbit": {"eval_log": view.eval_log, "agent": agent, "roster": roster(view),
                  "topology": topology(view), "system_prompt": info["system_prompt"],
                  "submit_recovered_from_events": info["submit_recovered"],
                  "turn_rules_recovered_from_events": info["turn_rules_messages"],
                  "model_calls": info["model_calls"]},
    }
    return transcript, info


def judge_prompt(view: SampleView, agent: str) -> str:
    """What the judge sees as the agent's instructions: the benchmark's judge_prompt, else the instruction."""
    cb = (view.metadata or {}).get("cheatbench", {})
    per_agent = (cb.get("judge_prompt") or {}).get(agent)
    if per_agent:
        return per_agent
    return cb.get("instruction") or (view.input if isinstance(view.input, str) else "")
