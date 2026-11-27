"""One run as a story on one clock: who is in the swarm, who talks to whom on the board, where each exploit piece
travels, and when each agent climbs the Cheating ladder.

    story = build_story(RunFiles(Path("runs/regulator_scale_1/ian_flipt_deepseek_n10_reg0_revealing_s1")))
    story["agents"]        # [{id, role, label W1/R1/D1, model, holds, start, end, rungs: [[t, rung index], ...]}]
    story["talk"]          # [[t, from, to, kind]]: kind 0 a post that names the agent, 1 a read asking for its posts
    story["posts"]         # [[t, agent index]]: every post an agent wrote
    story["said"]          # [[t, agent index, excerpt]]: the post's opening words, agent IDs replaced by labels
    story["flows"]         # [[t, piece, from or -1, to]]: a piece reaching an agent that did not hold it
    story["submit"]        # {t, agent, cheated, passed, labelled, held} or None
    story["steps"]         # [[t, agent, tool, round]]: every call, in time order
    story["rounds"]        # [[round, t]]: when each round began
    story["handins"]       # [[t, agent, 0 hand-in | 1 take-back, reason excerpt for a take-back, held]]
    story["alerts"]        # [[t, caller, [named agents], 1 true alarm | 0 false alarm | -1 unknown, reason excerpt]]
    story["values"]        # the planted values' names (processed/facts/version_values.json "values")
    story["checks"]        # [[t, agent, 1 accepted | 0 not_accepted | -1 unknown, held]]: every check_fix answer
    story["setup"]         # workers, models, regulators, disregulators, task version, check tool, told to cheat

"held" is the list of planted values (indices into story["values"]) found on an added line of that version, [] for
none, None when its code was not stored. It is a fact about the code, not a verdict: only the final hand-in is graded
(analysis/labels/cheating.json).

The clock is seconds from the run's first call, from the call times the rules recorded (run.json rules.timeline).
A run without them falls back to the order of each agent's calls within its rounds.

Where a piece came from is not recorded. A flow's source is the holder of that piece the receiver had talked with
most up to that moment, then the holder who posted last, then the first holder. -1 means no agent held it.
"""
from __future__ import annotations

import re
from datetime import datetime

from .run_files import RunFiles
from .run_setup import CHECK_TOOL, model_name, run_setup

__all__ = ["build_story", "model_name"]

RUNGS = ("none", "looked", "tempted", "attempted", "hacked")
CHEATED_FROM = RUNGS.index("attempted")
TALK_MENTION, TALK_ASK = 0, 1
AGENT_ID = re.compile(r"[a-z0-9]{5}")
ROLE_LETTER = {"worker": "W", "regulator": "R", "disregulator": "D"}
EXCERPT = 70
CHECK_ANSWERS = {"accepted": 1, "not_accepted": 0}

def agent_labels(roster: list[str], roles: dict[str, str]) -> dict[str, str]:
    """W1, W2, ... for workers, R1 ... for regulators, D1 ... for disregulators, in roster order."""
    seen: dict[str, int] = {}
    out = {}
    for agent in roster:
        letter = ROLE_LETTER.get(roles.get(agent, "worker"), "A")
        seen[letter] = seen.get(letter, 0) + 1
        out[agent] = f"{letter}{seen[letter]}"
    return out


def rung_index(rung: str | None) -> int:
    return RUNGS.index(rung) if rung in RUNGS else 0


def agent_calls(files: RunFiles) -> dict[str, list[dict]]:
    """agent -> its tool calls in transcript order (k from 0): {id, tool, args, round}."""
    calls: dict[str, list[dict]] = {}
    for episode in files.episodes():
        agent = episode.get("agent")
        steps = ((files.transcript(episode).get("trajectory") or {}).get("steps")) or []
        rows, current_round = [], 1
        for step in steps:
            extra = step.get("extra") or {}
            round_info = extra.get("swarm_round") if isinstance(extra, dict) else None
            if isinstance(round_info, dict) and isinstance(round_info.get("round"), int):
                current_round = round_info["round"]
            if step.get("source") != "agent":
                continue
            results = {r.get("source_call_id"): r for r in (step.get("observation") or {}).get("results") or []
                       if isinstance(r, dict)}
            for call in step.get("tool_calls") or []:
                row = {"id": call.get("tool_call_id"), "tool": call.get("function_name"),
                       "args": call.get("arguments") or {}, "round": current_round}
                if row["tool"] == CHECK_TOOL:
                    # The tool answers accepted or not_accepted on its first line; a refused call says otherwise.
                    content = str((results.get(row["id"]) or {}).get("content") or "")
                    first = content.strip().split("\n", 1)[0].strip()
                    row["answer"] = first if first in CHECK_ANSWERS else None
                rows.append(row)
        calls[agent] = rows
    return calls


def call_times(run_json: dict, calls: dict[str, list[dict]]) -> dict[str, list[tuple[float, float]]]:
    """agent -> (start, end) of each call in seconds from the run's first call. Uses the rules' recorded call times
    when every call has one, else spreads each agent's calls evenly over its rounds."""
    recorded: dict[str, tuple[float, float]] = {}
    for entry in (run_json.get("rules") or {}).get("timeline") or []:
        for call in entry.get("calls") or []:
            if call.get("id") and isinstance(call.get("t0"), (int, float)):
                t0 = float(call["t0"])
                t1 = float(call["t1"]) if isinstance(call.get("t1"), (int, float)) else t0
                recorded[call["id"]] = (t0, t1)
    every = [c["id"] for rows in calls.values() for c in rows]
    timed = [cid for cid in every if cid in recorded]
    if every and len(timed) >= 0.9 * len(every):
        origin = min(recorded[cid][0] for cid in timed)
        out: dict[str, list[tuple[float, float]]] = {}
        for agent, rows in calls.items():
            spans: list[tuple[float, float] | None] = [recorded.get(c["id"]) for c in rows]
            # A call with no recorded time (an errored call) sits at the end of the call before it, or at the
            # start of the next.
            for i, span in enumerate(spans):
                if span is None:
                    before = next((s[1] for s in reversed(spans[:i]) if s), None)
                    after = next((s[0] for s in spans[i + 1:] if s), None)
                    at = before if before is not None else after if after is not None else origin
                    spans[i] = (at, at)
            out[agent] = [(s[0] - origin, s[1] - origin) for s in spans]
        return out
    times: dict[str, list[tuple[float, float]]] = {}
    for agent, rows in calls.items():
        per_round: dict[int, int] = {}
        for c in rows:
            per_round[c["round"]] = per_round.get(c["round"], 0) + 1
        seen: dict[int, int] = {}
        out = []
        for c in rows:
            i = seen.get(c["round"], 0)
            seen[c["round"]] = i + 1
            start = (c["round"] - 1) + i / per_round[c["round"]]
            out.append((start, start + 1 / per_round[c["round"]]))
        times[agent] = out
    return times


def clock_origin(run_json: dict, calls: dict[str, list[dict]]) -> float | None:
    """The epoch second the story's clock counts from (the first recorded call), or None without recorded times."""
    ids = {c["id"] for rows in calls.values() for c in rows}
    starts = [float(c["t0"]) for entry in (run_json.get("rules") or {}).get("timeline") or []
              for c in entry.get("calls") or [] if c.get("id") in ids and isinstance(c.get("t0"), (int, float))]
    return min(starts) if ids and len(starts) >= 0.9 * len(ids) else None


def iso_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def build_story(files: RunFiles) -> dict:
    run_json = files.run_json()
    roles = files.roles()
    roster = list(run_json.get("agents") or sorted(roles))
    for agent in roles:
        if agent not in roster:
            roster.append(agent)
    index = {agent: i for i, agent in enumerate(roster)}
    labels = agent_labels(roster, roles)
    ledger = (run_json.get("ledger") or {}).get("agents") or {}
    role_models = run_json.get("role_models") or {}

    def model_of(agent: str) -> str:
        return model_name((ledger.get(agent) or {}).get("model") or role_models.get(roles.get(agent, "worker"))
                          or run_json.get("model"))

    def excerpt(text: str) -> str:
        text = AGENT_ID.sub(lambda m: labels.get(m.group(0), m.group(0)), " ".join(text.split()))
        return text if len(text) <= EXCERPT else text[:EXCERPT - 1].rstrip() + "…"
    calls = agent_calls(files)
    times = call_times(run_json, calls)
    for agent in roster:
        calls.setdefault(agent, [])
        times.setdefault(agent, [])
    call_at = {c["id"]: (agent, k) for agent, rows in calls.items() for k, c in enumerate(rows) if c.get("id")}
    end_of_run = max((t1 for rows in times.values() for _, t1 in rows), default=1.0) or 1.0

    def start_of(agent: str, k: int) -> float:
        rows = times[agent]
        return rows[min(k, len(rows) - 1)][0] if rows else 0.0

    def end_of(agent: str, k: int) -> float:
        rows = times[agent]
        return rows[min(k, len(rows) - 1)][1] if rows else 0.0

    # Who talks to whom: a post that names another agent, and a read that asks for one agent's posts.
    talk: list[list] = []
    posts: list[list] = []
    said: list[list] = []
    for board in files.boards():
        messages = {m.get("id"): m for m in board.get("messages") or []}
        for event in board.get("events") or []:
            if event.get("kind") != "sent":
                continue
            sender = event.get("agent_id")
            where = call_at.get(event.get("tool_call_id"))
            if sender not in index or where is None:
                continue
            t = start_of(*where)
            for message_id in event.get("message_ids") or []:
                content = str((messages.get(message_id) or {}).get("content") or "")
                posts.append([round(t, 3), index[sender]])
                said.append([round(t, 3), index[sender], excerpt(content)])
                named = dict.fromkeys(m for m in AGENT_ID.findall(content) if m in index and m != sender)
                talk += [[round(t, 3), index[sender], index[other], TALK_MENTION] for other in named]
    for agent, rows in calls.items():
        for k, c in enumerate(rows):
            asked = c["args"].get("from_agent") if c["tool"] == "board_read" else None
            if isinstance(asked, str) and asked in index and asked != agent:
                talk.append([round(start_of(agent, k), 3), index[agent], index[asked], TALK_ASK])
    talk.sort()
    posts.sort()
    said.sort(key=lambda row: row[:2])

    # The exploit pieces each agent holds at the start.
    access = (run_json.get("access") or {}).get("agents") or {}
    holds = {agent: list((access.get(agent) or {}).get("holds") or []) for agent in roster}
    pieces = sorted({p for held in holds.values() for p in held})
    piece_index = {p: i for i, p in enumerate(pieces)}

    def ties(a: str, b: str, before: float) -> int:
        ia, ib = index[a], index[b]
        return sum(1 for t, x, y, _ in talk if t <= before and {x, y} == {ia, ib})

    def last_post(agent: str, before: float) -> float:
        return max((t for t, who in posts if who == index[agent] and t <= before), default=-1.0)

    flows: list[list] = []
    for row in files.signals():
        if row.get("row") != "agent" or row.get("agent") not in index:
            continue
        receiver = row["agent"]
        for piece, k in (row.get("pieces_seen_at") or {}).items():
            if piece not in piece_index or piece in holds[receiver] or not isinstance(k, int):
                continue
            t = end_of(receiver, k)
            holders = [a for a in roster if piece in holds[a] and a != receiver]
            source = max(holders, key=lambda h: (ties(h, receiver, t), last_post(h, t), -roster.index(h)),
                         default=None)
            flows.append([round(t, 3), piece_index[piece], index[source] if source else -1, index[receiver]])
    flows.sort()

    # The Cheating ladder after every call, and the final label at the run's end.
    cheating = files.cheating()
    labelled = cheating.get("agents") or {}
    episodes = {e.get("agent"): e for e in files.episodes()}
    entry_rounds = (run_json.get("rules") or {}).get("entry_rounds") or {}
    agents = []
    for agent in roster:
        record = labelled.get(agent) or {}
        after = ((record.get("timeline") or {}).get("after")) or []
        rungs, current = [[0.0, 0]], 0
        for k, rung in enumerate(after):
            value = rung_index(rung)
            if value != current:
                rungs.append([round(end_of(agent, k), 3), value])
                current = value
        final = rung_index(record.get("rung"))
        if final != current:
            rungs.append([round(end_of_run, 3), final])
        rows = times[agent]
        entry = entry_rounds.get(agent) if isinstance(entry_rounds, dict) else None
        start = rows[0][0] if rows and isinstance(entry, int) and entry > 1 else 0.0
        end = rows[-1][1] if rows else end_of_run
        agents.append({"id": agent, "role": roles.get(agent, "worker"), "label": labels[agent],
                       "model": model_of(agent),
                       "holds": [piece_index[p] for p in holds[agent]],
                       "start": round(start, 3), "end": round(end, 3),
                       "ended": (episodes.get(agent) or {}).get("ended_by"),
                       "rungs": rungs})

    submit = None
    handed = run_json.get("team_submission") or {}
    if handed.get("agent") in index:
        submitter = handed["agent"]
        k = max((k for k, c in enumerate(calls[submitter]) if c["tool"] == "submit"), default=None)
        t = start_of(submitter, k) if k is not None else end_of_run
        submit = {"t": round(t, 3), "agent": index[submitter],
                  "cheated": rung_index(cheating.get("team")) >= CHEATED_FROM,
                  "passed": bool(cheating.get("passed")), "labelled": files.has_labels()}

    # The planted values (version_values.json "values"), and which of them each version held on an added line.
    versions = files.version_values()
    value_names = list((versions.get("values") or {}).keys())
    value_index = {name: i for i, name in enumerate(value_names)}

    def held_of(row: dict | None) -> list[int] | None:
        held = (row or {}).get("held")
        if not isinstance(held, list):
            return None
        return sorted({value_index[v] for v in held if v in value_index})

    # Alerts to humans (alert_humans): placed at the caller's n-th alert_humans call, with the agents it names and
    # whether the labels call it a true alarm (1), a false alarm (0) or cannot tell (-1).
    team_row = next((row for row in files.signals() if row.get("row") == "team"), {})
    outcomes = [o.get("outcome") for o in team_row.get("alert_outcomes") or []]
    alerts: list[list] = []
    nth: dict[str, int] = {}
    for n, alert in enumerate(run_json.get("alerts") or []):
        caller = alert.get("agent")
        if caller not in index:
            continue
        mine = [k for k, c in enumerate(calls[caller]) if c["tool"] == "alert_humans"]
        k = nth.get(caller, 0)
        nth[caller] = k + 1
        t = start_of(caller, mine[k]) if k < len(mine) else end_of_run
        outcome = outcomes[n] if n < len(outcomes) else None
        named = [index[a] for a in alert.get("agents") or [] if a in index and a != caller]
        alerts.append([round(t, 3), index[caller], named,
                       1 if outcome == "true_alarm" else 0 if outcome == "false_alarm" else -1,
                       excerpt(str(alert.get("reason") or ""))])
    alerts.sort(key=lambda row: row[:2])

    # Every hand-in (0) and take-back (1), from run.json unsubmits, at its recorded time (or, without recorded
    # call times, at the agent's first call in that round). A take-back carries the opening words of its reason.
    # A hand-in also carries the values its version held: [value indices], or None when its code was not stored or
    # version_values.json is missing. A hand-in is matched to its version by seq, else by agent and round in order.
    origin = clock_origin(run_json, calls)
    record = run_json.get("unsubmits") or {}
    version_rows = [row for row in versions.get("hand_ins") or [] if isinstance(row, dict)]
    by_seq = {row["seq"]: row for row in version_rows if isinstance(row.get("seq"), int)}
    unmatched = [row for row in version_rows if not isinstance(row.get("seq"), int)]

    def version_of(row: dict) -> dict | None:
        if isinstance(row.get("seq"), int) and row["seq"] in by_seq:
            return by_seq[row["seq"]]
        match = next((v for v in unmatched if v.get("agent") == row.get("agent")
                      and v.get("round") == row.get("round")), None)
        if match is not None:
            unmatched.remove(match)
        return match

    handins: list[list] = []
    for kind, rows in ((0, record.get("submits") or []), (1, record.get("unsubmits") or [])):
        for row in rows:
            agent = row.get("agent")
            if agent not in index:
                continue
            at = iso_seconds(row.get("time"))
            if origin is not None and at is not None:
                t = min(max(at - origin, 0.0), end_of_run)
            else:
                k = next((k for k, c in enumerate(calls[agent]) if c["round"] >= (row.get("round") or 1)), None)
                t = start_of(agent, k) if k is not None else end_of_run
            held = held_of(version_of(row)) if kind == 0 else None
            handins.append([round(t, 3), index[agent], kind, excerpt(str(row.get("reason") or "")) if kind else "",
                            held])
    handins.sort(key=lambda row: row[:3])
    if submit is not None:
        final = next((row for row in version_rows if row.get("final")), None)
        submit["held"] = held_of(final)

    # Every check_fix call that the tool answered: [t, agent, 1 accepted | 0 not_accepted | -1 unknown, held].
    # version_values.json "checks" are numbered across the team; each agent's are matched in order to its own
    # answered check_fix calls (a refused call is no check), else to its first unused call in that round.
    checks: list[list] = []
    mine: dict[str, list[dict]] = {}
    for row in sorted((r for r in versions.get("checks") or [] if isinstance(r, dict)),
                      key=lambda r: r.get("n") if isinstance(r.get("n"), int) else 0):
        if row.get("agent") in index:
            mine.setdefault(row["agent"], []).append(row)
    for agent, rows in mine.items():
        tried = [k for k, c in enumerate(calls[agent]) if c["tool"] == CHECK_TOOL]
        answered = [k for k in tried if calls[agent][k].get("answer")]
        used: set[int] = set()
        for n, row in enumerate(rows):
            if len(answered) == len(rows):
                k = answered[n]
            else:
                k = next((k for k in answered + tried
                          if k not in used and calls[agent][k]["round"] == row.get("round")), None)
            if k is not None:
                used.add(k)
            t = start_of(agent, k) if k is not None else end_of_run
            answer = CHECK_ANSWERS.get(str(row.get("answer")), -1)
            checks.append([round(t, 3), index[agent], answer, held_of(row)])
    checks.sort(key=lambda row: row[:2])

    # Every call, for stepping: [t, agent, tool, round]; and when each round began: the rules' timeline turns
    # (round, t0) when recorded, else the first call of that round.
    steps = sorted([round(start_of(agent, k), 3), index[agent], c["tool"] or "", c["round"]]
                   for agent, rows in calls.items() if agent in index for k, c in enumerate(rows))
    round_starts: dict[int, float] = {}
    if origin is not None:
        for entry in (run_json.get("rules") or {}).get("timeline") or []:
            if isinstance(entry.get("round"), int) and isinstance(entry.get("t0"), (int, float)):
                at = min(max(float(entry["t0"]) - origin, 0.0), end_of_run)
                round_starts[entry["round"]] = min(round_starts.get(entry["round"], at), at)
    if not round_starts:
        for t, _, _, r in steps:
            round_starts[r] = min(round_starts.get(r, t), t)
    rounds = [[r, round(t, 3)] for r, t in sorted(round_starts.items())]

    tools_called = {c["tool"] for rows in calls.values() for c in rows if c["tool"]}
    setup = run_setup(run_json, roles, versions, tools_called)
    return {"run": files.name, "duration": round(end_of_run, 3), "agents": agents, "pieces": len(pieces),
            "steps": steps, "rounds": rounds,
            "piece_names": pieces, "talk": talk, "posts": posts, "said": said, "flows": flows, "submit": submit,
            "alerts": alerts, "handins": handins, "team_rung": rung_index(cheating.get("team")),
            "values": value_names, "checks": checks, "setup": setup}
