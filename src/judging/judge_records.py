"""What the judge "labels" reads of a stored run, and where code finds the judge's excerpt in the agent's own text.

    records = run_records(Path("OLD/runs/ian_baseline_explorations_1/flipt_minimal_luna_fixed2"))
    records.board                   # [{"label": "P1", "id", "writer", "readers", "content"}, ...] in board order
    records.prompts["k4q8z"]        # the agent's instructions: its system and user messages
    records.reasoning["k4q8z"]      # [(call number, visible reasoning), ...] in order
    records.actions["k4q8z"]        # [Action(call, "shell: git show origin/rd/x", "fatal: bad object", False), ...]
    locate("copy the gold", "post", own_posts, reasoning)
                                    # {"where": "post", "post": "P3", "call": None, "start": 120, "end": 135} or None

The board is every post of the run in board order, labelled P1, P2, ... (the transcript viewer's Board tab numbers
them the same way). A post's readers are the agents other than its writer with a read event that returned it. A post
by a sender who is no agent of the run is a harness notice.

Visible reasoning is a model call's reasoning_content that a reader can read: not the export's "[redacted reasoning]",
not an encrypted blob, not a list of provider blocks with no text or summary. It is the text the transcript viewer's
readableReasoning shows: a string as stored, a list of blocks as their texts or summaries joined by a blank line. A
call number counts the agent's model calls from 1, every call, so the numbers match the agent's steps in its
transcript. An action is one tool call of a model call, its function and its arguments on one line, under the same
call number as that call's reasoning, with what the call returned and whether Inspect recorded it as an error.

An excerpt is located when, after plain() on both sides (whitespace collapsed, markdown backticks dropped, curly quotes
and apostrophes made straight), it is a substring of one of the agent's own posts or of one call's visible reasoning:
first in the source the judge names, then in the other. The span (start, end) is in characters of that post's content
or that call's visible reasoning as stored, so a viewer can highlight it. Pure: no model call, nothing written.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from src.export.atif_transcripts import visible_reasoning
from src.measures.run_calls import Run, load_run
from src.measures.run_directory import RunDir, episode_agent

PLAIN = str.maketrans({"`": "", "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
                       "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"'})
"""Markdown backticks dropped; curly single and double quotes, primes and apostrophes made straight."""


# --------------------------------------------------------------------------
# visible reasoning
# --------------------------------------------------------------------------


def model_calls(transcript: dict) -> list[dict]:
    """The agent's model calls in order: its trajectory's steps whose source is the agent."""
    return [s for s in transcript["trajectory"]["steps"] if s.get("source") == "agent"]


def reasoning_calls(transcript: dict) -> list[tuple[int, str]]:
    """(call number, visible reasoning) of each of the agent's model calls that shows some, in order."""
    out = []
    for number, step in enumerate(model_calls(transcript), 1):
        text = visible_reasoning(step.get("reasoning_content"))
        if text:
            out.append((number, text))
    return out


# --------------------------------------------------------------------------
# actions
# --------------------------------------------------------------------------
class Action(NamedTuple):
    """One tool call: its model call's number, the call on one line, what it returned, and whether Inspect recorded
    the call as an error."""
    call: int
    text: str
    result: str
    error: bool


def action_text(call: dict) -> str:
    """One tool call on one line: its function, then its one argument's value when that is text, or each argument as
    name=value."""
    args = call.get("arguments")
    if not isinstance(args, dict):
        shown = "" if args is None else str(args)
    elif len(args) == 1 and isinstance(next(iter(args.values())), str):
        shown = next(iter(args.values()))
    else:
        shown = "; ".join(f"{k}={v if isinstance(v, str) else json.dumps(v)}" for k, v in args.items())
    return " ".join(f"{call.get('function_name')}: {shown}".split())


def action_calls(transcript: dict) -> list[Action]:
    """Every tool call of the agent's model calls, in order, numbered as reasoning_calls numbers the calls, each with
    its result (the observation whose source_call_id is the call's id) and its error mark (the result's extra
    is_error)."""
    out = []
    for number, step in enumerate(model_calls(transcript), 1):
        results = {r.get("source_call_id"): r for r in (step.get("observation") or {}).get("results") or []}
        for call in step.get("tool_calls") or []:
            result = results.get(call.get("tool_call_id")) or {}
            out.append(Action(number, action_text(call), str(result.get("content") or ""),
                              bool((result.get("extra") or {}).get("is_error"))))
    return out


# --------------------------------------------------------------------------
# the board
# --------------------------------------------------------------------------
def posts(run: Run) -> list[dict]:
    """The run's posts in board order, each with its label (P1, P2, ...), id, writer and readers (the agents other
    than its writer with a read event that returned it)."""
    readers: dict[str, set[str]] = {}
    for event in run.reads:
        for message_id in event.get("message_ids") or []:
            readers.setdefault(message_id, set()).add(event.get("agent_id"))
    out = []
    for n, message in enumerate(run.messages, 1):
        writer = message.get("sender")
        out.append({"label": f"P{n}", "id": message.get("id"), "writer": writer,
                    "readers": sorted(readers.get(message.get("id"), set()) - {writer, None}),
                    "content": str(message.get("content") or "")})
    return out


def named(agent: str, roles: dict) -> str:
    """The agent with its role, or as a harness notice when it is no agent of the run."""
    if agent not in roles:
        return f"{agent} (harness notice)"
    role = roles[agent]
    return f"{agent} ({role})" if role else agent


# --------------------------------------------------------------------------
# the run's records
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class RunRecords:
    """What the judge reads of a run: its task, its agents' roles, each agent's instructions and episode, the board,
    each agent's visible reasoning and each agent's actions."""
    path: Path
    task: str | None
    roles: dict[str, str | None]
    prompts: dict[str, str]
    episodes: dict[str, str]
    board: list[dict]
    reasoning: dict[str, list[tuple[int, str]]]
    actions: dict[str, list[Action]]


def run_records(run_dir: Path) -> RunRecords:
    """The run's records, read from its stored files only (run_calls.load_run gives the board and the
    instructions as the Collusion measure reads them)."""
    stored = RunDir(run_dir).require()
    run = load_run(stored.path)
    reasoning: dict[str, list[tuple[int, str]]] = {agent: [] for agent in run.agents}
    actions: dict[str, list[Action]] = {agent: [] for agent in run.agents}
    episodes = {}
    for ep in stored.with_transcripts():
        agent = episode_agent(ep)
        episodes[agent] = ep["episode"]
        transcript = stored.transcript(ep["episode"])
        reasoning.setdefault(agent, []).extend(reasoning_calls(transcript))
        actions.setdefault(agent, []).extend(action_calls(transcript))
    return RunRecords(stored.path, stored.run_json.get("task"), run.roles, run.prompts, episodes, posts(run),
                      reasoning, actions)


# --------------------------------------------------------------------------
# locating an excerpt
# --------------------------------------------------------------------------
def plain_map(text: str) -> tuple[str, list[int]]:
    """The text as an excerpt is matched (backticks dropped, quote marks straightened, each run of whitespace one
    space, none at either end) and, for each of its characters, the index of the character of `text` it comes from."""
    out: list[str] = []
    where: list[int] = []
    for i, ch in enumerate(text):
        ch = ch.translate(PLAIN)
        if not ch:
            continue
        if ch.isspace():
            if out and out[-1] != " ":
                out.append(" ")
                where.append(i)
            continue
        out.append(ch)
        where.append(i)
    if out and out[-1] == " ":
        out.pop()
        where.pop()
    return "".join(out), where


def plain(text: str) -> str:
    """The text as an excerpt is matched (plain_map's text)."""
    return plain_map(text)[0]


def span(excerpt: str, text: str) -> tuple[int, int] | None:
    """(start, end) of the excerpt's first occurrence in `text`, both as plain() gives them, in characters of `text`;
    None when it does not occur or is empty."""
    needle = plain(excerpt)
    if not needle:
        return None
    hay, where = plain_map(text)
    at = hay.find(needle)
    if at < 0:
        return None
    return where[at], where[at + len(needle) - 1] + 1


def locate(excerpt: str, source: str, own_posts: list[dict], reasoning: list[tuple[int, str]]) -> dict | None:
    """Where the excerpt is in the agent's own text: {"where": "post" | "reasoning", "post": label or None, "call":
    number or None, "start", "end"}, searched first in the source the judge names (post or reasoning), then in the
    other; None when it is in neither, or empty."""
    def in_posts() -> dict | None:
        for post in own_posts:
            found = span(excerpt, post["content"])
            if found:
                return {"where": "post", "post": post["label"], "call": None, "start": found[0], "end": found[1]}
        return None

    def in_reasoning() -> dict | None:
        for number, text in reasoning:
            found = span(excerpt, text)
            if found:
                return {"where": "reasoning", "post": None, "call": number, "start": found[0], "end": found[1]}
        return None
    first, second = (in_reasoning, in_posts) if source == "reasoning" else (in_posts, in_reasoning)
    return first() or second()
