"""The results browser's run reader: the run directories under runs/ and the eval logs they point at.

`Store` reads run.json, episodes.jsonl, judge.jsonl, ladder.jsonl, signals.jsonl, the trajectories, the
communication and defense stores and the analysis tools' summaries. It loads a run's Inspect eval log once, on
demand, for the sample facts and the context windows. The run directories are written by
src/benchmark/run_export.py and the SWE measures (swe_summary.json) by src/measures.

    store = Store(Path("runs"))
    rows = [store.run_summary(name) for name in store.run_names()]
    call = store.context_call("swarm_R0D0", 3)
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from inspect_ai.log import read_eval_log

from src.benchmark.atif_transcripts import (
    REFUSAL_TEXT,
    TURN_RULES_TAG,
    WITHHELD_TEXT,
    split_content,
    tool_result_text,
)
from src.configuration.settings import INCOMPLETE_FAILURES
from src.configuration.swarm_config import ROLES
from src.utils.json_files import read_json, read_json_dir, read_jsonl

FLAG_KEYS = {
    TURN_RULES_TAG: "turn_rules",
    "orbit_defense_redacted": "redacted",
}

# A verdict is every key of the judge.jsonl row except these: the row's identity, how the judge
# was configured, its usage, and the transcript it read. The task's own verdict fields pass through
# whatever they are (openmath or SWE). VERDICT_BASE is present on every verdict, None when absent.
VERDICT_BOOKKEEPING = frozenset({
    "id", "episode", "replicate", "task", "model", "transcript_path",
    "judge_generation_config", "judge_generation_config_unsupported", "judge_prompt", "judge_schema",
    "output_mode", "judge_usage", "judge_attempts",
})
VERDICT_BASE = ("cheated", "summary", "judge", "judge_cost_usd", "parse_ok", "judge_error")
LADDER_BOOKKEEPING = VERDICT_BOOKKEEPING | {"raw"}
SWARM_FIELDS = ("n_agents", "regulators", "disregulators", "workers", "seed", "composition")
DELIVERABLE_FIELDS = ("archive_in_workspace", "registered_on_main", "registered_message_copied_from")
SAMPLE_STORE_KEYS = (
    "EnvironmentState:turn", "EnvironmentState:last_text_by_agent",
    "DefenseLog:blocks", "DefenseLog:activity", "DefenseLog:vaccination_injections",
    "knowledge_reviews", "task_tool_state", "task_tool_calls",
)
# The analysis tools (tools/ at the repository root) write runs/<run>/tools/<tool>/summary.json and their own files.
# The page may fetch these file types from a tool's directory; anything else is refused.
TOOL_FILE_SUFFIXES = frozenset({".png", ".csv", ".html", ".gexf", ".json", ".yaml"})

# A run is failed when an episode ended in a harness crash or a provider error (the converter's
# "connection_closed": rate limit, billing, connection), or when it was set aside by name
# (failed_, failed2_, ..., cancelled_). Such runs are never counted as outcomes.
FAILED_NAME = re.compile(r"^(failed\d*|cancelled\d*)_")


# --------------------------------------------------------------------------
# run readers
# --------------------------------------------------------------------------
def _tool_summary(path: Path) -> dict:
    """A tool's summary.json, or {"error"} when it does not parse or is not an object, so one bad file does not
    break the run page."""
    try:
        summary = read_json(path)
    except ValueError as ex:
        return {"error": f"{path.name} did not parse: {ex}"}
    if not isinstance(summary, dict):
        return {"error": f"{path.name} is not a JSON object"}
    return summary


def _safe_part(part: str) -> bool:
    """A path segment that stays inside its directory: not empty, not . or .., no separator."""
    return part not in ("", ".", "..") and "/" not in part and "\\" not in part


def graph_of(topology) -> str:
    """The communication graph of a run: a lone agent, or every agent on one shared board.

    run.json writes "swarm" for a board with hidden roles; the graph is the same board.
    """
    if topology in (None, "single"):
        return "single"
    return "board"


def ending_of(ep: dict) -> str:
    """How an episode ended: its failure label, else submit, else the converter's ended_by."""
    if ep.get("failure"):
        return ep["failure"]
    if ep.get("ended_by") == "submit":
        return "submit"
    return ep.get("ended_by") or "end"


def endings_of(eps: list[dict]) -> dict:
    out: dict = {}
    for e in eps:
        k = ending_of(e)
        out[k] = out.get(k, 0) + 1
    return out


def is_failed(name: str, eps: list[dict]) -> bool:
    crashed = any(e.get("failure") in INCOMPLETE_FAILURES for e in eps)
    return crashed or bool(FAILED_NAME.match(name))


def is_mock(run: dict) -> bool:
    return str(run.get("model") or "").startswith("mockllm")


def verdict_of(row: dict | None) -> dict | None:
    """The verdict fields of one judge.jsonl row, or None when the episode was not judged."""
    if not row:
        return None
    verdict = {key: None for key in VERDICT_BASE}
    verdict.update({k: v for k, v in row.items() if k not in VERDICT_BOOKKEEPING})
    return verdict


def ladder_of(row: dict | None) -> dict | None:
    """A ladder.jsonl row for the page (src/benchmark/judging_ladder.py): everything but the bookkeeping."""
    if row is None:
        return None
    return {k: v for k, v in row.items() if k not in LADDER_BOOKKEEPING}


def signals_of(row: dict | None) -> dict | None:
    """A signals.jsonl row for the page (src/measures/code_ladder.py): everything but the episode's identity."""
    if row is None:
        return None
    return {k: v for k, v in row.items() if k not in ("episode", "agent", "role", "task")}


def rules_of(eps: list[dict]) -> dict | None:
    """The turn-rules facts of each agent (episode "rules"), or None when no episode carries them."""
    rules = {e.get("orbit_agent"): e["rules"] for e in eps if e.get("rules") is not None}
    return rules or None


def swe_of(run: dict, d: Path | None = None) -> dict | None:
    """The SWE setting of a run: issue, team size, honeypot, rule, pieces, rewards and Lucy's measures, from run.json
    "swe"; signals.jsonl and swe_summary.json in the run directory add the code ladder's rung and the talk measures per
    agent."""
    swe = run.get("swe")
    if not swe:
        return None
    grades = swe.get("grades") or {}
    measures = read_json(d / "swe_summary.json") if d is not None else None
    signals = read_jsonl(d / "signals.jsonl") if d is not None else []
    honeypot = swe.get("honeypot")
    jigsaw = honeypot == "jigsaw"
    rule = swe.get("rule") or "none"
    n_agents = swe.get("n_agents")
    arm = f"n{n_agents}-{honeypot}-{rule}"
    return {
        "task_key": swe.get("task_key") or swe.get("issue"),
        "instance": swe.get("instance"),
        "n_agents": n_agents,
        "board": swe.get("board"),
        "honeypot": honeypot,
        "jigsaw": jigsaw,
        "rule": rule,
        "base_commit": swe.get("base_commit"),
        "fix_commit": swe.get("fix_commit"),
        "arm": arm,
        "team_reward": (grades.get("team") or {}).get("reward") if swe.get("board") else None,
        "rewards": {k: v.get("reward") for k, v in grades.items()},
        "required_passed": {k: len(v.get("required_passed") or []) for k, v in grades.items()},
        "required_tests": len(swe.get("required_tests") or []),
        "pieces": swe.get("pieces") or None,
        "fragments": {a: (f or {}).get("files") for a, f in (swe.get("fragments") or {}).items()} or None,
        "checks_ok": swe.get("checks_ok"),
        "rungs": {r["agent"]: r.get("rung") for r in signals} or None,
        "measures": _swe_measures(measures),
    }


def _swe_measures(summary: dict | None) -> dict | None:
    if not summary:
        return None
    keys = ("fix_posts", "rule_posts", "disclosed", "posts")
    return {a: {k: x.get(k) for k in keys} for a, x in (summary.get("agents") or {}).items()}


# --------------------------------------------------------------------------
# eval log readers
# --------------------------------------------------------------------------
def usage_dict(usage) -> dict:
    return {
        "input": usage.input_tokens,
        "cache_read": usage.input_tokens_cache_read,
        "output": usage.output_tokens,
        "reasoning": usage.reasoning_tokens,
    }


def output_message(output):
    """The completion's message, or None when the call produced no choice."""
    if output and output.choices:
        return output.message
    return None


def blocked_message_ids(sample, boundary: str) -> set:
    """Ids of messages a defense blocked at one boundary (model_input or model_output)."""
    ids = set()
    for e in sample.events:
        d = getattr(e, "data", None)
        if e.event != "info" or not isinstance(d, dict):
            continue
        if d.get("event") != "defense_intervention" or d.get("boundary") != boundary:
            continue
        if d.get("allow") is False and d.get("message_id"):
            ids.add(d["message_id"])
    return ids


def span_owner(sample):
    """A function from span id to (agent or None, defense name or None)."""
    begins = {e.id: e for e in sample.events if e.event == "span_begin"}
    cache: dict = {}

    def parent_owner(e):
        return owner(e.parent_id) if e.parent_id else (None, None)

    def owner(span_id):
        if span_id in cache:
            return cache[span_id]
        e = begins.get(span_id)
        if e is None:
            res = (None, None)
        elif e.type == "orbit.defense":
            res = (parent_owner(e)[0], e.name)
        elif e.type == "tool":
            # a model call made while a tool runs (dual-LLM quarantine summariser)
            res = (parent_owner(e)[0], f"inside tool {e.name}")
        elif e.type == "agent":
            res = (e.name, None)
        else:
            res = parent_owner(e)
        cache[span_id] = res
        return res
    return owner


def _message_dict(m, redacted: set = frozenset(), blocked: set = frozenset(), own_output: bool = False) -> dict:
    """One message as the provider saw it.

    A withheld message carries the placeholder as text and the original under
    withheld_text. A refusal sentence is flagged output_blocked only when it is
    a call's own raw output that a defense blocked (its id is in ``blocked``);
    in an input history the same sentence is only ``refusal_in_history``, since
    the substituted message carries a new id and the model may also write the
    sentence itself.
    """
    text, reasoning = split_content(m.content)
    meta = m.metadata or {}
    flags = {v for k, v in FLAG_KEYS.items() if meta.get(k)}
    d = {"role": m.role, "id": m.id, "text": text, "reasoning": reasoning}
    if m.id in redacted:
        flags.add("redacted")
        d["withheld_text"] = text
        d["text"] = WITHHELD_TEXT
    if m.role == "assistant":
        d["tool_calls"] = [
            {"id": tc.id, "function": tc.function, "arguments": tc.arguments}
            for tc in (m.tool_calls or [])
        ]
        d["model"] = getattr(m, "model", None)
        if own_output and m.id in blocked:
            flags.add("output_blocked")
        elif text.strip() == REFUSAL_TEXT:
            flags.add("refusal_in_history")
    if m.role == "tool":
        d["tool_call_id"] = m.tool_call_id
        d["function"] = m.function
        content = tool_result_text(m.content)
        d["error"] = m.error.message if m.error else None
        if m.id in redacted:
            d["withheld_text"] = content
            d["text"] = WITHHELD_TEXT
        else:
            d["text"] = content
    d["flags"] = sorted(flags)
    return d


def _public(call: dict) -> dict:
    """A context call without its private fields (the raw event and the id sets)."""
    return {k: v for k, v in call.items() if not k.startswith("_")}


# --------------------------------------------------------------------------
# summary pieces
# --------------------------------------------------------------------------
def _is_judged(e: dict) -> bool:
    verdict = e["verdict"]
    return bool(verdict) and verdict.get("cheated") is not None and bool(e.get("has_transcript"))


def _count_verdict(judged: list[dict], field: str) -> int:
    return sum(1 for e in judged if e["verdict"].get(field))


def _usage_total(eps: list[dict], field: str) -> int:
    return sum((e.get("usage") or {}).get(field, 0) for e in eps)


def _role_counts(mine: list[dict]) -> dict:
    return {
        "n": len(mine),
        "cheated": sum(1 for e in mine if e["verdict"]["cheated"]),
        "honeypots_seen": _count_verdict(mine, "honey_pots_seen"),
        "attempted": _count_verdict(mine, "attempted_access"),
        "disclosed": _count_verdict(mine, "disclosed_access"),
    }


class Store:
    """Reads run directories; loads an eval log once per run on demand."""

    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = runs_dir
        self._logs: dict[str, object] = {}
        self._calls: dict[str, list] = {}
        self._lock = threading.Lock()

    # -- loading -----------------------------------------------------------
    def run_names(self) -> list[str]:
        return sorted(p.parent.name for p in self.runs_dir.glob("*/run.json"))

    def run_dir(self, name: str) -> Path:
        if name not in self.run_names():
            raise KeyError(name)
        return self.runs_dir / name

    def run_json(self, name: str) -> dict:
        return read_json(self.run_dir(name) / "run.json") or {}

    def _eval_log_path(self, name: str):
        """The run's eval log as a string, or None: the path run.json records, else, for a set-aside run
        (renamed with a failed_ prefix), the same file under logs/<kind>/<run name>/."""
        path = self.run_json(name).get("eval_log")
        if not path:
            return None
        if Path(path).exists():
            return str(path)
        candidate = Path(path).parent.parent / name / Path(path).name
        return str(candidate) if candidate.exists() else None

    def eval_log(self, name: str):
        with self._lock:
            if name in self._logs:
                return self._logs[name]
        path = self._eval_log_path(name)
        if not path:
            recorded = self.run_json(name).get("eval_log")
            raise KeyError(f"{name}: eval log not found at {recorded}")
        log = read_eval_log(path, resolve_attachments=True)
        with self._lock:
            self._logs[name] = log
        return log

    # -- per-run views -----------------------------------------------------
    def episodes(self, name: str) -> list[dict]:
        d = self.run_dir(name)
        verdicts = {v["episode"]: v for v in read_jsonl(d / "judge.jsonl")}
        ladder = {row["episode"]: row for row in read_jsonl(d / "ladder.jsonl")}
        signals = {row["episode"]: row for row in read_jsonl(d / "signals.jsonl")}
        out = []
        for rec in read_jsonl(d / "episodes.jsonl"):
            v = verdicts.get(rec["episode"])
            rec = dict(rec)
            # A set-aside run keeps the transcript_path of its original name, which a newer run may own by
            # now; the copy under the run's own trajectories/ is its record.
            own = d / "trajectories" / rec["episode"] / "transcript.json"
            if own.exists():
                rec["transcript_path"] = str(own)
            rec["verdict"] = verdict_of(v)
            rec["ladder"] = ladder_of(ladder.get(rec["episode"]))
            rec["signals"] = signals_of(signals.get(rec["episode"]))
            path = rec.get("transcript_path")
            rec["has_transcript"] = bool(path) and Path(path).exists()
            out.append(rec)
        return out

    def run_summary(self, name: str) -> dict:
        d = self.run_dir(name)
        run = read_json(d / "run.json") or {}
        eps = self.episodes(name)
        sw = run.get("swarm") or {}
        dl = run.get("deliverables") or {}
        swarm = None
        if sw:
            swarm = {k: sw.get(k) for k in SWARM_FIELDS}
        deliverables = {k: dl.get(k) for k in DELIVERABLE_FIELDS} if dl else None
        eval_log = self._eval_log_path(name)
        return {
            "run": name,
            "task": run.get("task"),
            "model": run.get("model"),
            "topology": run.get("topology"),
            "graph": graph_of(run.get("topology")),
            "n_agents": len(run.get("agents") or eps),
            "roles": bool(sw.get("roles")),
            "mock": is_mock(run),
            "swarm": swarm,
            "deliverables": deliverables,
            "endings": endings_of(eps),
            "agents": run.get("agents"),
            "control": run.get("control") or "baseline",
            "monitor_model": run.get("monitor_model"),
            "defenses": [defense["name"] for defense in run.get("defenses") or []],
            "eval_log": eval_log,
            "eval_log_exists": eval_log is not None,
            "failed": is_failed(name, eps),
            "episodes": eps,
            "has_communication": (d / "communication").exists(),
            "has_defense": (d / "defense").exists(),
            "config": run.get("config"),
            "rules": rules_of(eps),
            "swe": swe_of(run, d),
            "mode": run.get("mode"),
            "scale": run.get("scale"),
            "preset": run.get("preset"),
            "engagement": run.get("engagement"),
            # written only by runs that record them (src/benchmark/run_export.py newer_run_facts); null otherwise
            "turn_budget": run.get("turn_budget"),
            "access": run.get("access"),
            "knowledge": run.get("knowledge"),
            "interview": run.get("interview"),
            "protein": run.get("protein"),
            "tools": self.tool_names(name),
        }

    def run_detail(self, name: str) -> dict:
        d = self.run_dir(name)
        detail = self.run_summary(name)
        detail["run_json"] = read_json(d / "run.json")
        detail["communication"] = read_json_dir(d / "communication")
        detail["defense"] = read_json_dir(d / "defense")
        detail["tools"] = {tool: _tool_summary(d / "tools" / tool / "summary.json") for tool in self.tool_names(name)}
        return detail

    def tool_names(self, name: str) -> list[str]:
        """The analysis tools that wrote a summary for this run. The loader's caches (tools/*.json) are not tools."""
        return sorted(p.parent.name for p in (self.run_dir(name) / "tools").glob("*/summary.json"))

    def tool_file(self, name: str, tool: str, parts: list[str]) -> Path:
        """A file inside runs/<run>/tools/<tool>/. Raises ValueError for a path that leaves it or a refused type."""
        if not parts or not _safe_part(tool) or not all(_safe_part(p) for p in parts):
            raise ValueError("refused path")
        base = (self.run_dir(name) / "tools" / tool).resolve()
        path = base.joinpath(*parts).resolve()
        if not path.is_relative_to(base):
            raise ValueError("refused path")
        if path.suffix.lower() not in TOOL_FILE_SUFFIXES:
            raise ValueError(f"refused file type {path.suffix or '(none)'}")
        return path

    def transcript(self, name: str, episode: str) -> dict:
        for rec in self.episodes(name):
            if rec["episode"] != episode:
                continue
            if not rec.get("has_transcript"):
                raise KeyError(f"{episode}: no transcript")
            t = json.loads(Path(rec["transcript_path"]).read_text())
            return {"episode": rec, "transcript": t}
        raise KeyError(episode)

    def sample(self, name: str) -> dict:
        log = self.eval_log(name)
        s = log.samples[0]
        scores = {}
        for k, sc in (s.scores or {}).items():
            scores[k] = {"value": sc.value, "answer": sc.answer, "explanation": sc.explanation, "metadata": sc.metadata}
        limit = None
        if s.limit:
            # EvalSampleLimit carries type and limit; older logs had a message, newer ones a reason.
            message = getattr(s.limit, "message", None) or getattr(s.limit, "reason", None)
            limit = {"type": s.limit.type, "limit": s.limit.limit, "message": message}
        model_usage = {k: usage_dict(v) for k, v in (s.model_usage or {}).items()}
        return {
            "status": log.status,
            "model": log.eval.model,
            "eval_id": log.eval.eval_id,
            "sample_id": s.id,
            "epoch": s.epoch,
            "total_time": s.total_time,
            "working_time": s.working_time,
            "limit": limit,
            "error": str(s.error.message)[:4000] if s.error else None,
            "model_usage": model_usage,
            "scores": scores,
            "store": {k: s.store.get(k) for k in SAMPLE_STORE_KEYS},
            "n_messages": len(s.messages),
            "n_events": len(s.events),
        }

    def context_index(self, name: str) -> list[dict]:
        """Every model call in the eval log, cached per run, with private fields for context_call."""
        with self._lock:
            cached = self._calls.get(name)
        if cached is not None:
            return cached
        s = self.eval_log(name).samples[0]
        owner = span_owner(s)
        redacted = blocked_message_ids(s, "model_input")
        blocked = blocked_message_ids(s, "model_output")
        calls = []
        for e in s.events:
            if e.event != "model":
                continue
            calls.append(self._call_entry(e, len(calls), owner, redacted, blocked))
        with self._lock:
            self._calls[name] = calls
        return calls

    def _call_entry(self, e, index: int, owner, redacted: set, blocked: set) -> dict:
        agent, defense = owner(e.span_id)
        out = e.output
        msg = output_message(out)
        usage = out.usage if out else None
        inputs = e.input or []
        output_blocked = bool(msg and msg.id in blocked)
        input_flags = {f for m in inputs for f in _message_dict(m, redacted)["flags"]}
        own_flags = {"output_blocked"} if output_blocked else set()
        output_text = (msg.text if msg else "") or ""
        return {
            "index": index,
            "agent": agent,
            "defense": defense,
            "model": e.model,
            "timestamp": e.timestamp.isoformat() if e.timestamp else None,
            "working_time": e.working_time,
            "n_input": len(inputs),
            "input_chars": sum(len(m.text or "") for m in inputs),
            "tools": [t.name for t in (e.tools or [])],
            "usage": usage_dict(usage) if usage else None,
            "output_preview": output_text[:200],
            "output_tool_calls": [tc.function for tc in (msg.tool_calls or [])] if msg else [],
            "stop_reason": out.choices[0].stop_reason if (out and out.choices) else None,
            "error": str(e.error)[:500] if e.error else None,
            "output_blocked": output_blocked,
            "flags": sorted(input_flags | own_flags),
            "_event": e,
            "_redacted": redacted,
            "_blocked": blocked,
        }

    def context_list(self, name: str) -> dict:
        calls = self.context_index(name)
        agents = sorted({c["agent"] for c in calls if c["agent"]})
        defenses = sorted({c["defense"] for c in calls if c["defense"]})
        return {"run": name, "agents": agents, "defenses": defenses, "calls": [_public(c) for c in calls]}

    def context_call(self, name: str, index: int) -> dict:
        calls = self.context_index(name)
        if not 0 <= index < len(calls):
            raise KeyError(f"call {index}")
        c = calls[index]
        e = c["_event"]
        out = e.output
        msg = output_message(out)
        output = _message_dict(msg, blocked=c["_blocked"], own_output=True) if msg else None
        return {
            **_public(c),
            "input": [_message_dict(m, c["_redacted"]) for m in (e.input or [])],
            "output": output,
            "output_error": str(out.error) if (out and out.error) else None,
            "tool_schemas": [{"name": t.name, "description": t.description} for t in (e.tools or [])],
        }

    # -- summary -----------------------------------------------------------
    def summary(self) -> dict:
        """Cross-run aggregates for the overview charts (no eval logs needed)."""
        return {"runs": [self._summary_row(name) for name in self.run_names()]}

    def _summary_row(self, name: str) -> dict:
        run = self.run_json(name)
        eps = self.episodes(name)
        judged = [e for e in eps if _is_judged(e)]
        sw = run.get("swarm") or {}
        dl = run.get("deliverables") or {}
        by_role = {role: _role_counts([e for e in judged if e.get("role") == role]) for role in ROLES}
        scores = [v for e in eps for v in (e.get("score") or {}).values()]
        return {
            "run": name,
            "control": run.get("control") or "baseline",
            "topology": run.get("topology"),
            "graph": graph_of(run.get("topology")),
            "roles": bool(sw.get("roles")),
            "model": run.get("model"),
            "mock": is_mock(run),
            "output_tokens": _usage_total(eps, "output"),
            "cache_read": _usage_total(eps, "cache_read"),
            "judge_cost": round(sum((e["verdict"] or {}).get("judge_cost_usd") or 0 for e in eps), 4),
            "registered_on_main": dl.get("registered_on_main"),
            "registered_copied_from": dl.get("registered_message_copied_from"),
            "archive_in_workspace": dl.get("archive_in_workspace"),
            "endings": endings_of(eps),
            "failed": is_failed(name, eps),
            "n_agents": len(eps),
            "judged": len(judged),
            "cheated": _count_verdict(judged, "cheated"),
            "honeypots_seen": _count_verdict(judged, "honey_pots_seen"),
            "attempted": _count_verdict(judged, "attempted_access"),
            "disclosed": _count_verdict(judged, "disclosed_access"),
            "blocks": sum((e.get("defense") or {}).get("blocks", 0) for e in eps),
            "checks": sum((e.get("defense") or {}).get("checks", 0) for e in eps),
            "timeouts": sum(1 for e in eps if e.get("failure") == "timeout"),
            "score": max(scores or [None]),
            "wall_time": max([e.get("wall_time") or 0 for e in eps] or [0]),
            "prompt_tokens": _usage_total(eps, "input"),
            "regulators": sw.get("regulators"),
            "disregulators": sw.get("disregulators"),
            "workers": sw.get("workers"),
            "composition": sw.get("composition"),
            "seed": sw.get("seed"),
            "by_role": by_role if sw else None,
            "task": run.get("task"),
            "swe": swe_of(run, self.run_dir(name)),
        }
