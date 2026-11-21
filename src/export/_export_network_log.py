"""The network log of one sample as the run keeps it: the monitor's raw rows with an agent, an origin, a tool call and
the agent at the other end, as network.jsonl, and run.json "network".

    block, rows = network_log(view)         # rows None: the run gets no network.jsonl

The network log's sidecar (src/sandbox/network_log/) leaves raw rows, the seat table and the machines in the
sample store. A raw row says which process made which socket call. This module says who that was, from the eval log
alone, the way the access log does (src/export/_export_access_log.py: origin_of, named, holding, call_of), and which
agent stood at the other end. Each row of network.jsonl is one socket call:

    seq, t, op, proto, family, type, machine, netns, pid, uid, user, agent, named_by, candidates when it stays open,
    origin, call_id, exe, cmd, root_cmd, comm, laddr, lport, raddr, rport, result, source, at_start, control,
    peer_agent, peer_pid, peer_origin

- op: bind, listen, connect, accept, send (a UDP datagram to an address, the first per process and destination) or
  close. proto: tcp, udp, unix or raw. A unix socket's path stands in laddr (raddr for a connect), "@" before an
  abstract name; its ports are null. result: ok or the errno's name (EINPROGRESS for a connect that did not wait).
- t: the kernel's time of the call for source ebpf, the read of the tables for procnet. at_start: the socket was
  there when the log began (the harness's own servers, say).
- origin as the access log's: agent, detached, harness, control (the harness's own loopback control with the run's
  nonce) or unknown (a process gone before /proc was read). An unknown row keeps the kernel's uid, so an observer's or
  a seat's own uid on its machine still names its agent (named_by observer or uid); a harness row names nobody.
- peer: the other end, in the same network namespace. A connect's peer is the accept with the mirrored ports, else
  what listens on (or for UDP is bound to) its remote port; an accept's is the connect; a unix connect's is the bind
  or listen at its path, and a unix accept's the connect to its path. peer_agent is that row's agent, peer_pid its
  process and peer_origin its origin.

run.json "network":

    {"status", "method": ebpf | procnet, "fallback": why the tracer did not record, or null, "rows",
     "ops": {op: n}, "origins": {origin: n}, "agents": {agent: {op: n}},
     "cross_agent": [{t, machine, proto, op, from, to, from_pid, to_pid, raddr, rport, result, call_id, named_by}],
     "control": {start: {seen, missing}, end: {seen, missing}}, "gaps": [...], "dropped", "lost", "error"}

    valid    every control's connect and accept seen on every machine that holds a seat, a stopped row, no gap
    partial  the controls seen, but a gap (the tracer died mid-run, a restart, a machine down, a row that failed),
             lost events or dropped rows
    broken   a control missing or no stopped row: the rows are true and an absence means nothing
    absent   the switch was on and no record came back; no file
    off      the switch was off, or the domain has no machine to watch (chess, whose honeypot lives on the host):
             {"status": "off"} alone, no file

cross_agent lists every connect and send whose agent and peer_agent are both named and differ, an observer's
included, so a regulator's peer_shell reaching a worker's server shows. A run older than the switch gets no block.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from src.export._export_access_log import NOBODY, call_of, holding, named, origin_of, seated, tool_calls
from src.export._export_run_blocks import cheatbench, domain
from src.export.eval_sample_view import SampleView
from src.sandbox.network_log.network_monitor import CONTROL_OPS, STORE, ready_row, uncontrolled

NETWORK_LOG = "network.jsonl"
GAP_ROWS = {"trace_failed", "restart", "machine_down", "row_failed", "lost"}
KEPT = ("seq", "op", "proto", "family", "type", "machine", "netns", "pid", "laddr", "lport", "raddr", "rport",
        "result", "source", "comm")
PEER_OPS = {"connect", "accept", "send"}


# --------------------------------------------------------------------------
# who
# --------------------------------------------------------------------------
def by_uid(row: dict, seats: list[dict]) -> dict:
    """An unknown row's agent from the kernel's uid alone: the one observer, else the one seat, of that uid on the
    row's machine."""
    here = [seat for seat in seats if seat["machine"] == row.get("machine") and seat["uid"] == row.get("uid")]
    watching = [seat for seat in here if seat.get("observer")]
    if len(watching) == 1:
        return seated(watching[0], "observer")
    own = [seat for seat in here if not seat.get("observer")]
    if len(own) == 1 and row.get("uid") != 0:
        return seated(own[0], "uid")
    return NOBODY


def attributed(row: dict, seats: list[dict], calls: list) -> dict:
    """One raw net row as a row of network.jsonl, its peer still empty."""
    timed = {**row, "t": row.get("t_kernel", row["t"])}
    origin = origin_of(timed)
    matches = holding(timed, calls) if origin == "agent" else []
    if origin in ("agent", "detached"):
        who = named(timed, seats, matches)
    elif origin == "unknown":
        who = by_uid(timed, seats)
    else:
        who = NOBODY
    out = {"t": timed["t"], **{key: row.get(key) for key in KEPT}, "uid": row.get("uid"), **who, "origin": origin,
           "call_id": call_of(timed, who["agent"], matches), "exe": row.get("exe"), "cmd": row.get("cmd"),
           "root_cmd": (row.get("root") or {}).get("cmd"), "at_start": bool(row.get("at_start")),
           "control": row.get("control"), "peer_agent": None, "peer_pid": None, "peer_origin": None}
    return out


# --------------------------------------------------------------------------
# the other end
# --------------------------------------------------------------------------
def nearest(rows: list[dict], t: float, before: bool = False) -> dict | None:
    """The row nearest in time to t; with before, the latest at or before t (an at_start row counts as before)."""
    if before:
        rows = [row for row in rows if row["at_start"] or row["t"] <= t + 1e-3]
        return max(rows, key=lambda row: row["t"], default=None)
    return min(rows, key=lambda row: abs(row["t"] - t), default=None)


def set_peer(row: dict, peer: dict | None) -> None:
    if peer is not None:
        row.update(peer_agent=peer["agent"], peer_pid=peer["pid"], peer_origin=peer["origin"])


def with_peers(rows: list[dict]) -> list[dict]:
    """Each connect, accept and send with its peer (the module's docstring), in place."""
    accepts, connects, listeners, unix_ends, unix_connects = (defaultdict(list) for _ in range(5))
    for row in rows:
        ns, op, proto = row["netns"], row["op"], row["proto"]
        if proto == "unix":
            if op in ("bind", "listen") and row["laddr"]:
                unix_ends[(ns, row["laddr"])].append(row)
            elif op == "connect" and row["raddr"]:
                unix_connects[(ns, row["raddr"])].append(row)
        elif op == "accept":
            accepts[(ns, row["rport"], row["lport"])].append(row)
        elif op == "connect":
            connects[(ns, row["lport"], row["rport"])].append(row)
        elif op in ("listen", "bind") and row["lport"]:
            listeners[(ns, proto, row["lport"])].append(row)
    for row in rows:
        ns, op, proto = row["netns"], row["op"], row["proto"]
        if op not in PEER_OPS:
            continue
        if proto == "unix":
            if op == "connect":
                set_peer(row, nearest(unix_ends.get((ns, row["raddr"]), []), row["t"], before=True))
            elif op == "accept":
                set_peer(row, nearest(unix_connects.get((ns, row["laddr"]), []), row["t"]))
        elif op == "accept":
            set_peer(row, nearest(connects.get((ns, row["rport"], row["lport"]), []), row["t"]))
        else:
            mirrored = accepts.get((ns, row["lport"], row["rport"]), []) if op == "connect" and row["lport"] else []
            peer = nearest(mirrored, row["t"])
            if peer is None:
                found = listeners.get((ns, proto, row["rport"]), [])
                peer = nearest([r for r in found if r["op"] == "listen"] or found, row["t"], before=True)
            set_peer(row, peer)
    return rows


# --------------------------------------------------------------------------
# run.json "network"
# --------------------------------------------------------------------------
def controls(record: dict) -> dict:
    """Each control's connect and accept rows on the machines that hold a seat, and the machine:op pairs with none."""
    rows, machines = record["rows"], record.get("control_machines", [])
    return {name: {"seen": sum(row.get("control") == name and row.get("op") in CONTROL_OPS for row in rows),
                   "missing": uncontrolled(rows, machines, name)} for name in ("start", "end")}


def gaps_of(rows: list[dict]) -> list[dict]:
    """The rows that say the log lost sight of something. A trace_failed before ready is the method's choice."""
    ready = ready_row(rows)
    after = ready["seq"] if ready else 0
    return [{key: row[key] for key in ("t", "row", "machine", "error", "events") if key in row}
            for row in rows if row["row"] in GAP_ROWS and row["seq"] > after]


def cross_agent(rows: list[dict]) -> list[dict]:
    """The connects and sends from one named agent to another."""
    return [{"t": row["t"], "machine": row["machine"], "proto": row["proto"], "op": row["op"], "from": row["agent"],
             "to": row["peer_agent"], "from_pid": row["pid"], "to_pid": row["peer_pid"], "raddr": row["raddr"],
             "rport": row["rport"], "result": row["result"], "call_id": row["call_id"], "named_by": row["named_by"]}
            for row in rows if row["op"] in ("connect", "send") and row["agent"] and row["peer_agent"]
            and row["agent"] != row["peer_agent"]]


def status_of(record: dict, control: dict, stopped: dict | None, gaps: list[dict]) -> str:
    if ready_row(record["rows"]) is None:
        return "absent"
    if stopped is None or any(c["missing"] for c in control.values()):
        return "broken"
    return "partial" if gaps or stopped.get("dropped") or stopped.get("lost") else "valid"


def network_block(record: dict, rows: list[dict]) -> dict:
    """run.json "network" of a sample the sidecar watched, from the store's record and the attributed rows."""
    raw = record["rows"]
    ready, control, gaps = ready_row(raw), controls(record), gaps_of(raw)
    stopped = next((row for row in raw if row["row"] == "stopped"), None)
    fallback = next((row.get("error") for row in raw if row["row"] == "trace_failed"
                     and (ready is None or row["seq"] < ready["seq"])), None)
    agents: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        if row["agent"]:
            agents[row["agent"]][row["op"]] += 1
    return {"status": status_of(record, control, stopped, gaps), "method": (ready or {}).get("method"),
            "fallback": fallback, "rows": len(rows), "ops": dict(sorted(Counter(r["op"] for r in rows).items())),
            "origins": dict(sorted(Counter(r["origin"] for r in rows).items())),
            "agents": {agent: dict(sorted(ops.items())) for agent, ops in sorted(agents.items())},
            "cross_agent": cross_agent(rows), "control": control, "gaps": gaps,
            "dropped": stopped.get("dropped") if stopped else None, "lost": stopped.get("lost") if stopped else None,
            "error": record.get("error")}


def network_log(view: SampleView) -> tuple[dict | None, list[dict] | None]:
    """(run.json "network", the rows of network.jsonl) of one sample. The block is None for a run older than the
    switch, the rows None for a run with no file (status absent and off)."""
    config = cheatbench(view).get("config") or {}
    switch = config.get("network_log")
    if switch is None:
        return None, None
    bench = domain(view)
    if not switch or (bench is not None and bench.watched is None):
        return {"status": "off"}, None
    record = view.store_get(STORE)
    if record is None or "rows" not in record or ready_row(record["rows"]) is None:
        return {"status": "absent", "error": (record or {}).get("error")}, None
    calls = tool_calls(view)
    rows = with_peers([attributed(row, record["seats"], calls) for row in record["rows"] if row["row"] == "net"])
    return network_block(record, rows), rows
