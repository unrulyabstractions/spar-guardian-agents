"""Who entered a run and who left it, and each influencer's alert, for the transcript viewer: the run page's bar and
Details tab (/api/run "presence") and the Board tab's lanes (/api/board "presence").

    p = presence(run_json)
    p["agents"]["a04"]      # {"entered": 2, "exited": {"why": "budget", "round": 3, "seq": 2} or None}
    p["alerts"][0]          # {agent, round, reason, agents, time, outcome, team_cheated_by_then, named_cheated_by_then}
    placed = on_lanes(p, run.calls, at)     # the same, each entry, exit and alert with its call k and its time t
    found = with_alert_calls(p, run_dir)    # presence's answer, each alert with the k of its alert_humans call

Everything comes from run.json: rules.entry_rounds (agent -> the round it entered,
src/swarm/turn_rules/_turn_presence.py), rules.exits (agent -> {why, round, seq} and, for a hand-in, hand_in and
ends_after_round; why is one of EXIT_WHY), alerts (one per alert_humans call: agent, round, reason, agents, time) and
the code ladder's team row (signals.team alert_outcomes, src/measures/alert_outcomes.py), which gives each alert its
outcome: "true_alarm", "false_alarm", or None when the ladder could not tell or the run was not measured. A run made
before presence was recorded has none of these: agents {} and alerts [].

On the board's clock (transcript_board.clock) an entry in the run's first round sits at the clock's start (t 0, start
true): the agent was present before its first call. A later entry sits at the start of the agent's first call, an exit
at the end of its last call, and an alert at the start of its alert_humans call (the agent's i-th such call for its i-th
alert). An agent with no call, or an alert whose call is not found, has k and t None there.

The team's hand-ins and take-backs (hand_ins, /api/run and /api/board "hand_ins") come from run.json too: unsubmits
(src/swarm/unsubmit.py: submits and unsubmits, each with its seq on one count) and team_submission, the hand-in that
stood at the end, with the submits the harness refused (its refused). A run made without the unsubmit option has
team_submission alone, its one hand-in; an older run may have neither, and then no event. Each event carries the id of
its call: the agent's submit or unsubmit call in rules.timeline whose span holds the record's time.

    h = hand_ins(run_json)
    h["events"][0]      # {kind: "submit" | "unsubmit", agent, round, snapshot, text, seq, call_id, final[, took_back]}
    h["final"]          # {agent, round, snapshot, call_id} of the hand-in that stood, or None
    h["refused"]        # [{agent, round, reason}]: the submits refused while a hand-in stood

presence, on_lanes and hand_ins read only what they are given; with_alert_calls reads the alerting agents'
transcripts. None writes.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.measures.run_directory import RunDir, episode_agent
from src.measures.shell_commands import tool_calls_of
from src.swarm.turn_rules import ALERT_TOOL, SUBMIT, UNSUBMIT_TOOL

EXIT_WHY = ("submit", "stop", "alert", "rounds", "budget", "context", "delivered")
"""An exit's reasons as rules.exits records them (src/swarm/turn_rules/_turn_presence.py record_exit); "budget" is a
run's before 2026-11-21."""
ALERT_KEYS = ("agent", "round", "reason", "agents", "time")
"""What the page shows of an alert of run.json's alerts."""
OUTCOME_KEYS = ("outcome", "team_cheated_by_then", "named_cheated_by_then")
"""What the page shows of an alert's outcome (alert_outcomes.alert_outcomes)."""
HAND_IN_TOOLS = {"submit": SUBMIT, "unsubmit": UNSUBMIT_TOOL}
"""The tool of each kind of hand-in event."""
CALL_SLACK = 0.05
"""Seconds a record's time may fall outside its call's span on the timeline (the timeline keeps milliseconds)."""


def outcomes_of(run_json: dict) -> list[dict]:
    """The code ladder's outcome of each alert, in run.json's alert order: signals.team.alert_outcomes, [] without."""
    team = (run_json.get("signals") or {}).get("team") or {}
    return list(team.get("alert_outcomes") or [])


def presence(run_json: dict) -> dict:
    """{"agents": {agent: {"entered": round or None, "exited": exit or None}}, "alerts": [...]}, on the module's terms.
    The agents follow run.json's agents, then any other agent the records name; an exit is rules.exits' record as
    stored. An alert takes the outcome at its own place in signals.team.alert_outcomes when that row names the same
    agent and round, else outcome None."""
    rules = run_json.get("rules") or {}
    entries = rules.get("entry_rounds") or {}
    exits = rules.get("exits") or {}
    named = list(run_json.get("agents") or [])
    order = [a for a in named if a in entries or a in exits]
    order += [a for a in [*entries, *exits] if a not in order]
    agents = {a: {"entered": entries.get(a), "exited": dict(exits[a]) if a in exits else None} for a in order}
    outcomes = outcomes_of(run_json)
    alerts = []
    for i, alert in enumerate(run_json.get("alerts") or []):
        row = outcomes[i] if i < len(outcomes) else {}
        same = row.get("agent") == alert.get("agent") and row.get("round") == alert.get("round")
        alerts.append({**{k: alert.get(k) for k in ALERT_KEYS},
                       **{k: row.get(k) if same else None for k in OUTCOME_KEYS}})
    return {"agents": agents, "alerts": alerts}


def call_at(timeline: list[dict], agent: str, tool: str, when) -> str | None:
    """The id of `agent`'s `tool` call in rules.timeline whose span (t0 to t1, UTC seconds) holds the ISO time `when`;
    None without a time or without such a call."""
    if not isinstance(when, str):
        return None
    t = datetime.fromisoformat(when).timestamp()
    found = [c.get("id") for e in timeline if e.get("agent") == agent for c in e.get("calls") or []
             if c.get("tool") == tool and c.get("t0") is not None
             and c["t0"] - CALL_SLACK <= t <= (c.get("t1") if c.get("t1") is not None else c["t0"]) + CALL_SLACK]
    return found[0] if len(found) == 1 else None


def hand_ins(run_json: dict) -> dict:
    """{"events", "final", "refused"} on the module's terms, the events in their seq order (their time where a record
    has no seq). An event's text is a hand-in's answer or a take-back's reason; a take-back's took_back is the hand-in
    it took back ({agent, round, snapshot}). The final event is the last hand-in that is team_submission."""
    timeline = (run_json.get("rules") or {}).get("timeline") or []
    stood = run_json.get("team_submission") or None
    recorded = run_json.get("unsubmits") or {}
    rows = [("submit", r) for r in recorded.get("submits") or []] + [("unsubmit", r) for r in recorded.get("unsubmits")
                                                                    or []]
    if not rows and stood:
        rows = [("submit", stood)]
    rows.sort(key=lambda kr: (kr[1].get("seq") is None, kr[1].get("seq") or 0, str(kr[1].get("time") or "")))
    events = []
    for kind, r in rows:
        event = {"kind": kind, "agent": r.get("agent"), "round": r.get("round"),
                 "snapshot": r.get("snapshot") if kind == "submit" else (r.get("submit") or {}).get("snapshot"),
                 "text": r.get("answer") if kind == "submit" else r.get("reason"), "seq": r.get("seq"),
                 "call_id": call_at(timeline, r.get("agent"), HAND_IN_TOOLS[kind], r.get("time")), "final": False}
        if kind == "unsubmit":
            took = r.get("submit") or {}
            event["took_back"] = {k: took.get(k) for k in ("agent", "round", "snapshot")}
        events.append((event, r.get("time")))
    final = None
    if stood:
        key = (stood.get("agent"), stood.get("round"), stood.get("snapshot"))
        same = [e for e, when in events if e["kind"] == "submit" and (
            (when is not None and when == stood.get("time")) or (e["agent"], e["round"], e["snapshot"]) == key)]
        if same:
            same[-1]["final"] = True
        final = {"agent": stood.get("agent"), "round": stood.get("round"), "snapshot": stood.get("snapshot"),
                 "call_id": same[-1]["call_id"] if same else None}
    refused = [{k: r.get(k) for k in ("agent", "round", "reason")} for r in (stood or {}).get("refused") or []]
    return {"events": [e for e, _ in events], "final": final, "refused": refused}


def on_lanes(p: dict, calls: dict[str, list], at: dict[tuple[str, int], tuple[float, float]]) -> dict:
    """presence's answer with each agent's entry and exit placed on the board's clock, {"entry": {k, t, start},
    "exit": {k, t}} beside "entered" and "exited", and each alert with its call k and time t, on the module's terms.
    calls: agent -> its calls in order (run_calls.Call: k and tool); at: (agent, k) -> (t, dur),
    transcript_board.clock's."""
    def place(agent: str, k: int | None, end: bool) -> dict:
        if k is None or (agent, k) not in at:
            return {"k": None, "t": None}
        t, dur = at[(agent, k)]
        return {"k": k, "t": t + dur if end else t}

    first = min((row["entered"] for row in p["agents"].values() if row["entered"] is not None), default=None)
    agents = {}
    for agent, row in p["agents"].items():
        rows = calls.get(agent) or []
        entry = place(agent, rows[0].k if rows and row["entered"] is not None else None, False)
        start = row["entered"] is not None and row["entered"] == first
        agents[agent] = {**row, "entry": {**entry, "t": 0.0, "start": True} if start else {**entry, "start": False},
                         "exit": place(agent, rows[-1].k if rows and row["exited"] else None, True)}
    ks = nth_alert_calls(p["alerts"], lambda agent: [c.k for c in calls.get(agent) or [] if c.tool == ALERT_TOOL])
    alerts = [{**alert, **place(alert["agent"], k, False)} for alert, k in zip(p["alerts"], ks)]
    return {"agents": agents, "alerts": alerts}


def nth_alert_calls(alerts: list[dict], ks_of) -> list[int | None]:
    """Each alert's call: the k of its agent's i-th alert_humans call for its i-th alert, None when there is no such
    call. ks_of(agent) -> the ks of the agent's alert_humans calls in order."""
    seen: dict[str, int] = {}
    cache: dict[str, list[int]] = {}
    out = []
    for alert in alerts:
        agent = alert.get("agent")
        if agent not in cache:
            cache[agent] = ks_of(agent)
        i = seen.get(agent, 0)
        seen[agent] = i + 1
        out.append(cache[agent][i] if i < len(cache[agent]) else None)
    return out


def with_alert_calls(p: dict, run_dir: Path) -> dict:
    """presence's answer with each alert's k, the number of its alert_humans call as the transcript scan numbers it
    (the agent tab's "Call k"), read from the alerting agent's transcript; None without the transcript or the call."""
    if not p["alerts"]:
        return p
    run = RunDir(run_dir)
    episodes = {episode_agent(ep): ep["episode"] for ep in run.with_transcripts()}

    def ks_of(agent: str) -> list[int]:
        if agent not in episodes:
            return []
        return [k for k, tc in tool_calls_of(run.transcript(episodes[agent])) if tc.get("function_name") == ALERT_TOOL]

    return {**p, "alerts": [{**a, "k": k} for a, k in zip(p["alerts"], nth_alert_calls(p["alerts"], ks_of))]}
