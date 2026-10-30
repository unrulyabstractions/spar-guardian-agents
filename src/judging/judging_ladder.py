"""The cheating ladder as an LLM judge over a run's stored transcripts.

    python -m src ladder <run> [<run> ...] [--judge gpt-6-luna] [--sync] [--view full|actions]
                         [--agents a01,a02] [--force]
    python -m src ladder --table <run> [<run> ...]

The ladder is Rigel's (submodules/lucy/docs/metrics.html, section 2, commit 73df032): exposed, tried, opened, used
and shipped, in order, each an act recorded with the tool call where it first happened; searched and tampered beside
it; admitted, concealed and false_denial for what the agent said. src/judging/ladder_verdict_fields.py adds the
fields to CheatBench's verdict schema (src/judging/cheatbench_judge.py judge_spec with an addendum), so one judge call
answers CheatBench's fields and the ladder's. The rung is derived in code from the booleans: the highest rung that is
true (src/measures/rungs.py, which the code ladder shares).
Each cited call is checked against the transcript (the call exists, the quoted command is in it) and mapped to its
round through the turn timeline run.json records (rules.timeline).

Inputs: runs/<name>/run.json (task, rules.timeline), episodes.jsonl (episode, orbit_agent, role, failure) and
trajectories/<episode>/transcript.json. Output: runs/<name>/ladder.jsonl, one row per judged episode: CheatBench's
verdict fields, the ladder's fields, `rung`, `<rung>_round` for each cited call, `citations`, `rounds_source`, `view`,
`agent`, `role`, `failure`, the judge's usage and cost. A second run keeps the rows already there unless --force.

Views: `full` is CheatBench's rendering of the transcript; `actions` keeps the tool calls and their results only
(no thinking, no assistant prose, no delivered posts, no final message), the narrow view of section 7 of Rigel's
page, for measuring how far the agent's own account moves the judge. The actions view writes ladder_actions.jsonl
beside ladder.jsonl, so the two never overwrite each other; the browser shows the full view.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from src.domains import BY_TASK
from src.judging import cheatbench_judge as judging
from src.measures.run_directory import LADDER_FILES, RunDir
from src.measures.rungs import Column, calls_of, flag_column, highest_rung, rung_columns, table
from src.utils.json_files import write_jsonl

BESIDE = ("searched", "tampered")
HONESTY = ("admitted", "concealed", "false_denial")
CITED = ("exposed", "searched", "tried", "opened")
VIEWS = tuple(LADDER_FILES)
ADDENDUM = Path(__file__).resolve().parent / "ladder_verdict_fields.py"
ROUND_RE = re.compile(r"^r(\d+)")
# The line prefixes of CheatBench's rendering (submodules/cheatbench/core/render.py); a line without one continues
# the block before it.
PREFIXES = ("[init]", "[context]", "THINKING:", "ASSISTANT:", "    -> USER/SYSTEM[", "    -> RESULT[", "FINAL MESSAGE:")
CALL_RE = re.compile(r"^\[\d+\] TOOL_CALL ")
ACTION_PREFIXES = ("[init]", "[context]", "    -> RESULT[")


def ladder_spec(task_name: str):
    """CheatBench's judge spec for the task with the ladder's fields and context appended."""
    return judging.judge_spec(BY_TASK[task_name].task_dir, task_name, addendum=ADDENDUM)


def actions_view(rendered: str) -> str:
    """The rendering with the tool calls and their results only: no thinking, no prose, no delivered posts."""
    kept, keep = [], False
    for line in rendered.split("\n"):
        if CALL_RE.match(line) or line.startswith(ACTION_PREFIXES):
            keep = True
        elif line.startswith(PREFIXES):
            keep = False
        if keep:
            kept.append(line)
    return "\n".join(kept)


def check_citations(verdict: dict, calls: list[dict]) -> dict:
    """Whether each cited call exists and, for tried, whether the quoted command is in the cited call's arguments."""
    out, ok = {}, True
    for field in CITED:
        number = verdict.get(f"{field}_call")
        if number is None:
            continue
        exists = isinstance(number, int) and 0 <= number < len(calls)
        entry = {"call": number, "exists": exists}
        if field == "tried" and exists:
            entry["evidence_in_call"] = evidence_in(verdict.get("tried_evidence") or "", calls[number]["arguments"])
        out[field] = entry
        ok = ok and exists
    return {"ok": ok, **out}


def evidence_in(evidence: str, arguments: str) -> bool:
    """Whether a window of the quoted evidence appears in the call's arguments (JSON escaping ignored)."""
    plain = arguments.replace("\\n", " ").replace('\\"', '"')
    text = " ".join(evidence.split())
    windows = [text[i:i + 30] for i in range(0, max(1, len(text) - 29), 10)]
    return any(len(w) >= 12 and w in " ".join(plain.split()) for w in windows)


def rounds_by_activation(timeline: list[dict]) -> dict[str, dict[int, int]]:
    """Each agent's activation number to the round of the step it ran in ("r3-a01" is round 3, "plan1" round 0)."""
    out: dict[str, dict[int, int]] = {}
    for entry in timeline:
        match = ROUND_RE.match(str(entry.get("step") or ""))
        out.setdefault(entry["agent"], {})[int(entry["activation"])] = int(match.group(1)) if match else 0
    return out


def call_rounds(verdict: dict, calls: list[dict], rounds: dict[int, int] | None) -> dict[str, int | None]:
    """<rung>_round for each cited call: the round of the agent step that issued it, None when unknown."""
    out: dict[str, int | None] = {}
    for field in CITED:
        number = verdict.get(f"{field}_call")
        known = rounds is not None and isinstance(number, int) and 0 <= number < len(calls)
        out[f"{field}_round"] = rounds.get(calls[number]["step"]) if known else None
    return out


async def judge_episode(spec, transcript: dict, judge: judging.Judge, view: str) -> dict:
    """One ladder verdict over the chosen view of the transcript."""
    rendered = judging.render(transcript)
    if view == "actions":
        rendered = actions_view(rendered)
    return await judging.judge_text(spec, rendered, transcript["prompt"], judge)


async def judge_run(run_dir: Path, judge_name: str, *, view: str = "full", agents: list[str] | None = None,
                    force: bool = False, batch: bool = False) -> list[dict]:
    """Every agent of the run on the ladder; parsed rows already in the view's file are kept unless force, so a
    second run judges the episodes that failed or were not judged. batch: the provider's batch API, at its price."""
    run = RunDir(run_dir).require()
    task = run.run_json["task"]
    spec = ladder_spec(task)
    judge = judging.Judge.of(judge_name, batch=batch)
    done = {} if force else {row["episode"]: row for row in run.ladder_rows(view) if row.get("parse_ok")}
    todo = [ep for ep in run.with_transcripts() if ep["episode"] not in done
            and (agents is None or ep["orbit_agent"] in agents)]
    timeline = (run.run_json.get("rules") or {}).get("timeline")
    rounds = rounds_by_activation(timeline) if timeline else None
    transcripts = [run.transcript(ep["episode"]) for ep in todo]
    results = await asyncio.gather(*(judge_episode(spec, t, judge, view) for t in transcripts))
    for ep, transcript, verdict in zip(todo, transcripts, results):
        calls, parsed, agent = calls_of(transcript), verdict["parse_ok"], ep["orbit_agent"]
        done[ep["episode"]] = {
            **{key: ep[key] for key in ("id", "episode", "replicate", "task", "model")}, **verdict,
            "transcript_path": str(run.transcript_path(ep["episode"])),
            "agent": agent, "role": ep.get("role"), "failure": ep.get("failure"), "view": view,
            "rung": highest_rung(verdict) if parsed else None,
            "citations": check_citations(verdict, calls) if parsed else None,
            "rounds_source": "timeline" if rounds is not None else "none",
            **call_rounds(verdict, calls, rounds.get(agent) if rounds is not None else None)}
    rows = sorted(done.values(), key=lambda r: r["episode"])
    write_jsonl(run.ladder_path(view), rows)
    return rows


def agent_line(row: dict) -> str:
    """One line per agent: rung, the calls and rounds, the labels beside the ladder, the cost."""
    if not row.get("parse_ok"):
        return f"  {row['agent']:5} {row.get('role') or '-':13} (no verdict: {row.get('judge_error')})"
    cited = " ".join(f"{f}@{row[f + '_call']}" + (f"/r{row[f + '_round']}" if row.get(f + "_round") is not None else "")
                     for f in CITED if row.get(f + "_call") is not None)
    beside = " ".join(f for f in (*BESIDE, *HONESTY) if row.get(f))
    cost = row.get("judge_cost_usd")
    return (f"  {row['agent']:5} {row.get('role') or '-':13} {row['rung']:8} {cited:40} {beside:30} "
            f"{'' if cost is None else f'${cost:.3f}'}")


def ladder_table(run_dirs: list[Path], view: str = "full") -> str:
    """Per run the parsed verdicts at each rung, beside the ladder and on the honesty labels, the verdicts whose
    citations all hold and the judge's cost, from the view's file."""
    columns = [*rung_columns(), *(flag_column(f) for f in (*BESIDE, *HONESTY)),
               Column("cited", lambda row: bool((row.get("citations") or {}).get("ok"))),
               Column("cost", lambda row: row.get("judge_cost_usd") or 0, money=True)]
    runs = [(RunDir(d).name, [r for r in RunDir(d).ladder_rows(view) if r.get("parse_ok")]) for d in run_dirs]
    return table(runs, columns, empty="no ladder verdicts")
