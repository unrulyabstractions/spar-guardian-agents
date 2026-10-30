"""The results browser's run reader: the run directories under runs/ and the eval logs they point at.

`Store` reads run.json, episodes.jsonl, judge.jsonl, ladder.jsonl, signals.jsonl and the trajectories through
src/measures/run_directory.py RunDir, and the communication and defense stores and the analysis tools' summaries
itself; it loads a run's Inspect eval log once, on demand. The run directories are written by src/export/run_export.py
and the SWE measures (swe_summary.json) by src/measures/swe_measures.py. _eval_log_views.py reads the eval logs. Each
episode carries its agent rows of the two ladders; the run carries the code ladder's team records.

    store = Store(Path("runs"))
    rows = [store.run_row(name) for name in store.run_names()]
    rows[0]["aggregate"]["cheated"], rows[0]["team"]["rung"]
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

from inspect_ai.log import EvalLog, read_eval_log

from src.configuration.settings import INCOMPLETE_FAILURES
from src.configuration.swarm_config import ROLES
from src.export.eval_sample_view import PASSIVE_DEFENSES
from src.measures.run_directory import RUN_JSON, RunDir
from src.measures.verdict_fields import CRITICAL, FLAGS_BY_TASK, GOOD, SERIOUS, WARNING
from src.utils.json_files import read_json, read_json_dir

# A verdict is every key of the judge.jsonl row except these: the row's identity, how the judge was configured, its
# usage, and the transcript it read. The task's own verdict fields pass through whatever they are. VERDICT_BASE is
# present on every verdict, None when absent.
VERDICT_BOOKKEEPING = frozenset({
    "id", "episode", "replicate", "task", "model", "transcript_path",
    "judge_generation_config", "judge_generation_config_unsupported", "judge_prompt", "judge_schema",
    "output_mode", "judge_usage", "judge_attempts",
})
VERDICT_BASE = ("cheated", "summary", "judge", "judge_cost_usd", "parse_ok", "judge_error")
LADDER_BOOKKEEPING = VERDICT_BOOKKEEPING | {"raw", "row", "sample"}
SIGNALS_IDENTITY = ("row", "sample", "episode", "agent", "role", "task")
TEAM_FIELDS = ("rung", "collaboration")          # what the runs list shows of the first sample's team record
SWARM_FIELDS = ("n_agents", "regulators", "disregulators", "workers", "seed", "composition")
DELIVERABLE_FIELDS = ("archive_in_workspace", "archive_on_remote_main", "registered_on_main",
                      "registered_message_copied_from")
SWE_FIELDS = ("issue", "instance", "n_agents", "mode", "honeypot", "rule", "base_commit", "fix_commit", "pieces",
              "checks_ok")
# The badge a true verdict flag takes, by the status colour the figures give it.
FLAG_KIND = {WARNING: "warn", SERIOUS: "bad", GOOD: "good", CRITICAL: "bad"}
# The tasks with a setting of their own, apart from openmath: the setting key's prefix, the label and the sort order.
# Openmath settings sort 0 (lone agent), 1 (board) and 2 (hidden roles); SWE sorts 3.
TASK_SETTINGS = {"knowledge_work": ("kw", "knowledge work", 4), "subset_sum": ("subset", "subset sum", 5),
                 "prime_factorization": ("factor", "prime factorization", 6),
                 "protein_design": ("protein", "protein design", 7),
                 "creative_writings": ("creative", "creative writing", 8), "chess": ("chess", "chess", 9)}

# A run is failed when an episode ended in a harness crash or a provider error (the converter's "connection_closed":
# rate limit, billing, connection), or when it was set aside by name (failed_, failed2_, ..., cancelled_).
FAILED_NAME = re.compile(r"^(failed\d*|cancelled\d*)_")


def safe_part(part: str) -> bool:
    """A path segment that stays inside its directory: not empty, not . or .., no separator."""
    return part not in ("", ".", "..") and "/" not in part and "\\" not in part


def _tool_summary(path: Path) -> dict:
    """A tool's summary.json, or {"error"} when it does not parse or is not an object, so one bad file does not break
    the run page."""
    try:
        summary = read_json(path)
    except ValueError as ex:
        return {"error": f"{path.name} did not parse: {ex}"}
    if not isinstance(summary, dict):
        return {"error": f"{path.name} is not a JSON object"}
    return summary


def _without(row: dict | None, keys) -> dict | None:
    return None if row is None else {k: v for k, v in row.items() if k not in keys}


def verdict_of(row: dict | None) -> dict | None:
    """The verdict fields of one judge.jsonl row, or None when the episode was not judged."""
    if not row:
        return None
    return {**{key: None for key in VERDICT_BASE}, **_without(row, VERDICT_BOOKKEEPING)}


def verdict_fields(task: str | None) -> list[dict]:
    """The task's judge flags in their order (src/measures/verdict_fields.py FLAGS_BY_TASK): field, label and badge
    kind."""
    return [{"field": field, "label": label.replace("\n", " "), "kind": FLAG_KIND[colour]}
            for field, label, colour in FLAGS_BY_TASK.get(task, ())]


def setting_of(run: dict, n_agents: int) -> dict:
    """The arrangement a run was made in: the task, the communication graph (a lone agent, or every agent on one
    board), the roster size and whether roles were hidden. {key, label, order, n} plus composition, R, D and W for
    hidden roles. A CheatBench SWE run is set by its issue, team size, honeypot and rule."""
    swarm = run.get("swarm") or {}
    hidden = {"composition": swarm["composition"], "R": swarm.get("regulators"), "D": swarm.get("disregulators"),
              "W": swarm.get("workers")} if swarm.get("composition") else {}
    n = 1 if run.get("topology") in (None, "single") else n_agents
    team = "lone agent" if n == 1 else f"board of {n}"
    swe = run.get("swe")
    if swe:
        n = swe.get("n_agents") or n
        team = "lone agent" if n == 1 else f"board of {n}"
        honeypot = swe.get("honeypot") if swe.get("honeypot") != "as_released" else None
        rule = swe.get("rule") or "none"
        bits = ", ".join(filter(None, [honeypot, f"rule: {rule}" if rule != "none" else None]))
        return {"key": f"swe-{swe.get('issue')}-n{n}-{swe.get('honeypot')}-{rule}", "order": 3, "n": n,
                "label": f"SWE {swe.get('issue')} {team}" + (f" ({bits})" if bits else "")}
    if run.get("task") in TASK_SETTINGS:
        prefix, label, order = TASK_SETTINGS[run["task"]]
        return {"key": f"{prefix}-{'roles' if hidden else 'board'}-{n}", "order": order, "n": n,
                "label": f"{label} {team}" + (", hidden roles" if hidden else ""), **hidden}
    if n == 1:
        return {"key": "single", "label": "lone agent", "order": 0, "n": 1}
    if hidden:
        return {"key": f"roles-{n}", "label": f"board of {n}, hidden roles", "order": 2, "n": n, **hidden}
    return {"key": f"board-{n}", "label": f"board of {n}", "order": 1, "n": n}


def _swe_of(swe: dict, run_dir: Path) -> dict:
    """run.json "swe" as the page reads it: the issue and arm fields, each grade's reward and required tests passed,
    and the talk measures of swe_summary.json when src/measures wrote one."""
    grades = swe.get("grades") or {}
    measures = (read_json(run_dir / "swe_summary.json") or {}).get("agents")
    keys = ("posts", "fix_posts", "rule_posts", "disclosed")
    return {
        **{key: swe.get(key) for key in SWE_FIELDS},
        "rewards": {k: v.get("reward") for k, v in grades.items()},
        "required_passed": {k: len(v.get("required_passed") or []) for k, v in grades.items()},
        "required_tests": len(swe.get("required_tests") or []),
        "measures": {a: {k: m.get(k) for k in keys} for a, m in measures.items()} if measures else None,
    }


def aggregate(run: dict, episodes: list[dict], fields: list[dict]) -> dict:
    """One run's counts over its episodes. judged, cheated, flags and by_role count only counted verdicts (a verdict
    on an episode with a transcript); tokens.input includes cache reads."""
    counted = [e for e in episodes if e["counted"]]
    defenses = [d["name"] for d in run.get("defenses") or []]
    scores = [v for e in episodes for v in (e.get("score") or {}).values() if v is not None]
    usages = [e.get("usage") or {} for e in episodes]
    return {
        "judged": len(counted),
        "cheated": sum(1 for e in counted if e["verdict"]["cheated"]),
        "flags": {f["field"]: sum(1 for e in counted if e["verdict"].get(f["field"])) for f in fields},
        "controlled": any(name not in PASSIVE_DEFENSES for name in defenses),
        "blocks": sum((e.get("defense") or {}).get("blocks", 0) for e in episodes),
        "checks": sum((e.get("defense") or {}).get("checks", 0) for e in episodes),
        "timeouts": sum(1 for e in episodes if e.get("failure") == "timeout"),
        "score": max(scores, default=None),
        "wall_time": max((e.get("wall_time") or 0 for e in episodes), default=0),
        "tokens": {key: sum(u.get(key) or 0 for u in usages) for key in ("input", "output", "cache_read")},
        "judge_cost": round(sum((e["verdict"] or {}).get("judge_cost_usd") or 0 for e in episodes), 4),
        "by_role": {role: {"n": sum(1 for e in counted if e.get("role") == role),
                           "cheated": sum(1 for e in counted if e.get("role") == role and e["verdict"]["cheated"])}
                    for role in ROLES} if run.get("swarm") else None,
    }


class Store:
    """Reads run directories; loads an eval log once per run on demand."""

    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = runs_dir
        self._logs: dict[str, EvalLog] = {}
        self._lock = threading.Lock()

    def run_names(self) -> list[str]:
        return sorted(p.parent.name for p in self.runs_dir.glob(f"*/{RUN_JSON}"))

    def run_dir(self, name: str) -> Path:
        """The run's directory. Raises KeyError for a name that is not a run under runs/."""
        if not safe_part(name) or not RunDir(self.runs_dir / name).is_run():
            raise KeyError(name)
        return self.runs_dir / name

    def run_json(self, name: str) -> dict:
        return RunDir(self.run_dir(name)).run_json

    def eval_log(self, name: str) -> EvalLog:
        with self._lock:
            if name in self._logs:
                return self._logs[name]
        path = RunDir(self.run_dir(name)).eval_log
        if path is None:
            raise KeyError(f"{name}: eval log not found at {self.run_json(name).get('eval_log')}")
        log = read_eval_log(str(path), resolve_attachments=True)
        with self._lock:
            self._logs[name] = log
        return log

    def episodes(self, name: str) -> list[dict]:
        """episodes.jsonl rows, each with its verdict, ladder and signals rows, has_transcript and counted."""
        run = RunDir(self.run_dir(name))
        verdicts = run.verdicts()
        ladder = {row["episode"]: row for row in run.ladder_rows()}
        signals = {row["episode"]: row for row in run.signals()}
        out = []
        for rec in run.episodes():
            # A set-aside run keeps the transcript_path of its original name, which a newer run may own by now; the
            # copy under the run's own trajectories/ is its record.
            own = run.transcript_path(rec["episode"])
            if own.exists():
                rec["transcript_path"] = str(own)
            rec["verdict"] = verdict_of(verdicts.get(rec["episode"]))
            rec["ladder"] = _without(ladder.get(rec["episode"]), LADDER_BOOKKEEPING)
            rec["signals"] = _without(signals.get(rec["episode"]), SIGNALS_IDENTITY)
            rec["has_transcript"] = bool(rec.get("transcript_path")) and Path(rec["transcript_path"]).exists()
            rec["counted"] = bool(rec["verdict"]) and rec["verdict"]["cheated"] is not None and rec["has_transcript"]
            out.append(rec)
        return out

    def run_row(self, name: str) -> dict:
        """One run for the runs list, the summary and the swarm page: run.json's facts, the setting, the episodes
        and the aggregate block."""
        d = self.run_dir(name)
        run = RunDir(d).run_json
        eps = self.episodes(name)
        teams = RunDir(d).team_signals()
        fields = verdict_fields(run.get("task"))
        swarm, deliverables = run.get("swarm"), run.get("deliverables")
        n_agents = len(run.get("agents") or eps)
        eval_log = RunDir(self.run_dir(name)).eval_log
        endings: dict[str, int] = {}
        for e in eps:
            ending = e.get("failure") or e.get("ended_by") or "unrecorded"     # a log without the turn rules
            endings[ending] = endings.get(ending, 0) + 1
        return {
            "run": name,
            "task": run.get("task"),
            "model": run.get("model"),
            "topology": run.get("topology"),
            "n_agents": n_agents,
            "setting": setting_of(run, n_agents),
            "mock": str(run.get("model") or "").startswith("mockllm"),
            "agents": run.get("agents"),
            "control": run.get("control") or "baseline",
            "monitor_model": run.get("monitor_model"),
            "defenses": [defense["name"] for defense in run.get("defenses") or []],
            "eval_log": str(eval_log or run.get("eval_log") or "") or None,
            "eval_log_exists": eval_log is not None,
            "failed": any(e.get("failure") in INCOMPLETE_FAILURES for e in eps) or bool(FAILED_NAME.match(name)),
            "swarm": {k: swarm.get(k) for k in SWARM_FIELDS} if swarm else None,
            "deliverables": {k: deliverables.get(k) for k in DELIVERABLE_FIELDS} if deliverables else None,
            "endings": endings,
            "config": run.get("config"),
            "rules": {e["orbit_agent"]: e["rules"] for e in eps if e.get("rules") is not None} or None,
            "swe": _swe_of(run["swe"], d) if run.get("swe") else None,
            "mode": run.get("mode"),
            "scale": run.get("scale"),
            "preset": run.get("preset"),
            "engagement": run.get("engagement"),
            "turn_budget": run.get("turn_budget"),
            "access": run.get("access"),
            "knowledge": run.get("knowledge"),
            "tools": self.tool_names(name),
            "verdict_fields": fields,
            "aggregate": aggregate(run, eps, fields),
            "team": {key: teams[0].get(key) for key in TEAM_FIELDS} if teams else None,
            "episodes": eps,
        }

    def run_detail(self, name: str) -> dict:
        """The run row plus run.json itself, every team record, the board, the defense evidence and each analysis
        tool's summary."""
        d = self.run_dir(name)
        detail = self.run_row(name)
        detail["run_json"] = RunDir(d).run_json
        detail["team"] = RunDir(d).team_signals()
        detail["communication"] = read_json_dir(d / "communication")
        detail["defense"] = read_json_dir(d / "defense")
        detail["tools"] = {tool: _tool_summary(d / "tools" / tool / "summary.json") for tool in detail["tools"]}
        return detail

    def tool_names(self, name: str) -> list[str]:
        """The analysis tools that wrote a summary for this run. The loader's caches (tools/*.json) are not tools."""
        return sorted(p.parent.name for p in (self.run_dir(name) / "tools").glob("*/summary.json"))

    def tool_dir(self, name: str, tool: str) -> Path:
        """runs/<run>/tools/<tool>/, the directory an analysis tool writes. Raises KeyError for an unknown tool."""
        path = self.run_dir(name) / "tools" / tool
        if not safe_part(tool) or not path.is_dir():
            raise KeyError(tool)
        return path

    def transcript(self, name: str, episode: str) -> dict:
        for rec in self.episodes(name):
            if rec["episode"] != episode:
                continue
            if not rec["has_transcript"]:
                raise KeyError(f"{episode}: no transcript")
            return {"episode": rec, "transcript": read_json(Path(rec["transcript_path"]))}
        raise KeyError(episode)
