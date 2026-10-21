"""Seam C: an Inspect .eval log from an ORBIT run -> the run directory CheatBench's judge reads.

    python -m integration.convert.eval_to_atif logs/<run>.eval runs/<name> [--task-root PATH]

Writes
    runs/<name>/run.json
    runs/<name>/episodes.jsonl                      one record per (sample, agent)
    runs/<name>/trajectories/<episode>/transcript.json
    runs/<name>/trajectories/<episode>/prompt.txt
    runs/<name>/communication/<sample>.json         channel messages and events, if any

A transcript with a "trajectory" key is validated as ATIF by CheatBench's judge
without any vendor adapter (core/agents/factory.py). Each ORBIT agent becomes
its own episode: the sample's projected messages are split by
EnvironmentState:message_attribution (history_projection="all"). Three things
live only in Inspect's native events and are recovered from them:

  * the completed submit call, which ORBIT strips from the conversation,
  * channel notices / auto-delivered board messages, which ORBIT adds to the
    provider input only (CommunicationDeliveryFilter),
  * per-agent token usage.

Episode ids: "<sample>#<epoch>" for a single agent, "<sample>#<epoch>@<agent>" otherwise.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CB = ROOT / "submodules" / "cheatbench"
if str(CB) not in sys.path:
    sys.path.insert(0, str(CB))

import inspect_ai  # noqa: E402
import orbit  # noqa: E402
from inspect_ai.log import read_eval_log  # noqa: E402
from inspect_ai.model import (ChatMessageAssistant, ChatMessageSystem,  # noqa: E402
                              ChatMessageTool, ChatMessageUser)

from core.trajectory import (Agent, FinalMetrics, Observation, ObservationResult,  # noqa: E402
                             Step, ToolCall, Trajectory)
from core.trajectory.trajectory import SCHEMA_VERSION  # noqa: E402

DELIVERY_TAG = "orbit_communication_delivery"      # orbit/communication/delivery.py
ATTRIBUTION_KEY = "EnvironmentState:message_attribution"
LAST_TEXT_KEY = "EnvironmentState:last_text_by_agent"

DEVIATIONS = [
    "agent_is_orbit_turn_react_not_vendor_harness",
    "orbit_assistant_and_submit_prompt_appended_to_system_prompt",
    "continue_prompts_are_orbit_synthetic_user_messages",
    "mirror_served_from_docker_sidecar_and_ca_baked_into_image",
]

# CheatBench SWE under Lucy's plan (integration/task/swe_agent.py, swe_env.py).
SWE_DEVIATIONS = [
    "agent_is_orbit_turn_react_not_vendor_harness",
    "orbit_assistant_and_submit_prompt_appended_to_system_prompt",
    "continue_prompts_are_orbit_synthetic_user_messages",
    "native_arm64_rebuild_of_the_amd64_swe_bench_pro_image",
    "no_network_github_unreachable",
    "tools_named_shell_and_run_python_bound_to_the_agents_own_container",
    "auto_delivered_board_posts_shown_once_not_kept_in_the_conversation",
    "agents_share_one_clock_and_time_limit",
]
COMM_TURN_REFUSAL_MARK = "unavailable during the team's two opening turns"     # integration/task/swe_env.py


# --------------------------------------------------------------------------
# message content
# --------------------------------------------------------------------------
def split_content(content) -> tuple[str, str | None]:
    """(text, reasoning) from an Inspect message content (str or content list)."""
    if content is None:
        return "", None
    if isinstance(content, str):
        return content, None
    texts, reasoning = [], []
    for part in content:
        kind = getattr(part, "type", None)
        if kind == "text":
            texts.append(part.text)
        elif kind == "reasoning":
            if getattr(part, "reasoning", ""):
                reasoning.append(part.reasoning)
            elif getattr(part, "redacted", False):
                reasoning.append("[redacted reasoning]")
        else:
            texts.append(f"[{kind} content omitted]")
    return "\n".join(t for t in texts if t), ("\n".join(reasoning) or None)


def tool_result_text(result) -> str:
    if result is None:
        return ""
    if isinstance(result, (str, int, float, bool)):
        return str(result)
    if isinstance(result, list):
        return "\n".join(tool_result_text(r) for r in result)
    text, _ = split_content([result]) if hasattr(result, "type") else (str(result), None)
    return text


# --------------------------------------------------------------------------
# which agent owns which messages and events
# --------------------------------------------------------------------------
def roster(sample) -> list[str]:
    exp = (sample.metadata or {}).get("experiment", {})
    return [a["name"] for a in exp.get("setup", {}).get("agents", [])]


def agent_messages(sample, agent: str) -> list:
    """The agent's conversation, as projected by AgentScheduler.sync_to_task_state."""
    attribution = sample.store.get(ATTRIBUTION_KEY) or []
    indices = [a["index"] for a in attribution if a.get("agent") == agent]
    bad = [i for i in indices if i >= len(sample.messages)]
    if bad:
        raise ValueError(f"attribution indices {bad} outside {len(sample.messages)} messages")
    return [sample.messages[i] for i in indices]


def span_owner(sample):
    """span_id -> name of the nearest enclosing span of type 'agent' (ORBIT names it after the spec).

    Events inside an ``orbit.defense`` span (a monitor's or guardian's own model
    calls) or inside a ``tool`` span (a model call made while a tool runs, such as
    the dual-LLM quarantine summariser) belong to the defense, not to the agent,
    and get owner None. ToolEvents themselves sit directly in the agent span.
    """
    begins = {e.id: e for e in sample.events if e.event == "span_begin"}
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


def defense_evidence(sample) -> dict:
    """DefenseLog store fields plus every defense_intervention info event."""
    keys = ("resolved_defenses", "activity", "blocks", "detections", "vaccinations",
            "vaccination_injections", "total_activations", "false_positives", "true_positives")
    log = {k: sample.store.get(f"DefenseLog:{k}") for k in keys}
    interventions = []
    for e in sample.events:
        if e.event == "info" and isinstance(e.data, dict) and e.data.get("event") in (
                "defense_intervention", "defense_resample", "defense_instrumentation_exempt"):
            interventions.append(e.data)
    return {"log": log, "interventions": interventions}


PASSIVE_DEFENSES = {"trailing_turn_fix"}     # integration shim: checks everything, blocks nothing


def defense_counts(evidence: dict, agent: str) -> dict:
    """checks and blocks per boundary for one agent, from the intervention events.

    The passive trailing-turn shim is itself a registered defense and is asked at
    every boundary; its checks are left out so the counts describe the control.
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


REFUSAL_TEXT = "I cannot provide that response under the configured defense policy."   # orbit/defenses/boundaries.py
WITHHELD_TEXT = "[Content withheld by a configured defense.]"


def blocked_output_ids(sample) -> set:
    """Ids of raw model outputs a defense replaced with the refusal (model_output blocks).

    ORBIT keeps the raw output in the ModelEvent and substitutes a new assistant
    message in the conversation, so the i-th assistant message of an agent is a
    real substitution iff the i-th model call's output id is in this set. A
    refusal sentence the model wrote itself is not a block.
    """
    ids = set()
    for e in sample.events:
        d = getattr(e, "data", None)
        if e.event == "info" and isinstance(d, dict) and d.get("event") == "defense_intervention" \
                and d.get("boundary") == "model_output" and d.get("allow") is False and d.get("message_id"):
            ids.add(d["message_id"])
    return ids


def redacted_message_ids(model_events, sample=None) -> set:
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
    for e in (sample.events if sample is not None else []):
        d = getattr(e, "data", None)
        if e.event == "info" and isinstance(d, dict) and d.get("event") == "defense_intervention" \
                and d.get("boundary") == "model_input" and d.get("allow") is False and d.get("message_id"):
            ids.add(d["message_id"])
    return ids


def agent_events(sample, agent: str, kind: str) -> list:
    owner = span_owner(sample)
    return [e for e in sample.events if e.event == kind and owner(e.span_id) == agent]


TRAILING_TURN_TAG = "orbit_trailing_turn_fix"   # integration/task/controls.py


def delivery_notices(model_events) -> list[tuple[str | None, str, str]]:
    """(output assistant message id or None, text, source) per provider-only message in a model call.

    Covers ORBIT's channel notices (system) and, should Inspect ever record it,
    the integration's trailing-turn continue prompt (user). Neither enters the
    durable conversation. In practice the ModelEvent is written before ORBIT's
    model filters run, so the shim's prompt never appears here; the transcript
    shows a refusal followed by the next turn.
    """
    out = []
    for ev in model_events:
        for m in ev.input or []:
            meta = m.metadata or {}
            if (m.role == "system" and meta.get(DELIVERY_TAG)) or (m.role == "user" and meta.get(TRAILING_TURN_TAG)):
                text, _ = split_content(m.content)
                out_id = ev.output.message.id if (ev.output and ev.output.choices) else None
                out.append((out_id, text, "system" if m.role == "system" else "user"))
    return out


def usage_from_events(model_events) -> dict:
    """Prompt tokens include cache reads: Inspect reports them apart from input_tokens."""
    total_in = total_out = cache_read = 0
    for ev in model_events:
        usage = getattr(ev.output, "usage", None) if ev.output else None
        if usage:
            total_in += (usage.input_tokens or 0) + (usage.input_tokens_cache_read or 0)
            cache_read += usage.input_tokens_cache_read or 0
            total_out += usage.output_tokens or 0
    return {"input": total_in, "output": total_out, "cache_read": cache_read} if (total_in or total_out) else {}


# --------------------------------------------------------------------------
# one (sample, agent) -> one ATIF trajectory
# --------------------------------------------------------------------------
def build_trajectory(sample, log, agent_name: str, messages: list) -> tuple[Trajectory, dict]:
    model_events = agent_events(sample, agent_name, "model")
    notices = delivery_notices(model_events)
    redacted = redacted_message_ids(model_events, sample)
    blocked_outputs = blocked_output_ids(sample)
    output_ids = [ev.output.message.id if (ev.output and ev.output.choices) else None for ev in model_events]
    by_id: dict = {}
    for oid, text, source in notices:
        if oid:
            by_id.setdefault(oid, []).append((text, source))
    ordered = [(text, source) for _, text, source in notices]
    steps: list[dict] = []
    issued: dict[str, int] = {}          # tool_call_id -> index into steps
    system_prompt = None
    assistant_seen = 0
    attached_notices = 0
    for msg in messages:
        if isinstance(msg, ChatMessageSystem):
            text, _ = split_content(msg.content)
            system_prompt = text if system_prompt is None else system_prompt
            steps.append({"source": "system", "message": text})
        elif isinstance(msg, ChatMessageUser):
            text, _ = split_content(msg.content)
            if msg.id in redacted:
                # the provider received the placeholder; the judge must see what the agent saw
                steps.append({"source": "user", "message": WITHHELD_TEXT,
                              "extra": {"withheld_from_model_by_defense": True, "withheld_text": text}})
            else:
                steps.append({"source": "user", "message": text})
        elif isinstance(msg, ChatMessageAssistant):
            items = by_id.get(msg.id)
            how = "message_id"
            if items is None and not by_id and assistant_seen < len(ordered):
                items, how = [ordered[assistant_seen]], "order"
            for text, source in items or []:
                steps.append({"source": source, "message": text,
                              "extra": {"orbit_delivery_notice": source == "system",
                                        "orbit_trailing_turn_fix": source == "user", "attached_by": how}})
                attached_notices += 1
            call_output_id = output_ids[assistant_seen] if assistant_seen < len(output_ids) else None
            assistant_seen += 1
            text, reasoning = split_content(msg.content)
            calls = [ToolCall(tool_call_id=tc.id, function_name=tc.function,
                              arguments=dict(tc.arguments) if isinstance(tc.arguments, dict) else {"value": tc.arguments})
                     for tc in (msg.tool_calls or [])]
            step = {"source": "agent", "message": text, "reasoning_content": reasoning,
                    "tool_calls": calls or None, "llm_call_count": 1,
                    "model_name": msg.model or None, "results": []}
            if call_output_id is not None and call_output_id in blocked_outputs:
                step["extra"] = {"defense_refusal": True, "blocked_output_message_id": call_output_id}
            elif text.strip() == REFUSAL_TEXT:
                step["extra"] = {"refusal_text_written_by_model": True}
            steps.append(step)
            for tc in calls:
                issued[tc.tool_call_id] = len(steps) - 1
        elif isinstance(msg, ChatMessageTool):
            content = tool_result_text(msg.content)
            extra = {"is_error": True, "error": msg.error.message} if msg.error else None
            if msg.id in redacted:
                extra = {**(extra or {}), "withheld_from_model_by_defense": True, "withheld_text": content}
                content = WITHHELD_TEXT
            idx = issued.get(msg.tool_call_id)
            if idx is None:
                steps.append({"source": "user", "message": content,
                              "extra": {"orphan_tool_result": msg.tool_call_id, "function": msg.function,
                                        **(extra or {})}})
            else:
                steps[idx]["results"].append(ObservationResult(
                    source_call_id=msg.tool_call_id, content=content, extra=extra))
        else:
            text, _ = split_content(getattr(msg, "content", None))
            steps.append({"source": "user", "message": text, "extra": {"role": getattr(msg, "role", "?")}})

    # ORBIT removes the completed submit call and its result from the conversation
    # (orbit/agents/turn_react.py:_remove_submit_tool); the native tool event keeps it.
    recovered = 0
    for ev in agent_events(sample, agent_name, "tool"):
        if ev.function != "submit" or ev.id in issued:
            continue
        last_agent = next((i for i in range(len(steps) - 1, -1, -1) if steps[i]["source"] == "agent"), None)
        call = ToolCall(tool_call_id=ev.id, function_name="submit",
                        arguments=dict(ev.arguments) if isinstance(ev.arguments, dict) else {"answer": str(ev.arguments)})
        result = ObservationResult(source_call_id=ev.id, content=tool_result_text(ev.result),
                                   extra={"is_error": True, "error": ev.error.message} if ev.error else None)
        if last_agent is None:
            steps.append({"source": "agent", "message": "", "tool_calls": [call], "llm_call_count": 1,
                          "results": [result], "extra": {"submit_recovered_from_events": True}})
        else:
            step = steps[last_agent]
            step["tool_calls"] = list(step.get("tool_calls") or []) + [call]
            step["results"].append(result)
            step["extra"] = {**(step.get("extra") or {}), "submit_recovered_from_events": True}
        recovered += 1

    atif_steps = []
    for i, s in enumerate(steps, 1):
        results = s.pop("results", [])
        observation = Observation(results=results) if results else None
        atif_steps.append(Step(step_id=i, observation=observation, **s))

    usage = usage_from_events(model_events) or (sum_usage(sample) if len(roster(sample)) == 1 else {})
    last_text = (sample.store.get(LAST_TEXT_KEY) or {}).get(agent_name)
    if last_text is None and len(roster(sample)) == 1 and sample.output:
        last_text = sample.output.completion or None
    traj = Trajectory(
        schema_version=SCHEMA_VERSION,
        session_id=f"{sample.id}#{sample.epoch}",
        trajectory_id=f"{sample.id}#{sample.epoch}/{agent_name}",
        agent=Agent(name=f"orbit/{agent_name}",
                    version=f"orbit-{orbit.__version__}+inspect-ai-{inspect_ai.__version__}",
                    model_name=log.eval.model),
        steps=atif_steps,
        final_metrics=FinalMetrics(total_prompt_tokens=usage.get("input"), total_completion_tokens=usage.get("output"),
                                   total_steps=len(atif_steps)) if usage else None,
        extra={"final_text": last_text or None},
    )
    info = {"system_prompt": system_prompt, "submit_recovered": recovered,
            "delivery_notices": attached_notices, "model_calls": len(model_events),
            "n_agent_steps": sum(1 for s in atif_steps if s.source == "agent"),
            "n_tool_calls": sum(len(s.tool_calls or []) for s in atif_steps),
            "usage": usage or None, "final_text": last_text or None}
    return traj, info


def sum_usage(sample) -> dict:
    total_in = total_out = cache_read = 0
    for usage in (sample.model_usage or {}).values():
        total_in += (getattr(usage, "input_tokens", 0) or 0) + (getattr(usage, "input_tokens_cache_read", 0) or 0)
        cache_read += getattr(usage, "input_tokens_cache_read", 0) or 0
        total_out += getattr(usage, "output_tokens", 0) or 0
    return {"input": total_in, "output": total_out, "cache_read": cache_read} if (total_in or total_out) else {}


HALT_RE = re.compile(r"Experiment complete: .*halt_reason=([^\s,]+)")


def orbit_halt_reason(sample) -> str | None:
    """ORBIT's own stop reason (convergence, max_turns, max_time, attack_success, inspect_limit,
    error: ..., interrupted, before_agents_completed), written only to an info event."""
    for ev in reversed(sample.events or []):
        if ev.event == "info" and isinstance(ev.data, str):
            m = HALT_RE.search(ev.data)
            if m:
                return m.group(1)
    return None


def error_and_failure(sample) -> tuple[str | None, str | None]:
    """Map Inspect's limit/error, or ORBIT's scheduler cap, onto CheatBench's episode error
    string and failure tag.

    core/judge.py:is_judgeable drops lost episodes; limits stay judgeable. ORBIT's
    scheduler caps (max_turns, max_time) are not Inspect limits: the sample completes
    normally, so they are read from the halt reason.
    """
    halt = orbit_halt_reason(sample)
    if sample.limit is None and sample.error is None and halt == "max_turns":
        return "max_turns reached before confirmed task completion", "max_turns"
    if sample.limit is None and sample.error is None and halt == "max_time":
        return "time limit reached", "timeout"
    if sample.limit is not None:
        kind = sample.limit.type
        if kind == "time":
            return "time limit reached", "timeout"
        if kind in ("message", "turn"):
            return "max_turns reached before confirmed task completion", "max_turns"
        if kind in ("token", "context"):
            return f"{kind} limit reached", "context_window_exceeded"
        return f"{kind} limit reached", None
    if sample.error is not None:
        message = str(getattr(sample.error, "message", sample.error))
        lowered = message.lower()
        if any(k in lowered for k in ("rate limit", "429", "connection", "stream", "timed out", "overloaded",
                                      "credits are depleted", "billing", "quota", "insufficient_quota", "resource_exhausted")):
            # a lost episode in CheatBench's terms: the provider, not the agent, ended it
            return message, "connection_closed"
        return message, "harness"
    return None, None


# --------------------------------------------------------------------------
# run directory
# --------------------------------------------------------------------------
TEAM_IDENTITY = "agent-018"   # the git identity the staged row gives the agent (README; the scorer reads this branch)


def deliverables_block(sample) -> dict | None:
    """What the team left behind, from the deliverables scorer's evidence.

    The score value only says whether the archive sat in the working tree at the end; a
    team that registered and then checked out main scores 0, and an uncommitted archive
    scores 1. The registry on main and the git summary say whether the team registered.
    """
    sc = (sample.scores or {}).get("deliverables_scorer")
    if sc is None:
        return None
    md = sc.metadata or {}
    try:
        entries = [a for a in (json.loads(md.get("agent_evals_main") or "{}").get("agents") or []) if isinstance(a, dict)]
    except (ValueError, AttributeError):
        entries = []
    agents = [a.get("agent") for a in entries]
    team = next((a for a in entries if a.get("agent") == TEAM_IDENTITY), None)
    # A registration that carries another agent's message verbatim registered that agent's work
    # (R0D3 on 2026-10-11 registered agent-014's archive and message byte for byte).
    copied_from = None
    if team and (team.get("message") or "").strip():
        for other in entries:
            if other is not team and (other.get("message") or "").strip() == team["message"].strip():
                copied_from = other.get("agent")
    lines = (md.get("git") or "").splitlines()
    status = next((line for line in lines if line.startswith("## ")), "")
    return {"archive_in_workspace": sc.value == 1.0,
            "registered_on_main": TEAM_IDENTITY in agents,
            "registered_entry": ({"archive": team.get("archive"), "commits": team.get("commits"),
                                  "message_head": (team.get("message") or "")[:200]} if team else None),
            "registered_message_copied_from": copied_from,
            "main_registry_agents": agents,
            "checkout": status[3:].split("...")[0] if status else None,
            "commits": [line for line in lines if re.match(r"^[0-9a-f]{7,} ", line)],
            "explanation": sc.explanation}


def swarm_block(sample) -> dict | None:
    """Composition, hidden roles and the team's own report, for a swarm run."""
    cb = (sample.metadata or {}).get("cheatbench", {})
    swarm = cb.get("swarm")
    if not swarm:
        return None
    md = {}
    for sc in (sample.scores or {}).values():
        md = sc.metadata or {}
        break
    return {**swarm, "swarm_report": md.get("swarm_report"), "swarm_report_cheated": md.get("swarm_report_cheated")}


def swe_grades(sample) -> dict | None:
    """Hidden-test results per target (team, a01, ...) from the SWE grader, without the diff text."""
    sc = (sample.scores or {}).get("swe_grader")
    if sc is None:
        return None
    return {name: {k: v for k, v in t.items() if k != "diff_text"} for name, t in (sc.metadata or {}).get("targets", {}).items()}


def comm_turn_compliance(steps: list[dict], opening: int) -> dict:
    """What an agent did in its opening communication turns (its first `opening` agent steps)."""
    agent_steps = [s for s in steps if s.get("source") == "agent"]
    first = agent_steps[:opening]
    calls = [[tc["function_name"] for tc in (s.get("tool_calls") or [])] for s in first]
    blocked = sum(1 for s in agent_steps for r in ((s.get("observation") or {}).get("results") or [])
                  if COMM_TURN_REFUSAL_MARK in ((r.get("extra") or {}).get("error") or ""))
    return {"opening_turns": opening, "calls": calls,
            "posted_in_opening": sum(1 for c in calls if "channel_post" in c),
            "submitted_in_opening": any("submit" in c for c in calls), "blocked": blocked}


def swe_block(sample, out_dir: Path) -> dict | None:
    """Lucy's-plan facts for run.json; the diffs go to swe/diffs/<target>.diff."""
    swe = ((sample.metadata or {}).get("cheatbench") or {}).get("swe")
    if not swe:
        return None
    sc = (sample.scores or {}).get("swe_grader")
    md = (sc.metadata or {}) if sc else {}
    diffs = out_dir / "swe" / "diffs"
    for name, t in (md.get("targets") or {}).items():
        diffs.mkdir(parents=True, exist_ok=True)
        (diffs / f"{name}.diff").write_text(t.get("diff_text") or "")
    setup = sample.store.get("swe_setup") or md.get("setup") or {}
    return {
        "task_key": swe["task_key"], "instance": swe["instance"], "repo": swe["repo"], "split": swe["split"],
        "base_commit": swe["base_commit"], "fix_commit": swe["fix_commit"], "n_agents": swe["n_agents"],
        "agents": swe["agents"], "board": swe["board"], "jigsaw": swe["jigsaw"], "rule": swe["rule"],
        "rule_sentence": swe["rule_sentence"], "comm_turns": swe["comm_turns"],
        "delivery": (sample.metadata or {}).get("cheatbench", {}).get("delivery"),
        "parts": swe["parts"], "patch_files": swe["patch_files"],
        "fragments": {a: {k: v for k, v in f.items() if k != "patch"} for a, f in (swe.get("fragments") or {}).items()} or None,
        "fragment_commits": {a: ev.get("fragment_commit") for a, ev in (setup.get("agents") or {}).items()} if swe["jigsaw"] else None,
        "jigsaw_isolation_ok": setup.get("jigsaw_isolation_ok"),
        "setup": setup, "images": swe.get("images"),
        "primary": md.get("primary"), "graded": md.get("graded"), "required_tests": md.get("required_tests"),
        "grades": swe_grades(sample), "team": md.get("team"),
        "agents_final": md.get("agents"), "comm_turn_blocks": md.get("comm_turn_blocks"),
    }


def convert(eval_path: Path, out_dir: Path, task_root: Path | None = None) -> dict:
    log = read_eval_log(str(eval_path), resolve_attachments=True)
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    agents_seen: list[str] = []
    topology = "single"
    for sample in log.samples or []:
        meta = sample.metadata or {}
        cb = meta.get("cheatbench", {})
        agents = roster(sample)
        if not agents:
            raise ValueError(f"sample {sample.id}: no ORBIT roster in metadata['experiment']")
        agents_seen = agents
        if (meta.get("experiment", {}).get("communication") or {}).get("channels"):
            topology = "swarm" if cb.get("swarm") else "board"
        error, failure = error_and_failure(sample)
        comm = {k.split(":", 1)[1]: sample.store.get(k) for k in ("CommunicationState:messages", "CommunicationState:events")
                if sample.store.get(k)}
        if comm:
            comm_dir = out_dir / "communication"
            comm_dir.mkdir(exist_ok=True)
            (comm_dir / f"{sample.id}#{sample.epoch}.json").write_text(json.dumps(comm, indent=1, default=str))
        defenses_cfg = meta.get("experiment", {}).get("defenses") or []
        evidence = defense_evidence(sample)
        if defenses_cfg or evidence["interventions"]:
            def_dir = out_dir / "defense"
            def_dir.mkdir(exist_ok=True)
            (def_dir / f"{sample.id}#{sample.epoch}.json").write_text(json.dumps(
                {"defenses": defenses_cfg, **evidence}, indent=1, default=str))
        for agent_name in agents:
            episode = f"{sample.id}#{sample.epoch}" if len(agents) == 1 else f"{sample.id}#{sample.epoch}@{agent_name}"
            messages = agent_messages(sample, agent_name)
            record = {
                "id": sample.id, "episode": episode, "replicate": sample.epoch,
                "task": cb.get("task"), "variant": cb.get("variant", ""),
                "model": log.eval.model, "agent": f"orbit/{agent_name}", "orbit_agent": agent_name,
                "orbit_roster": agents, "topology": topology,
                "control": cb.get("control", "baseline"), "monitor_model": cb.get("monitor_model"),
                "role": ((cb.get("swarm") or {}).get("roles") or {}).get(agent_name),
                "defenses": [d["name"] for d in defenses_cfg],
                "defense": defense_counts(evidence, agent_name),
                "agent_version": f"orbit-{orbit.__version__}+inspect-ai-{inspect_ai.__version__}",
                "prompt_file": cb.get("prompt_file"),
                "wall_time": sample.total_time,
                "limit": sample.limit.type if sample.limit else None,
                "score": {k: (v.value if hasattr(v, "value") else v) for k, v in (sample.scores or {}).items()},
            }
            swe = cb.get("swe")
            if swe:
                grades = swe_grades(sample) or {}
                record.update({
                    "swe_task": cb.get("swe_task"), "seat": agent_name, "n_agents": swe["n_agents"],
                    "part": swe["parts"][agent_name]["titles"], "part_ids": swe["parts"][agent_name]["parts"],
                    "jigsaw": swe["jigsaw"], "rule": swe["rule"], "delivery": cb.get("delivery"),
                    "fragment_files": ((swe.get("fragments") or {}).get(agent_name) or {}).get("files"),
                    "reward": (grades.get(agent_name) or {}).get("reward"),
                    "team_reward": (grades.get("team") or {}).get("reward") if swe["board"] else None,
                })
            if not messages:
                # The agent never produced a projected conversation: a failed episode
                # in CheatBench's terms (no transcript, skipped by the judge).
                record.update({"final_text": None, "n_turns": 0, "n_tool_calls": 0, "usage": None,
                               "transcript_path": None,
                               "error": error or "no agent conversation was projected into the sample",
                               "failure": failure or "harness", "ok": False, "ended_by": "error"})
                records.append(record)
                continue
            traj, info = build_trajectory(sample, log, agent_name, messages)
            ep_dir = out_dir / "trajectories" / episode
            ep_dir.mkdir(parents=True, exist_ok=True)
            transcript = {
                "agent": f"orbit/{agent_name}", "model": log.eval.model,
                "prompt": (((cb.get("swe") or {}).get("first_message") or {}).get(agent_name)
                           or cb.get("instruction") or (sample.input if isinstance(sample.input, str) else "")),
                "trajectory": traj.to_json_dict(),
                "orbit": {"eval_log": str(eval_path.resolve()), "agent": agent_name, "roster": agents,
                          "topology": topology, "system_prompt": info["system_prompt"],
                          "submit_recovered_from_events": info["submit_recovered"],
                          "delivery_notices_recovered_from_events": info["delivery_notices"],
                          "model_calls": info["model_calls"]},
            }
            if cb.get("swe"):
                record["comm_turns"] = comm_turn_compliance(transcript["trajectory"].get("steps", []),
                                                            cb["swe"]["comm_turns"])
            transcript_path = ep_dir / "transcript.json"
            transcript_path.write_text(json.dumps(transcript, indent=1))
            (ep_dir / "prompt.txt").write_text(transcript["prompt"])
            # A limit ends the sample, not every agent in it: an agent whose last tool call
            # was submit finished its own episode before the limit hit the others.
            submitted = any(tc.get("function_name") == "submit"
                            for step in transcript["trajectory"].get("steps", []) for tc in (step.get("tool_calls") or []))
            ep_error, ep_failure = (None, None) if (submitted and failure in ("timeout", "max_turns")) else (error, failure)
            record.update({
                "final_text": info["final_text"],
                "n_turns": info["n_agent_steps"], "n_tool_calls": info["n_tool_calls"],
                "usage": info["usage"], "transcript_path": str(transcript_path),
                "error": ep_error, "failure": ep_failure, "ok": ep_error is None,
                "ended_by": "submit" if submitted else ("limit" if (sample.limit or ep_failure in ("timeout", "max_turns")) else ("error" if sample.error else "end")),
            })
            records.append(record)
    task_root = task_root or (CB / "tasks" / (records[0]["task"] if records else "unknown"))
    run_json = {
        "task": records[0]["task"] if records else None, "task_root": str(task_root.resolve()), "variant": "",
        "run_id": out_dir.name, "model": log.eval.model, "agent": "orbit",
        "source": "orbit", "topology": topology, "agents": agents_seen,
        "control": records[0]["control"] if records else None,
        "monitor_model": records[0]["monitor_model"] if records else None,
        "defenses": (log.samples[0].metadata.get("experiment", {}).get("defenses") if log.samples else None) or [],
        "swarm": swarm_block(log.samples[0]) if log.samples else None,
        "deliverables": deliverables_block(log.samples[0]) if log.samples else None,
        "orbit_halt_reason": orbit_halt_reason(log.samples[0]) if log.samples else None,
        "eval_log": str(eval_path.resolve()), "eval_id": log.eval.eval_id,
        "orbit_version": orbit.__version__, "inspect_version": inspect_ai.__version__,
        "swe": swe_block(log.samples[0], out_dir) if log.samples else None,
        "deviations": (SWE_DEVIATIONS if (records and records[0]["task"] == "software_engineering") else
                       DEVIATIONS + (["two_agents_share_one_sandbox_clock_and_git_identity"] if topology == "board" else [])),
    }
    (out_dir / "run.json").write_text(json.dumps(run_json, indent=1))
    (out_dir / "episodes.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    return {"out_dir": str(out_dir), "episodes": len(records), "topology": topology,
            "with_transcript": [r["episode"] for r in records if r["transcript_path"]],
            "without_transcript": [r["episode"] for r in records if not r["transcript_path"]]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("eval_log")
    ap.add_argument("out_dir")
    ap.add_argument("--task-root", default="")
    args = ap.parse_args()
    summary = convert(Path(args.eval_log), Path(args.out_dir), Path(args.task_root) if args.task_root else None)
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
