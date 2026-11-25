"""Each agent's rounds for the transcript viewer: the agent tab groups its steps by round (/api/run "rounds") and the
Board tab draws the rounds on each lane (/api/board, each agent's "rounds" and each call's round and place in it).

    r = rounds(run_json)
    r["rounds"], r["calls_per_round"]       # R and N as the turn rules recorded them (rules.round_budget,
                                            # rules.calls_per_round)
    r["agents"]["a02"][1]                   # {turn, round, n, end, ending, charge, charged, used, ended, t0, t1, calls}
    r["agents"]["a02"][1]["calls"][0]       # {id, tool, pos, free, refused, error, t0, t1, job}
    placed = on_clock(r, run.calls, at, timed)   # agent -> [{turn, round, n, end, ending, charge, charged, t, dur,
                                                 # calls}]
                                                 # on the board's clock, and (agent, k) -> {round, pos} of each call

A round is one timeline entry of rules.timeline (src/swarm/turn_rules/turn_activation.py after_activation): the
schedule's round (`round`, the same for every agent of one round, the round its line "Round {round} of {R}. ..."
gave), why the round ended (`end`, _turn_rounds.END_REASONS: end_round, calls (N reached), ending (submit,
stop_working or alert_humans went through), time, round_time, no_tool_call, context; None when ORBIT ended the agent's
loop otherwise), the tool that ended the agent's turns in it (`ending`: submit, stop, alert, or None) and its calls in
order, each with its place in the round among the calls that count toward N
(`pos`, from 1; a call past N is refused and keeps its place; end_round, which never counts, has none). Times are UTC
seconds, the eval log's clock.

A run before 2026-11-22 used up no round for a round of board calls alone: its entries carry the agent's own count
(`n`, the line "Round {n} of {R}." it read) and what the round counted as (`charge`: work, board, idle, stop, submit,
alert; `charged`), and its calls `free` (a board call); they read as recorded, None in a newer run.

A run before 2026-11-21 made one model step per round plus free follow-ups and recorded no calls per entry: rounds is
None there, and the viewer draws it as before.

On the board's clock (transcript_board.clock) a round on the eval log's clock spans its own t0 to t1, from the first
call's start (a round that opened before it starts at 0). On the call-order clock it spans its calls, and a round with
no call sits, with no width, at the end of the agent's round before it (0 for the first). A call matched to no round
(its id in no entry) has no round.
"""
from __future__ import annotations

from src.swarm.turn_rules import END_ROUND

OLD_END_ROUND = "end_round"         # wait's name in runs before 2026-11-22

ROUND_KEYS = ("turn", "round", "n", "end", "ending", "charge", "charged", "used", "ended", "t0", "t1")
"""What the viewer keeps of a timeline entry besides its calls."""
CALL_KEYS = ("id", "tool", "free", "refused", "error", "t0", "t1", "job")
"""What the viewer keeps of a call of a timeline entry besides its place (`pos`, places): job is the id of the job a
command call became when its round's time ran out (src/sandbox/command_jobs.py), None for any other call."""


def places(calls: list[dict]) -> list[int | None]:
    """Each call's place in its round, from 1, among the calls that count toward N: wait has none, nor end_round, its
    name in runs before 2026-11-22."""
    out, n = [], 0
    for call in calls:
        counts = call.get("tool") not in (END_ROUND, OLD_END_ROUND)
        n += counts
        out.append(n if counts else None)
    return out


def rounds(run_json: dict) -> dict | None:
    """{"rounds": R, "calls_per_round": N, "agents": {agent: [round]}} on the module's terms, the agents in timeline
    order; None for a run before 2026-11-21 (no entry carries its calls and the rules name no calls per round)."""
    rules = run_json.get("rules") or {}
    timeline = rules.get("timeline") or []
    if rules.get("calls_per_round") is None and not any("calls" in e for e in timeline):
        return None
    agents: dict[str, list[dict]] = {}
    for e in timeline:
        agents.setdefault(e.get("agent"), []).append({
            **{k: e.get(k) for k in ROUND_KEYS},
            "calls": [{**{k: c.get(k) for k in CALL_KEYS}, "pos": pos}
                      for c, pos in zip(e.get("calls") or [], places(e.get("calls") or []))]})
    return {"rounds": rules.get("round_budget"), "calls_per_round": rules.get("calls_per_round"), "agents": agents}


def on_clock(r: dict | None, calls: dict[str, list], at: dict[tuple[str, int], tuple[float, float]],
             timed: bool) -> tuple[dict[str, list[dict]], dict[tuple[str, int], dict]]:
    """(agent -> its rounds placed on the board's clock, (agent, k) -> {round, pos} of each call), on the module's
    terms. calls: agent -> its calls in order (run_calls.Call: k and id); at: (agent, k) -> (t, dur),
    transcript_board.clock's; timed: whether that clock is the eval log's. ({}, {}) when r is None."""
    if r is None:
        return {}, {}
    starts = [c.start for rows in calls.values() for c in rows if c.start is not None]
    base = min(starts) if timed and starts else None
    placed: dict[str, list[dict]] = {}
    where: dict[tuple[str, int], dict] = {}
    for agent, entries in r["agents"].items():
        k_of = {c.id: c.k for c in calls.get(agent) or []}
        out, last = [], 0.0
        for e in entries:
            ks = [k_of[c["id"]] for c in e["calls"] if c["id"] in k_of]
            for c in e["calls"]:
                if c["id"] in k_of:
                    where[(agent, k_of[c["id"]])] = {"round": e["round"], "pos": c["pos"]}
            if base is not None and e["t0"] is not None:
                t = max(0.0, e["t0"] - base)
                dur = max(0.0, (e["t1"] if e["t1"] is not None else e["t0"]) - base - t)
            elif ks:
                t = min(at[(agent, k)][0] for k in ks)
                dur = max(sum(at[(agent, k)]) for k in ks) - t
            else:
                t, dur = last, 0.0
            last = t + dur
            out.append({**{k: e[k] for k in ("turn", "round", "n", "end", "ending", "charge", "charged")},
                        "t": t, "dur": dur, "calls": len(e["calls"])})
        placed[agent] = out
    return placed, where
