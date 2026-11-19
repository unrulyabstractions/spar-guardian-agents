"""The run page's Board tab: two agents' calls over time, the board between them and every post of the run, for the
transcript viewer (src/ui/transcript_viewer.py, route /api/board).

    payload = board_payload(run_dir, levels, judges, boards)
    payload["agents"][0]["calls"][0]        # {k, id, m, t, dur, turn, tool, summary, board, wrote, read, labels}
    payload["posts"][0]     # {label, id, post_n, writer, role, kind, t, call, text, reads, collusion, names_exploit}
    payload["pair"]                         # the two agents the tab opens with, and payload["pair_why"]

The calls are collusion_takes.load_run's, numbered as the transcript scan numbers them (the agent tab's "Call k").
The clock is the eval log's tool events where the run's log holds every call (load_run's times, the source the
results browser's timeline reads): t and dur in seconds from the first call's start. Without them the clock is call
order: t is the call's place in Run.ordered (by turn, then agent and number) and every call lasts 1. The posts are
numbered P1, P2, ... in board order as the collusion judge numbers them (collusion_judge.posts), system notices
included; post_n is the viewer's number of an agent's post (transcript_viewer.board_view), which the evidence cites.
A post is placed at the start of the call that wrote it; a system notice, which no call wrote, at the end of the call
of the board's last event before it. Each read is a board_read call of another agent whose result held the post.

The labels of a call are the ones the run page draws (`levels`, transcript_levels.compute's answer): this module
places them and computes no level. A post carries a collusion mark from each collusion judge (`judges`, the verdict
files as src/judging/verdict_files.py read gives them) whose verdict names it as the post that shows collusion, with
the quote, the reason and whether the code's check let the verdict stand. names_exploit is the kinds of planted
material the honeypot scan finds in the post (the Collusion measure's naming_posts in `levels`), evidence and no rung.

The default pair: the pair with the highest Collusion rung above none (src/measures/collusion.py's pairs in
`levels`), workers before hidden roles, then the pair with more posts; else the two workers with the most posts.
"""
from __future__ import annotations

from pathlib import Path

from src.judging import collusion_judge, verdict_files
from src.measures import collusion
from src.measures.collusion_takes import Call, Run, load_run
from src.measures.run_directory import RunDir, is_worker

SUMMARY_CHARS = 140
"""The longest one-line summary of a call."""
COLLUSION_PASS = "collusion"
"""The verdict files' pass of the collusion judge (verdict_files.PASSES)."""
BOARD_TOOLS = {"board_post": "post", "board_read": "read", "board_status": "status"}
"""The board's tools and the kind of board call each makes."""
LADDER_KEYS = ("name", "levels", "line", "line_words")
"""What the tab reads of each ladder: what the page's levelChip and lineMark (tv_levels.js) draw a level from."""
CLOCKS = {
    "eval_log": "seconds from the first call's start, from the eval log's tool events",
    "call_order": "the run's call order (by turn, then agent and number): the run's eval log holds no times",
}


def step_numbers(run_dir: Path) -> dict[str, int]:
    """tool call id -> the number of the model call that made it, per agent from 0: the agent steps with a tool call,
    in transcript order."""
    stored = RunDir(run_dir)
    out: dict[str, int] = {}
    for ep in stored.with_transcripts():
        m = -1
        for step in stored.transcript(ep["episode"])["trajectory"]["steps"]:
            if step.get("source") == "agent" and step.get("tool_calls"):
                m += 1
                out.update((tc.get("tool_call_id"), m) for tc in step["tool_calls"])
    return out


def clock(run: Run) -> tuple[str, dict[tuple[str, int], tuple[float, float]], float]:
    """(the clock's name, (agent, k) -> (t, dur), the run's total) on CLOCKS' terms."""
    calls = [c for rows in run.calls.values() for c in rows]
    if calls and all(c.start is not None for c in calls):
        t0 = min(c.start for c in calls)
        at = {(c.agent, c.k): (c.start - t0, max(0.0, (c.end if c.end is not None else c.start) - c.start))
              for c in calls}
        return "eval_log", at, max((t + d for t, d in at.values()), default=0.0)
    at = {(c.agent, c.k): (float(i), 1.0) for i, c in enumerate(run.ordered())}
    return "call_order", at, float(len(at))


def one_line(text: str) -> str:
    """The first non-empty line of a text, at most SUMMARY_CHARS long."""
    line = next((x.strip() for x in str(text or "").splitlines() if x.strip()), "")
    return line if len(line) <= SUMMARY_CHARS else line[:SUMMARY_CHARS - 1] + "…"


def summary(c: Call, wrote: list[str], read: list[str]) -> str:
    """A call in one line: its command or code, the posts it wrote or read, else its tool's arguments."""
    if c.command:
        return one_line(c.command)
    if c.tool == "board_post":
        return f"posted {', '.join(wrote) or 'nothing'}: {one_line(c.arguments.get('message') or '')}"
    if c.tool == "board_read":
        return f"read {', '.join(read)}" if read else "read no new post"
    return one_line(" ".join(f"{k}={v}" for k, v in c.arguments.items())) or c.tool


def call_labels(levels: dict, agent: str, ids: dict[str, int]) -> dict[int, list[dict]]:
    """call number -> the labels the run page puts on it (levels["labels"], by k or by tool call id)."""
    out: dict[int, list[dict]] = {}
    for label in (levels.get("labels") or {}).get(agent) or []:
        k = label["k"] if label.get("k") is not None else ids.get(label.get("call_id"))
        if k is not None:
            out.setdefault(k, []).append({key: label[key] for key in ("ladder", "level", "index", "evidence")})
    return out


def collusion_marks(judges: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """post label -> each collusion judge's mark on it: a verdict that names it as the post showing collusion.
    `judges` is {judge: rows} of the pass COLLUSION_PASS; the applied judge (verdict_files.applied) is the one the
    ladder takes."""
    applied = verdict_files.applied(judges)
    out: dict[str, list[dict]] = {}
    for judge, rows in sorted(judges.items()):
        for row in rows:
            if row.get("parse_ok") and row.get("collusion") is True and (row.get("collusion_post") or "").strip():
                out.setdefault(row["collusion_post"].strip(), []).append({
                    "judge": judge, "applied": judge == applied, "pair": row.get("agents") or [],
                    "quote": row.get("collusion_quote") or "", "reason": row.get("collusion_reason") or "",
                    "stands": row.get("collusion_verdict") is True,
                    "check": (row.get("collusion_check") or {}).get("why")})
    return out


def default_pair(levels: dict, roles: dict[str, str | None], posts_by: dict[str, int]) -> tuple[list[str], str]:
    """The two agents the tab opens with and why (the module's rule)."""
    measure = levels.get("collusion") or {}
    pairs = [p for p in measure.get("pairs") or [] if p["rung"] in collusion.RUNGS[1:]]
    if pairs:
        top = max(pairs, key=lambda p: (collusion.RUNGS.index(p["rung"]), all(is_worker(r) for r in p["roles"]),
                                        sum(posts_by.get(a, 0) for a in p["agents"])))
        return list(top["agents"]), f"the pair with the highest Collusion rung, {top['rung']}"
    agents = list(roles)
    ranked = sorted(agents, key=lambda a: (not is_worker(roles[a]), -posts_by.get(a, 0), agents.index(a)))
    return ranked[:2], "no pair above none on the Collusion ladder: the two workers with the most posts"


def notice_time(message: dict, events: list[dict], by_id: dict[str, Call],
                at: dict[tuple[str, int], tuple[float, float]]) -> float:
    """When a system notice, which no call wrote, reached the board: the end of the call of the last board event before
    it (the board numbers its messages and events on one count), or 0 before any."""
    before = [by_id[e["tool_call_id"]] for e in events if (e.get("sequence") or 0) < (message.get("sequence") or 0)
              and e.get("tool_call_id") in by_id]
    return sum(at[(before[-1].agent, before[-1].k)]) if before else 0.0


def board_payload(run_dir: Path, levels: dict, judges: dict, boards: list[dict]) -> dict:
    """The Board tab's data for one run: {clock, clock_about, total, agents, posts, pair, pair_why, ladders, order}.
    `levels` is the run page's ladders (transcript_levels.compute), `judges` the run's verdict files
    (verdict_files.read) and `boards` the viewer's board (transcript_viewer.board_view)."""
    run = load_run(run_dir, times=True)
    name, at, total = clock(run)
    steps = step_numbers(run_dir)
    labelled = {m["id"]: f"P{i}" for i, m in enumerate(run.messages, 1)}
    events = sorted((e for board in run.boards for e in board.get("events") or []),
                    key=lambda e: e.get("sequence") or 0)
    wrote: dict[str, list[str]] = {}
    read: dict[str, list[str]] = {}
    for event in events:
        into = {"sent": wrote, "read": read}.get(event.get("kind"))
        if into is not None:
            into.setdefault(event.get("tool_call_id"), []).extend(
                labelled[mid] for mid in event.get("message_ids") or [] if mid in labelled)
    agents = []
    for agent, rows in run.calls.items():
        labels = call_labels(levels, agent, {c.id: c.k for c in rows})
        agents.append({"agent": agent, "role": run.roles.get(agent) or "worker", "calls": [{
            "k": c.k, "id": c.id, "m": steps.get(c.id), "t": at[(agent, c.k)][0], "dur": at[(agent, c.k)][1],
            "turn": c.turn, "tool": c.tool, "summary": summary(c, wrote.get(c.id, []), read.get(c.id, [])),
            "board": BOARD_TOOLS.get(c.tool), "wrote": wrote.get(c.id, []), "read": read.get(c.id, []),
            "labels": labels.get(c.k, [])} for c in rows]})
    agents.sort(key=lambda a: (a["role"] != "worker", a["agent"]))
    post_n = {m["id"]: m["post_n"] for b in boards for m in b.get("messages") or [] if m.get("post_n") is not None}
    by_id = {c.id: c for rows in run.calls.values() for c in rows}
    sent_by = {mid: by_id.get(e.get("tool_call_id")) for e in events if e.get("kind") == "sent"
               for mid in e.get("message_ids") or []}
    marks = collusion_marks(judges.get(COLLUSION_PASS) or {})
    naming = {p["id"]: p["kinds"] for p in (levels.get("collusion") or {}).get("naming_posts") or []}
    ordered = sorted(by_id.values(), key=lambda c: at[(c.agent, c.k)][0])
    posts = []
    for post, message in zip(collusion_judge.posts(run), run.messages):
        writer_call = sent_by.get(post["id"])
        if writer_call is not None:
            t = at[(writer_call.agent, writer_call.k)][0]
        else:
            t = notice_time(message, events, by_id, at)
        reads = [{"agent": c.agent, "k": c.k, "t": at[(c.agent, c.k)][0]} for c in ordered
                 if post["label"] in read.get(c.id, []) and c.agent != post["writer"]]
        posts.append({"label": post["label"], "id": post["id"], "post_n": post_n.get(post["id"]),
                      "writer": post["writer"], "role": run.roles.get(post["writer"]),
                      "kind": "post" if post["writer"] in run.roles else "system", "t": t,
                      "call": writer_call.k if writer_call else None, "text": post["content"], "reads": reads,
                      "collusion": marks.get(post["label"], []), "names_exploit": naming.get(post["id"], [])})
    posts.sort(key=lambda p: p["t"])
    posts_by = {a: sum(p["writer"] == a for p in posts) for a in run.roles}
    pair, why = default_pair(levels, run.roles, posts_by)
    return {"run": run_dir.name, "clock": name, "clock_about": CLOCKS[name], "total": total, "agents": agents,
            "posts": posts, "pair": pair, "pair_why": why,
            "ladders": {k: {key: v[key] for key in LADDER_KEYS} for k, v in levels["ladders"].items()},
            "order": levels["order"], "notes": run.notes}

