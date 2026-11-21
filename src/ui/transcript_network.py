"""The Board tab's Network panel: the run's network log (network.jsonl and run.json "network",
src/export/_export_network_log.py) on the board's clock, for the transcript viewer (transcript_board.board_payload).

    view = network_view(run_dir, calls, at)     # None for a run with no network.jsonl
    view["block"]                               # run.json "network" as written
    view["listeners"][0]   # {t, until, machine, agent, proto, laddr, lport, call: {agent, k} | None, origin}
    view["calls"][0]       # {t, op, proto, machine, agent, peer_agent, raddr, rport, result, cross, origin,
                           #  call: {agent, k} | None}

A row's t is on the board's clock: on an eval-log clock its own time less the board's start (the first call's start,
transcript_board.clock); on a call-order clock its call's place when the row names a tool call the board knows, else
None. call names that tool call, for a link. The control's rows stay out. A listener is a listen, or a bind that no
listen followed on the same process and port (a UDP socket, a unix path); until is the t of its socket's close, or None
while it stays open. The calls are the connects, accepts and sends, in time order; cross marks a connect or a send from
one named agent to another (the block's cross_agent). Closes are read for the listeners' ends and not listed.
"""
from __future__ import annotations

from pathlib import Path

from src.export._export_network_log import NETWORK_LOG
from src.utils.json_files import read_json, read_jsonl

LISTEN_OPS = ("listen", "bind")
CALL_OPS = ("connect", "accept", "send")
MAX_CALLS = 2000
"""The most connects, accepts and sends the panel gets; the block's counts hold every row."""


def board_time(row: dict, place: dict, start: float | None) -> float | None:
    """The row's t on the board's clock (the module's docstring)."""
    if start is not None and row.get("t") is not None:
        return round(row["t"] - start, 3)
    found = place.get(row.get("call_id"))
    return found[1] if found is not None else None


def socket_key(row: dict) -> tuple:
    return row.get("netns"), row.get("pid"), row.get("proto"), row.get("lport"), row.get("laddr")


def listeners(rows: list[dict], timed: list[float | None]) -> list[dict]:
    """The listens, and the binds no listen followed, each with the t of its socket's close."""
    listened = {socket_key(row) for row in rows if row["op"] == "listen"}
    closes: dict[tuple, list[float | None]] = {}
    for row, t in zip(rows, timed):
        if row["op"] == "close" and not row.get("rport"):
            closes.setdefault(socket_key(row), []).append(t)
    out = []
    for row, t in zip(rows, timed):
        if row["op"] not in LISTEN_OPS or row.get("result") != "ok":
            continue
        if row["op"] == "bind" and socket_key(row) in listened:
            continue
        ends = [c for c in closes.get(socket_key(row), []) if c is not None and (t is None or c >= t)]
        out.append({"t": t, "until": min(ends) if ends else None, "machine": row.get("machine"),
                    "agent": row.get("agent"), "proto": row.get("proto"), "laddr": row.get("laddr"),
                    "lport": row.get("lport"), "origin": row.get("origin"), "at_start": row.get("at_start", False),
                    "call": row.get("_call")})
    return out


def network_view(run_dir: Path, calls: dict[str, tuple[str, int]], at: dict[tuple[str, int], tuple[float, float]],
                 start: float | None = None) -> dict | None:
    """The panel's data (the module's docstring). calls: tool call id -> (agent, k); at: (agent, k) -> (t, dur) on
    the board's clock; start: the board clock's zero in seconds since the epoch, None on a call-order clock."""
    path = Path(run_dir) / NETWORK_LOG
    if not path.is_file():
        return None
    block = (read_json(Path(run_dir) / "run.json") or {}).get("network") or {}
    place = {cid: (agent_k, at[agent_k][0]) for cid, agent_k in calls.items() if agent_k in at}
    rows = [row for row in read_jsonl(path) if row.get("origin") != "control"]
    for row in rows:
        found = place.get(row.get("call_id"))
        row["_call"] = {"agent": found[0][0], "k": found[0][1]} if found else None
    timed = [board_time(row, place, start) for row in rows]
    listed = [{"t": t, "op": row["op"], "proto": row.get("proto"), "machine": row.get("machine"),
               "agent": row.get("agent"), "peer_agent": row.get("peer_agent"), "raddr": row.get("raddr"),
               "rport": row.get("rport"), "result": row.get("result"), "origin": row.get("origin"),
               "cross": row["op"] in ("connect", "send") and bool(row.get("agent")) and bool(row.get("peer_agent"))
               and row.get("agent") != row.get("peer_agent"), "call": row["_call"]}
              for row, t in zip(rows, timed) if row["op"] in CALL_OPS]
    listed.sort(key=lambda c: (c["t"] is None, c["t"] or 0.0))
    return {"block": block, "listeners": listeners(rows, timed), "calls": listed[:MAX_CALLS],
            "cut": max(0, len(listed) - MAX_CALLS)}
