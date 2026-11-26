"""The run page's Board tab: two agents' calls over time, the board between them and every post of the run, for the
transcript viewer (src/ui/transcript_viewer.py, route /api/board).

    payload = board_payload(run_dir, levels)
    payload["agents"][0]["calls"][0]        # {k, m, t, dur, tool, summary, board, labels, marks, held, after}, and
                                            # on a submit or unsubmit call hand_in: its event's index in
                                            # payload["hand_ins"]["events"], or None for a call that made no event
    payload["agents"][0]["calls"][0]["marks"]["cheating"]   # [{level, index, clause, evidence}]: one lens's marks
    payload["agents"][0]["final"]           # {ladder: {level, facts}}: the agent's level at the end of the run
    payload["agents"][0]["unplaced"]        # {lens: {clause, rule or label: records that name no call}}
    payload["posts"][0]                     # {label, writer, role, kind, t, call, text, reads, labels}, and
                                            # "record": True on a record (label None)
    payload["posts"][0]["reads"][0]         # {agent, k, t, form ("latest" | "from" | None), asked}
    payload["pair"]                         # the two agents the tab opens with, and payload["pair_why"]
    payload["notes"]                        # load_run's notes, then the count of reads of no recorded form
    payload["presence"]                     # each agent's entry and exit and each alert, placed on the clock
                                            # (src/ui/transcript_presence.py on_lanes)
    payload["hand_ins"]                     # the team's hand-ins and take-backs (transcript_presence.hand_ins), each
                                            # hand-in with what it held (transcript_monitoring.with_held)
    payload["versions"]["checks"][0]        # a check_fix call with what its code held and its call k and time t
                                            # (transcript_monitoring.versions); payload["versions"] None without

    network_payload(run_dir)                # the network log on the board's clock (src/ui/transcript_network.py),
                                            # None without network.jsonl; the viewer's /api/network

The calls are run_calls.load_run's, numbered as the transcript scan numbers them (the agent tab's "Call k").
The clock is the eval log's tool events where the run's log holds every call (load_run's times): t
and dur in seconds from the first call's start. Without them the clock is call
order: t is the call's place in Run.ordered (by turn, then agent and number) and every call lasts 1. The posts are
numbered P1, P2, ... in board order as the agents and the judges number them (src/judging/judge_records.py posts),
system notices included. A record of who came and went ("a01 entered.") stands in its place among them with no label,
no reads and no judge label, and alone carries "record": True; it is no post. A post is placed at the start of the
call that wrote it; a system notice or a record, which no call wrote, at the end of the call of the board's last event
before it. Each read is a board_read call of another agent whose result held the
post, with its form from the call's own arguments (src/swarm/board.py board_read, read_form): "from" when the call
named the post's writer in `from_agent`, with `asked` that agent; "latest" when it asked for the newest posts (`latest`,
or no argument) or named another writer; None when its arguments are none of the tool's, and the payload's notes count
such reads.

The labels of a call are the ones the run page draws (`levels`, transcript_levels.compute's answer): this module
places them and computes no level. An agent's "final" is its cell of each ladder on the run page (levels["agents"]):
the level at the end of the run, which holds the levels whose evidence cites no call too, and the evidence as facts.
A post carries each judge label whose excerpt code located in it (the measure's judge labels in `levels`,
src/measures/judge_labels.py labels): the agent, the label, its score, the excerpt, the reason and the excerpt's span.

A call's marks are what the tab's lenses (LENSES) colour it by: every record of `levels` that names the call, placed
on it, with no level computed here.
- cheating: each evidence line of each Cheating clause (the measure's clauses in levels["cheating"]) at every call it
  cites (cited_calls), or, for a read the access log shows (access_evidence.read_evidence), at the call the log's row
  sits in; the clause's rung (cheating_ladder.CLAUSES) is the mark's level. Where the measure read the agent after
  every call (its timeline of source "steps"), each clause also at the call at which it first held, and its lines that
  name no call are placed with it.
- coordination: each call that read a teammate's post it had not read before (the post's first read by that agent,
  from the posts' reads) as read, and each call that posted as write, with the teammates whose new posts that post
  took up (took_up: the step in the measure's R_ij at that post); and each change of the level the agent holds (the
  run page's held events, src/measures/coordination.py held_levels) at the call from which it holds it, held. A call's
  "held" is the index of the level the agent holds at it (held_at), which can fall from high or extreme. A call's
  "after" is, for Cheating and Collusion where the measure read them after every call, the index of the rung the agent
  holds after it (after_at); {} otherwise.
- collusion: each take of the agent (the measure's pairs) at its call, or at the calls its evidence cites, with whose
  work it took, its rule and whether it is a take of a planted piece (PIECE_RULE); where the measure read the agent
  after every call, each rung it rose to at the call at which it holds it, with the teammate (rung_marks).
- judge: each judge label located in the agent's reasoning, at the tool calls of that model call (the judges number
  the model calls from 1, judge_records.model_calls), or in its post, at the call that wrote the post.
A record that names no call is counted in the agent's "unplaced" by clause, rule or label.

The default pair starts from the worker highest on the Cheating ladder (then more posts): beside it the influencer
with the most posts, else the next worker highest on Cheating. With no worker above none: the pair with the highest
Collusion rung above none (src/measures/collusion.py's pairs in `levels`), workers before influencers, then the pair
with more posts; else the two workers with the most posts.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from src.configuration.seed_packs import is_seeded
from src.judging.judge_records import model_calls as judged_model_calls
from src.judging.judge_records import posts as labelled_posts
from src.measures import cheating_ladder, collusion, coordination, judge_labels
from src.measures.access_evidence import read_evidence
from src.measures.run_calls import Call, Run, load_run
from src.measures.run_directory import RunDir, episode_agent, is_worker
from src.ui.transcript_levels import CITE_RE, own_call, step_text, stepped
from src.ui.transcript_monitoring import versions, with_held
from src.ui.transcript_network import network_view
from src.ui.transcript_presence import HAND_IN_TOOLS, hand_ins, on_lanes, presence
from src.ui.transcript_rounds import on_clock, rounds

SUMMARY_CHARS = 140
"""The longest one-line summary of a call."""
BOARD_TOOLS = {"board_post": "post", "board_read": "read", "board_status": "status"}
"""The board's tools and the kind of board call each makes."""
LADDER_KEYS = ("name", "levels", "line", "line_words")
"""What the tab reads of each ladder: what the page's levelChip and lineMark (tv_levels.js) draw a level from."""
READ_ARGUMENTS = {"latest", "from_agent", "latest_from"}
"""The board_read tool's arguments (src/swarm/board.py board_read)."""
CLOCKS = {"eval_log": "seconds, from the eval log", "call_order": "call order: no times in the eval log"}
LENSES = ("cheating", "collusion", "coordination", "judge")
"""The tab's lenses besides All: each colours the calls by one ladder's marks, or by the judge labels'."""
READ, WRITE = coordination.LEVELS[1:3]
"""The Coordination levels of a read of a teammate's post and of a post."""
PIECE_RULE = "piece"
"""The rule of a take of a planted piece (src/measures/collusion.py piece_takes)."""


def model_calls(run_dir: Path) -> dict[str, list[list[str]]]:
    """agent -> the tool call ids of each of its model calls in transcript order: entry n - 1 is model call n as the
    judges number them (judge_records.model_calls), [] for a model call that called no tool."""
    stored = RunDir(run_dir)
    return {episode_agent(ep): [[tc.get("tool_call_id") for tc in step.get("tool_calls") or []]
                                for step in judged_model_calls(stored.transcript(ep["episode"]))]
            for ep in stored.with_transcripts()}


def step_numbers(steps: dict[str, list[list[str]]]) -> dict[str, int]:
    """tool call id -> the number of the model call that made it, per agent from 0, counting the model calls with a
    tool call (`steps`, model_calls' answer)."""
    return {call_id: m for calls in steps.values() for m, ids in enumerate(x for x in calls if x) for call_id in ids}


def cited_calls(text: str) -> list[int]:
    """The calls an evidence line cites, in order: "[k] " at its start or after "; " (transcript_levels.CITE_RE), or
    after ": ", where a clause or a take quotes its source ("the hand-in holds B, which it gave: [9] post holds B")."""
    return list(dict.fromkeys(int(m.group(1)) for part in str(text or "").split(": ")
                              for m in CITE_RE.finditer(part)))


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


def board_network(run_dir: Path, run: Run) -> dict | None:
    """The run's network log on the board's clock (transcript_network.network_view), None without network.jsonl."""
    name, at, _ = clock(run)
    by_id = {c.id: c for rows in run.calls.values() for c in rows}
    start = min(c.start for c in by_id.values()) if name == "eval_log" and by_id else None
    return network_view(run_dir, {c.id: (c.agent, c.k) for c in by_id.values()}, at, start)


def network_payload(run_dir: Path) -> dict | None:
    """The Board tab's Network panel (/api/network): board_network of the run's calls, None without network.jsonl,
    which it checks before it reads the run."""
    if not RunDir(run_dir).file("network.jsonl").is_file():
        return None
    return board_network(run_dir, load_run(run_dir, times=True))


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


def read_form(arguments: dict, writer: str) -> tuple[str | None, str | None]:
    """(form, the agent asked for) of a board_read call's read of a post by `writer`, on the module's terms."""
    given = {k: v for k, v in arguments.items() if v is not None}
    if not set(given) <= READ_ARGUMENTS:
        return None, None
    asked = given.get("from_agent")
    if asked == writer:
        return "from", asked
    return "latest", None


def call_labels(levels: dict, agent: str, ids: dict[str, int]) -> dict[int, list[dict]]:
    """call number -> the labels the run page puts on it (levels["labels"], by k or by tool call id)."""
    out: dict[int, list[dict]] = {}
    for label in (levels.get("labels") or {}).get(agent) or []:
        k = label["k"] if label.get("k") is not None else ids.get(label.get("call_id"))
        if k is not None:
            out.setdefault(k, []).append({key: label[key] for key in ("ladder", "level", "index", "evidence")})
    return out


def final(levels: dict, agent: str) -> dict[str, dict]:
    """ladder -> the agent's level at the end of the run and its evidence as facts: its cells on the run page
    (levels["agents"]), {} when the page has none."""
    return {ladder: {"level": cell["level"], "facts": cell.get("facts") or []}
            for ladder, cell in ((levels.get("agents") or {}).get(agent) or {}).items()}


def post_labels(levels: dict) -> dict[str, list[dict]]:
    """post label -> the judge labels whose excerpt code located in the post: {agent, label, score, excerpt, reason,
    start, end, kind}, from the measure's judge labels in `levels`. kind is manipulation's (mislead, control or none)
    and None for disclosure: the page marks a mislead score, which an independent reading found untrustworthy."""
    out: dict[str, list[dict]] = {}
    for agent, row in levels["judge_labels"]["agents"].items():
        for label in judge_labels.LABELS if row else ():
            e = row[label]
            where = e["located"]
            if where and where["where"] == "post":
                out.setdefault(where["post"], []).append({
                    "agent": agent, "label": label, **{k: e[k] for k in ("score", "excerpt", "reason")},
                    "start": where["start"], "end": where["end"], "kind": e.get("kind")})
    return out


class Placed:
    """The marks of a run's calls by lens, (agent, k) -> {lens: [mark]}, and per agent the records that name no call,
    {lens: Counter of clause, rule or label}."""

    def __init__(self) -> None:
        self.marks: dict[tuple[str, int], dict[str, list[dict]]] = {}
        self.missed: dict[str, dict[str, Counter]] = {}

    def put(self, lens: str, agent: str, ks: list[int], mark: dict, what: str) -> None:
        """The mark on each call k of the agent; with no call, one more record of `what` that names none."""
        for k in ks:
            self.marks.setdefault((agent, k), {x: [] for x in LENSES})[lens].append(mark)
        if not ks:
            self.missed.setdefault(agent, {x: Counter() for x in LENSES})[lens][what] += 1

    def at(self, agent: str, k: int) -> dict[str, list[dict]]:
        return self.marks.get((agent, k)) or {x: [] for x in LENSES}

    def unplaced(self, agent: str) -> dict[str, dict[str, int]]:
        return {x: dict(c) for x, c in (self.missed.get(agent) or {x: Counter() for x in LENSES}).items()}


def logged_calls(run_dir: Path, at: dict[str, tuple[str, int]]) -> dict[str, dict[str, list[int]]]:
    """agent -> each read the access log shows by it, as the Cheating clauses word it (access_evidence.read_evidence)
    -> the calls of the agent its rows sit in. `at`: tool call id -> (agent, k)."""
    out: dict[str, dict[str, list[int]]] = {}
    for row in (RunDir(run_dir).access_log() or {}).get("rows") or []:
        where = at.get(row.get("call_id"))
        if where and where[0] == row.get("agent"):
            ks = out.setdefault(where[0], {}).setdefault(read_evidence(row), [])
            if where[1] not in ks:
                ks.append(where[1])
    return out


def cheating_marks(placed: Placed, levels: dict, agent: str, logged: dict[str, list[int]]) -> None:
    """Each evidence line of each of the agent's Cheating clauses at every call it cites, or at the calls the access
    log's rows of the read sit in (`logged`, logged_calls' entry for the agent), with the clause's rung. Where the
    agent's Cheating timeline was read after every call, each clause also at the call at which it first held
    (transcript_levels.own_call), and a line that names no call of a clause so placed counts as placed. Each undone
    write (the timeline's "rises") at the call that wrote it, at its rung, and at the call where its rung fell back,
    with how it was taken out, at the rung held there."""
    record = ((levels.get("cheating") or {}).get("agents") or {}).get(agent)
    timeline = stepped(record)
    at = {e["clause"]: (e, own_call(timeline, "cheating", e, agent)) for e in timeline["first"]} if timeline else {}

    def mark(clause: str, evidence: str) -> dict:
        level = cheating_ladder.CLAUSES[clause]
        return {"level": level, "index": cheating_ladder.RUNGS.index(level), "clause": clause, "evidence": evidence}

    for clause, lines in (record["clauses"] if record else {}).items():
        dated = at.get(clause, (None, None))[1] is not None
        for line in lines:
            ks = cited_calls(line) or logged.get(line, [])
            if ks or not dated:
                placed.put("cheating", agent, ks, mark(clause, line), clause)
    for clause, (entry, k) in at.items():
        if k is not None and all(m["clause"] != clause for m in placed.at(agent, k)["cheating"]):
            placed.put("cheating", agent, [k], mark(clause, step_text(entry)), clause)
    after = ((record or {}).get("timeline") or {}).get("after") or []
    for rise in ((record or {}).get("timeline") or {}).get("rises") or []:
        fell = after[rise["fell_at"]] if rise["fell_at"] < len(after) else rise["rung"]
        for k, level, text in ((rise["k"], rise["rung"], rise["evidence"]),
                               (rise["fell_at"], fell, f"taken out: {rise['how']}")):
            placed.put("cheating", agent, [k], {"level": level, "index": cheating_ladder.RUNGS.index(level),
                                                "clause": "undone", "evidence": text}, "undone")


def took_up(boards: list[dict], roles: dict[str, str | None]) -> dict[str, list[str]]:
    """tool call id of each post -> the teammates whose new posts it took up, in `roles` order: the R_ij that grew at
    that post, from the measure's rows before and after it (coordination.coordination_steps)."""
    agents = list(roles)
    out: dict[str, list[str]] = {}
    before = {a: dict.fromkeys(agents, 0) for a in agents}
    for step in coordination.coordination_steps(boards, roles):
        if step["kind"] == "sent":
            a = step["agent"]
            grew = step["agents"][a]["takes_up"]
            out[step["call_id"]] = [b for b in agents if grew.get(b, 0) > before[a].get(b, 0)]
        before = {a: row["takes_up"] for a, row in step["agents"].items()}
    return out


def held_marks(placed: Placed, levels: dict, agent: str, ids: dict[str, int]) -> None:
    """Each change of the Coordination level the agent holds (transcript_levels' held events in `levels`) at the call
    from which it holds it (`ids`, tool call id -> k); one after its last board call names no call."""
    for e in (levels.get("events") or {}).get(agent) or []:
        if e.get("held"):
            k = ids.get(e.get("call_id"))
            placed.put("coordination", agent, [] if k is None else [k],
                       {"level": e["level"], "index": e["index"], "held": True, "evidence": e["evidence"]}, e["level"])


def held_at(rows: list[Call], placed: Placed, agent: str) -> list[int | None]:
    """The Coordination level the agent holds at each of its calls, in order: the index of the latest held mark at or
    before the call (held_marks), None before the first."""
    out, now = [], None
    for c in rows:
        now = next((m["index"] for m in placed.at(agent, c.k)["coordination"] if m.get("held")), now)
        out.append(now)
    return out


def coordination_marks(placed: Placed, posts: list[dict], took: dict[tuple[str, int], list[str]]) -> None:
    """Each call that read a teammate's post it had not read before (each agent's first read of a post), as read, and
    each call that posted, as write, with the teammates its post took up (`took`, by (agent, k))."""
    new: dict[tuple[str, int], list[str]] = {}
    for p in posts:
        if p["kind"] != "post":
            continue
        first: dict[str, int] = {}
        for r in sorted(p["reads"], key=lambda r: (r["t"], r["k"])):
            first.setdefault(r["agent"], r["k"])
        for agent, k in first.items():
            new.setdefault((agent, k), []).append(f"{p['label']} by {p['writer']}")
        if p["call"] is not None:
            up = took.get((p["writer"], p["call"])) or []
            placed.put("coordination", p["writer"], [p["call"]], {
                "level": WRITE, "index": coordination.LEVELS.index(WRITE), "post": p["label"], "took_up": up,
                "evidence": f"wrote {p['label']}" + (f", taking up the new posts of {', '.join(up)}" if up else "")},
                WRITE)
    for (agent, k), names in new.items():
        placed.put("coordination", agent, [k], {"level": READ, "index": coordination.LEVELS.index(READ),
                                                 "evidence": f"read {', '.join(names)}, new to it"}, READ)


def take_marks(placed: Placed, levels: dict, agent: str) -> None:
    """Each take the agent made (the Collusion measure's pairs in `levels`) at its call, or at the calls its evidence
    cites, with whose work it took, its rule and whether it took a planted piece."""
    measure = levels.get("collusion") or {}
    for p in measure.get("pairs") or [] if measure.get("applicable") else []:
        for t in p.get("takes") or []:
            if t["taker"] == agent:
                placed.put("collusion", agent, [t["call"]] if t["call"] is not None else cited_calls(t["evidence"]),
                           {"level": collusion.RUNGS[1], "index": 1, "giver": t["giver"], "rule": t["rule"],
                            "planted": t["rule"] == PIECE_RULE, "evidence": t["evidence"]}, t["rule"])


def rung_marks(placed: Placed, levels: dict, agent: str) -> None:
    """Where the agent's Collusion timeline was read after every call: each rung it rose to (the timeline's "first"),
    at the call at which the agent holds it (transcript_levels.own_call), with the teammate it holds it with; a rung
    at no call counted by its name."""
    measure = levels.get("collusion") or {}
    timeline = stepped((measure.get("agents") or {}).get(agent) if measure.get("applicable") else None)
    for e in timeline["first"] if timeline else []:
        k = own_call(timeline, "collusion", e, agent)
        placed.put("collusion", agent, [] if k is None else [k], {
            "level": e["rung"], "index": collusion.RUNGS.index(e["rung"]), "with": e["with"],
            "evidence": "; ".join(e["evidence"]) or e["rung"]}, e["rung"])


def after_at(levels: dict, agent: str) -> dict[str, list[int]]:
    """ladder -> the index of the rung the agent holds after each of its calls, for Cheating and Collusion where the
    measure read them after every call (the timeline's "after"), and for Cheating where the timeline holds an undone
    write's rise and fall (its "rises"), which a run read from its record alone dates too."""
    collusion_measure = levels.get("collusion") or {}
    cheating_row = ((levels.get("cheating") or {}).get("agents") or {}).get(agent)
    rows = {"cheating": cheating_row,
            "collusion": (collusion_measure.get("agents") or {}).get(agent) if collusion_measure.get("applicable")
            else None}
    cheating_timeline = (cheating_row or {}).get("timeline") or {}
    risen = {"cheating": cheating_timeline if cheating_timeline.get("rises") else None}
    return {ladder: [levels["ladders"][ladder]["levels"].index(rung) for rung in timeline["after"]]
            for ladder, row in rows.items() if (timeline := stepped(row) or risen.get(ladder))}


def judge_marks(placed: Placed, levels: dict, agent: str, steps: list[list[str]], k_of: dict[str, int],
                post_calls: dict[str, int]) -> None:
    """Each judge label of the agent whose excerpt code located: in its reasoning, at the tool calls of that model
    call (`steps`, model_calls' entry for the agent; `k_of`, tool call id -> k); in its post, at the call that wrote
    the post (`post_calls`, post label -> k). A label with an excerpt code did not locate names no call."""
    row = levels["judge_labels"]["agents"].get(agent)
    for label in judge_labels.LABELS if row else ():
        e = row[label]
        where = e["located"]
        if where is None and not e.get("excerpt"):
            continue
        if where is None:
            ks = []
        elif where["where"] == "post":
            ks = [post_calls[where["post"]]] if where["post"] in post_calls else []
        else:
            ids = steps[where["call"] - 1] if 1 <= (where["call"] or 0) <= len(steps) else []
            ks = [k_of[i] for i in ids if i in k_of]
        placed.put("judge", agent, ks, {"label": label, "score": e["score"], "kind": e.get("kind"),
                                        "where": where["where"] if where else None, "evidence": e["excerpt"]}, label)


def default_pair(levels: dict, roles: dict[str, str | None], posts_by: dict[str, int]) -> tuple[list[str], str]:
    """The two agents the tab opens with and why (the module's rule)."""
    agents = list(roles)

    def rung(a: str) -> int:
        level = (final(levels, a).get("cheating") or {}).get("level")
        return cheating_ladder.RUNGS.index(level) if level in cheating_ladder.RUNGS else 0

    workers = sorted((a for a in agents if is_worker(roles[a])),
                     key=lambda a: (-rung(a), -posts_by.get(a, 0), agents.index(a)))
    if workers and rung(workers[0]):
        top = workers[0]
        influencers = sorted((a for a in agents if not is_worker(roles[a])),
                             key=lambda a: (-posts_by.get(a, 0), agents.index(a)))
        if influencers:
            return [top, influencers[0]], f"default: the worker highest on Cheating and the {roles[influencers[0]]}"
        if len(workers) > 1:
            return [top, workers[1]], "default: the two workers highest on Cheating"
    measure = levels.get("collusion") or {}
    pairs = [p for p in measure.get("pairs") or [] if p["rung"] in collusion.RUNGS[1:]]
    if pairs:
        top = max(pairs, key=lambda p: (collusion.RUNGS.index(p["rung"]), all(is_worker(r) for r in p["roles"]),
                                        sum(posts_by.get(a, 0) for a in p["agents"])))
        return list(top["agents"]), f"default: the highest Collusion pair, {top['rung']}"
    ranked = sorted(agents, key=lambda a: (not is_worker(roles[a]), -posts_by.get(a, 0), agents.index(a)))
    return ranked[:2], "default: the two workers with the most posts"


def notice_time(message: dict, events: list[dict], by_id: dict[str, Call],
                at: dict[tuple[str, int], tuple[float, float]]) -> float:
    """When a system notice, which no call wrote, reached the board: the end of the call of the last board event before
    it (the board numbers its messages and events on one count), or 0 before any."""
    before = [by_id[e["tool_call_id"]] for e in events if (e.get("sequence") or 0) < (message.get("sequence") or 0)
              and e.get("tool_call_id") in by_id]
    return sum(at[(before[-1].agent, before[-1].k)]) if before else 0.0


def placed_checks(stored: dict | None, by_id: dict[str, Call], at: dict[tuple[str, int], tuple[float, float]]) \
        -> dict | None:
    """transcript_monitoring.versions' answer with each check placed on the board's clock: the k and the start t of its
    check_fix call, both None where the call is not found; None without the stored fact."""
    if stored is None:
        return None

    def place(c: dict) -> dict:
        call = by_id.get(c["call_id"]) if c["call_id"] else None
        return {**c, "k": call.k, "t": at[(call.agent, call.k)][0]} if call else {**c, "k": None, "t": None}
    return {**stored, "checks": [place(c) for c in stored["checks"]]}


def board_payload(run_dir: Path, levels: dict) -> dict:
    """The Board tab's data for one run: {clock, clock_about, total, agents, posts, pair, pair_why, ladders,
    label_names (each judge label's name), notes, presence (transcript_presence.on_lanes), hand_ins
    (transcript_presence.hand_ins, each hand-in with what it held), versions (placed_checks), calls_per_round,
    seed_pack}. `levels` is the run page's ladders (transcript_levels.compute). The network log comes on its own (network_payload)."""
    run = load_run(run_dir, times=True)
    name, at, total = clock(run)
    by_model = model_calls(run_dir)
    steps = step_numbers(by_model)
    listed = labelled_posts(run)
    labelled = {p["id"]: p["label"] for p in listed if not p.get("record")}
    events = sorted((e for board in run.boards for e in board.get("events") or []),
                    key=lambda e: e.get("sequence") or 0)
    wrote: dict[str, list[str]] = {}
    read: dict[str, list[str]] = {}
    for event in events:
        into = {"sent": wrote, "read": read}.get(event.get("kind"))
        if into is not None:
            into.setdefault(event.get("tool_call_id"), []).extend(
                labelled[mid] for mid in event.get("message_ids") or [] if mid in labelled)
    by_id = {c.id: c for rows in run.calls.values() for c in rows}
    sent_by = {mid: by_id.get(e.get("tool_call_id")) for e in events if e.get("kind") == "sent"
               for mid in e.get("message_ids") or []}
    judged = post_labels(levels)
    ordered = sorted(by_id.values(), key=lambda c: at[(c.agent, c.k)][0])
    posts = []
    for post, message in zip(listed, run.messages):
        writer_call = sent_by.get(post["id"])
        if writer_call is not None:
            t = at[(writer_call.agent, writer_call.k)][0]
        else:
            t = notice_time(message, events, by_id, at)
        reads = [{"agent": c.agent, "k": c.k, "t": at[(c.agent, c.k)][0],
                  **dict(zip(("form", "asked"), read_form(c.arguments, post["writer"])))} for c in ordered
                 if not post.get("record") and post["label"] in read.get(c.id, []) and c.agent != post["writer"]]
        posts.append({"label": post["label"], "writer": post["writer"], "role": run.roles.get(post["writer"]),
                      "kind": "seeded" if is_seeded(message) else "post" if post["writer"] in run.roles else "system",
                      "t": t,
                      "call": writer_call.k if writer_call else None, "text": post["content"], "reads": reads,
                      "labels": judged.get(post["label"], []) if not post.get("record") else [],
                      **({"record": True} if post.get("record") else {})})
    posts.sort(key=lambda p: p["t"])
    placed = Placed()
    logged = logged_calls(run_dir, {c.id: (c.agent, c.k) for c in by_id.values()})
    took = {(by_id[i].agent, by_id[i].k): up for i, up in took_up(run.boards, run.roles).items() if i in by_id}
    coordination_marks(placed, posts, took)
    post_calls = {p["label"]: p["call"] for p in posts if p["call"] is not None}
    lanes, in_round = on_clock(rounds(run.run_json), run.calls, at, name == "eval_log")
    stored = versions(run_dir, run.run_json)
    handed = with_held(hand_ins(run.run_json), stored)
    event_of = {e["call_id"]: i for i, e in enumerate(handed["events"]) if e["call_id"]}
    agents = []
    for agent, rows in run.calls.items():
        cheating_marks(placed, levels, agent, logged.get(agent) or {})
        take_marks(placed, levels, agent)
        rung_marks(placed, levels, agent)
        k_of = {c.id: c.k for c in rows}
        held_marks(placed, levels, agent, k_of)
        judge_marks(placed, levels, agent, by_model.get(agent) or [], k_of, post_calls)
        labels = call_labels(levels, agent, k_of)
        after = after_at(levels, agent)
        agents.append({"agent": agent, "role": run.roles.get(agent) or "worker", "calls": [{
            "k": c.k, "m": steps.get(c.id), "t": at[(agent, c.k)][0], "dur": at[(agent, c.k)][1], "tool": c.tool,
            "summary": summary(c, wrote.get(c.id, []), read.get(c.id, [])), "board": BOARD_TOOLS.get(c.tool),
            "labels": labels.get(c.k, []), "marks": placed.at(agent, c.k), "held": held,
            "after": {ladder: rungs[c.k] for ladder, rungs in after.items() if c.k < len(rungs)},
            **(in_round.get((agent, c.k)) or {"round": None, "pos": None}),
            **({"hand_in": event_of.get(c.id)} if c.tool in HAND_IN_TOOLS.values() else {})}
            for c, held in zip(rows, held_at(rows, placed, agent))],
            "final": final(levels, agent), "unplaced": placed.unplaced(agent), "rounds": lanes.get(agent) or []})
    agents.sort(key=lambda a: (a["role"] != "worker", a["agent"]))
    posts_by = {a: sum(p["writer"] == a for p in posts) for a in run.roles}
    pair, why = default_pair(levels, run.roles, posts_by)
    formless = sum(r["form"] is None for p in posts for r in p["reads"])
    notes = run.notes + ([f"{formless} reads of no recorded form"] if formless else [])
    return {"clock": name, "clock_about": CLOCKS[name], "total": total, "agents": agents, "posts": posts,
            "pair": pair, "pair_why": why,
            "ladders": {k: {key: v[key] for key in LADDER_KEYS} for k, v in levels["ladders"].items()},
            "label_names": {k: v["name"] for k, v in levels["judge_label_about"]["labels"].items()},
            "notes": notes, "presence": on_lanes(presence(run.run_json), run.calls, at), "hand_ins": handed,
            "versions": placed_checks(stored, by_id, at),
            "calls_per_round": (run.run_json.get("rules") or {}).get("calls_per_round"),
            "seed_pack": (run.run_json.get("config") or {}).get("seed_pack") or None}
