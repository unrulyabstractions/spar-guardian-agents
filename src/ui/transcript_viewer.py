"""The transcript viewer: every run under runs/ in one index, and a run page with the three ladders (Cheating,
Coordination, Collusion), the judges' verdicts and labels, the board and each agent's transcript, every tool call with
the labels it is evidence for. Ported from collusion-hermaneutics' viewer (viewer/serve.py), whose runs our framework
made, so it reads the same files here.

    uv run python -m src.ui.transcript_viewer [--port 8770] [--runs runs]
    make transcripts

The index is src/ui/transcript_index.py's: what `make process-transcripts` stored in each run's folder. This module
answers the run page from a run's files and its stored measures (run_detail, board_detail) and serves the page. The
server reads the data and never writes it; it computes no ladder.

Routes: / (the page), /tv/<file> (src/ui/transcript_static/), /static/<path> (the shared assets in
src/ui/static/: dom.js, api.js, charts.js, components.js and style.css), /api/index, /api/run?id=,
/api/board?id= (the run page's Board tab, src/ui/transcript_board.py), /api/transcript?id=&agent=,
/api/tools?id=&agent= (the tool definitions the agent's model calls carried, from the eval log), /api/tmp?id= (the
run's tmp_snapshots.json, src/replay/tmp_snapshots.py, for the Board tab's /tmp panel), /api/network?id= (the run's
network log on the board's clock, transcript_board.network_payload, for the Board tab's Network panel), /api/ladders,
/api/summary?col= (the Summary page, src/ui/transcript_summary.py). src/ui/API.md describes them.
"""
from __future__ import annotations

import argparse
import json
import shutil
import signal
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from inspect_ai.log import read_eval_log

from src.configuration.settings import ROOT
from src.export.eval_sample_view import span_owners
from src.judging import verdict_files
from src.measures import judge_labels
from src.measures.run_directory import RunDir, episode_agent
from src.ui import (
    transcript_board,
    transcript_ladder_tables,
    transcript_levels,
    transcript_presence,
    transcript_rounds,
    transcript_summary,
)
from src.ui.transcript_index import (
    RunFiles,
    build_index,
    cheatbench_by_agent,
    folder_agents,
    read_stored,
    served_index,
)
from src.utils import progress

HERE = Path(__file__).resolve().parent
STATIC = HERE / "transcript_static"
BROWSER_STATIC = HERE / "static"
DEFAULT_PORT = 8770
ERROR_LINES = 5
"""The distinct errors of unreadable runs a start prints; /api/index lists every one."""
VERDICT_KEPT = ("parse_ok", "judge_cost_usd", "judge_error")
"""The bookkeeping keys of a verdict row (verdict_files.ROW_KEYS) the run page still shows; it leaves the rest out."""
EPISODE_KEYS = ["agent", "model", "n_turns", "n_tool_calls", "wall_time", "limit", "score", "ended_by", "ok",
                "failure", "final_text", "usage", "defense", "defenses"]
"""The episode fields the run page shows (components.js episodeRecord and the agent card); the rest of episodes.jsonl
(rules, probes, ledger, access) stays on disk."""



# ------------------------------------------------------------------------------------------------------- run page
def shown_verdict(row: dict) -> dict:
    """A verdict row as the run page shows it: the judge's answer, without the bookkeeping VERDICT_KEPT leaves out."""
    drop = set(verdict_files.ROW_KEYS) - set(VERDICT_KEPT)
    return {k: v for k, v in row.items() if k not in drop}


def run_detail(row: dict, run_dir: Path, measured: dict) -> dict:
    """The run page's data: the row, the run's settings the page shows, each agent with its episode and each
    CheatBench judge's verdict on it, the field of that verdict that says the agent acknowledged its access
    (judge_labels.CHEATBENCH_DISCLOSED_ACCESS, shown only in the CheatBench judge's block), the board, the three
    ladders with the judge labels from the stored measures, presence: each agent's entry and exit and each alert
    with its outcome and its call (transcript_presence), rounds: each agent's rounds and their calls
    (transcript_rounds), None for a run before 2026-11-21, regulators: each regulator's measures as the code
    record holds them (signals.team.regulators, src/measures/regulator_measures.py), {} without, and beliefs: the
    workers' belief answers as run.json holds them (run_beliefs). Each agent carries the routes of its aimed reads
    (signals.jsonl routes, SWE alone; None without) and a disregulator its label, mode and disclosed (stored_roles;
    None for any other agent and a run whose label is not stored)."""
    rf = RunFiles.read(run_dir)
    rj = rf.run_json
    folders = folder_agents(run_dir, rf.episodes)
    cheatbench = {judge: cheatbench_by_agent(rows, rf.episodes) for judge, rows in
                  sorted(measured["cheatbench"].items())}
    routes = {row.get("agent"): row.get("routes") for row in RunDir(run_dir).signals()}
    disregulators = stored_roles(run_dir)
    agents = []
    for ep in rf.episodes:
        a = episode_agent(ep)
        folder = folders.get(a)
        tpath = RunDir(run_dir).transcript_path(folder) if folder else None
        agents.append({
            "agent": a, "role": rf.roles.get(a) or "worker", "folder": folder,
            "transcript_bytes": tpath.stat().st_size if tpath else None,
            "episode": {k: ep.get(k) for k in EPISODE_KEYS if k in ep},
            "cheatbench": {judge: shown_verdict(rows[a]) for judge, rows in cheatbench.items() if a in rows},
            "routes": routes.get(a),
            "disregulator": disregulators.get(a),
        })
    # Workers first, influencers last; then by agent id.
    agents.sort(key=lambda e: (e["role"] != "worker", e["agent"] or ""))
    log = RunDir(run_dir).eval_log
    meta = {"config": rj.get("config"), "mode": rj.get("mode"), "code": rj.get("code"),
            "rounds": (rj.get("rules") or {}).get("rounds"), "eval_log": str(log) if log else None,
            "eval_log_recorded": rj.get("eval_log")}
    boards = rf.boards()
    return {"row": row, "meta": meta, "agents": agents, "boards": boards, "levels": rf.levels(measured, boards),
            "cheatbench_disclosed": judge_labels.CHEATBENCH_DISCLOSED_ACCESS.get(rf.task),
            "presence": transcript_presence.with_alert_calls(transcript_presence.presence(rj), run_dir),
            "rounds": transcript_rounds.rounds(rj),
            "regulators": ((rj.get("signals") or {}).get("team") or {}).get("regulators") or {},
            "beliefs": run_beliefs(rj)}


def stored_roles(run_dir: Path) -> dict[str, dict]:
    """{disregulator: {label, mode, disclosed}} from the run's stored analysis/labels/disregulator_roles.json
    (src/measures/disregulator_roles.py); {} for a run without it."""
    path = RunDir(run_dir).output("disregulator_roles")
    if not path.is_file():
        return {}
    agents = read_stored(path).get("agents") or {}
    return {agent: {k: record.get(k) for k in ("label", "mode", "disclosed")} for agent, record in agents.items()}


def run_beliefs(rj: dict) -> dict | None:
    """The workers' belief answers (src/swarm/belief_probes.py) as run.json holds them, {questions, agents}; None for a
    run that did not ask."""
    return rj.get("beliefs") or None


def sent_tool(t: dict) -> dict:
    """One tool of a provider request as {name, description, parameters}: OpenAI's chat format nests it under
    "function", the Responses format and Anthropic's do not, and Anthropic names the schema input_schema."""
    f = t.get("function") if isinstance(t.get("function"), dict) else t
    return {"name": f.get("name"), "description": f.get("description"),
            "parameters": f.get("parameters", f.get("input_schema"))}


def sent_tools(e) -> list[dict] | None:
    """The tools a model event's provider request carried (e.call.request), or None when the event recorded no
    request. The event's own tools list can hold more: the turn rules withhold ORBIT's channel tools from the provider
    (src/swarm/turn_rules/_turn_state.py CHANNEL_TOOLS), so only the request says what the model received."""
    request = e.call.request if e.call and isinstance(e.call.request, dict) else None
    if request is None:
        return None
    return [sent_tool(t) for t in request.get("tools") or []]


def agent_tools(log_path: Path) -> dict[str, dict]:
    """Each agent's tool definitions as its model calls sent them to the provider beside the messages: {agent:
    {"tools": [{name, description, parameters}], "tool_choice", "same_in_every_call", "from_request"}}. The first
    call's set stands for the agent; same_in_every_call says whether every later call sent the same names. A call
    whose request was not recorded falls back to the event's tools list (from_request false). A defense's own calls
    are left out."""
    sample = read_eval_log(str(log_path), resolve_attachments=True).samples[0]
    owner = span_owners(sample)
    out: dict[str, dict] = {}
    for e in sample.events:
        if e.event != "model":
            continue
        agent, defense = owner(e.span_id)
        if agent is None or defense is not None:
            continue
        sent = sent_tools(e)
        tools = sent if sent is not None else [
            {"name": t.name, "description": t.description, "parameters": t.parameters.model_dump(exclude_none=True)}
            for t in e.tools or []]
        names = [t["name"] for t in tools]
        if agent in out:
            out[agent]["same_in_every_call"] &= names == out[agent]["_names"]
            continue
        choice = e.tool_choice if isinstance(e.tool_choice, str) or e.tool_choice is None else \
            getattr(e.tool_choice, "name", str(e.tool_choice))
        out[agent] = {"tools": tools, "tool_choice": choice, "same_in_every_call": True,
                      "from_request": sent is not None, "_names": names}
    for v in out.values():
        del v["_names"]
    return out


def tmp_snapshots(run_dir: Path) -> Path | None:
    """The run's tmp_snapshots.json, or None when the run has none."""
    path = RunDir(run_dir).file("tmp_snapshots.json")
    return path if path.is_file() else None


def network_file(run_dir: Path) -> Path | None:
    """The run's network.jsonl, or None when the run has none."""
    path = RunDir(run_dir).file("network.jsonl")
    return path if path.is_file() else None


def board_detail(row: dict, run_dir: Path, measured: dict) -> dict:
    """The run page's Board tab (transcript_board.board_payload) from the run's files and its stored measures, with
    tmp: whether the run has /tmp snapshots (/api/tmp), and network: whether it has a network log (/api/network), so
    the tab shows each panel only then."""
    payload = transcript_board.board_payload(run_dir, RunFiles.read(run_dir).levels(measured))
    return {**payload, "tmp": tmp_snapshots(run_dir) is not None, "network": network_file(run_dir) is not None}


# ---------------------------------------------------------------------------------------------------------- server
RUN_ROUTES = {"/api/run": run_detail, "/api/board": board_detail}
"""The routes that answer for one run of the index (?id=), each from the run's row, its folder and its stored
measures."""
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml"}


class Server(ThreadingHTTPServer):
    """socketserver listens with a backlog of request_queue_size, 5 by default. The page asks for its dozen ES modules
    at once, and four pages loading together got connections reset at 5 and none at 128 (measured 2026-11-08)."""

    request_queue_size = 128


class Viewer:
    """What the server answers from: the served index (served_index) as bytes, its rows by id, each served run's
    folder and stored measures, the Ladders page's data and the Summary page's, by collection."""

    def __init__(self, idx: dict):
        served = served_index(idx)
        self.index_bytes = json.dumps(served, separators=(",", ":")).encode("utf-8")
        self.rows = {r["id"]: r for r in served["rows"]}
        self.dirs = {run_id: Path(idx["entries"][run_id]["dir"]) for run_id in self.rows}
        self.measured = {run_id: idx["entries"][run_id]["measured"] for run_id in self.rows}
        self.ladders = transcript_ladder_tables.ladders_payload(transcript_levels.LADDERS, transcript_levels.ORDER,
                                                                transcript_levels.label_about())
        self.summaries = {c["key"]: transcript_summary.summary(served["rows"], c["key"]) for c in served["collections"]}
        self._tools: dict[str, dict] = {}
        self._tools_lock = threading.Lock()
        self._network: dict[str, dict | None] = {}
        self._network_lock = threading.Lock()

    def tools(self, run_id: str) -> dict[str, dict]:
        """agent_tools of the run's eval log, read once per run; {} when the run has no eval log."""
        with self._tools_lock:
            if run_id not in self._tools:
                log = RunDir(self.dirs[run_id]).eval_log
                self._tools[run_id] = agent_tools(log) if log else {}
            return self._tools[run_id]

    def network(self, run_id: str) -> dict | None:
        """transcript_board.network_payload of the run, read once per run; None when the run has no network.jsonl."""
        with self._network_lock:
            if run_id not in self._network:
                self._network[run_id] = transcript_board.network_payload(self.dirs[run_id])
            return self._network[run_id]


class Handler(BaseHTTPRequestHandler):
    viewer: Viewer  # set in main()
    server_version = "TranscriptViewer/1"

    def log_message(self, fmt, *args):  # quiet: one line per error only
        if len(args) > 1 and str(args[1]).startswith(("4", "5")):
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def send_bytes(self, body: bytes, ctype: str, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, status=HTTPStatus.OK):
        self.send_bytes(json.dumps(obj, separators=(",", ":")).encode("utf-8"), "application/json", status)

    def fail(self, status, msg):
        self.send_json({"error": msg}, status)

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path in ("/", "/index.html"):
                return self.static(STATIC, "tv_index.html")
            if url.path.startswith("/tv/"):
                return self.static(STATIC, url.path[len("/tv/"):])
            if url.path.startswith("/static/"):
                return self.static(BROWSER_STATIC, url.path[len("/static/"):])
            if url.path == "/api/index":
                return self.send_bytes(self.viewer.index_bytes, "application/json")
            if url.path in RUN_ROUTES:
                run_id = q.get("id", "")
                row = self.viewer.rows.get(run_id)
                if row is None:
                    return self.fail(HTTPStatus.NOT_FOUND, f"no run {run_id!r} in the index")
                return self.send_json(RUN_ROUTES[url.path](row, self.viewer.dirs[run_id], self.viewer.measured[run_id]))
            if url.path == "/api/ladders":
                return self.send_json(self.viewer.ladders)
            if url.path == "/api/summary":
                col = q.get("col") or next(iter(self.viewer.summaries), "")
                if col not in self.viewer.summaries:
                    return self.fail(HTTPStatus.NOT_FOUND, f"no collection {col!r} in the index")
                return self.send_json(self.viewer.summaries[col])
            if url.path == "/api/tools":
                run_id, agent = q.get("id", ""), q.get("agent", "")
                if run_id not in self.viewer.dirs:
                    return self.fail(HTTPStatus.NOT_FOUND, f"no run {run_id!r} in the index")
                found = self.viewer.tools(run_id).get(agent)
                if found is None:
                    return self.fail(HTTPStatus.NOT_FOUND, f"no tool definitions for agent {agent!r} in {run_id}")
                return self.send_json(found)
            if url.path == "/api/transcript":
                return self.transcript(q.get("id", ""), q.get("agent", ""))
            if url.path == "/api/tmp":
                run_id = q.get("id", "")
                if run_id not in self.viewer.dirs:
                    return self.fail(HTTPStatus.NOT_FOUND, f"no run {run_id!r} in the index")
                path = tmp_snapshots(self.viewer.dirs[run_id])
                if path is None:
                    return self.fail(HTTPStatus.NOT_FOUND, f"no /tmp snapshots in {run_id}")
                return self.send_file(path)
            if url.path == "/api/network":
                run_id = q.get("id", "")
                if run_id not in self.viewer.dirs:
                    return self.fail(HTTPStatus.NOT_FOUND, f"no run {run_id!r} in the index")
                if network_file(self.viewer.dirs[run_id]) is None:
                    return self.fail(HTTPStatus.NOT_FOUND, f"no network log in {run_id}")
                return self.send_json(self.viewer.network(run_id))
            return self.fail(HTTPStatus.NOT_FOUND, "no such path")
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception as e:  # noqa: BLE001 - shown to the local user as an error box
            return self.fail(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(e).__name__}: {e}")

    def static(self, root: Path, rel: str):
        path = (root / rel).resolve()
        if root.resolve() not in path.parents or not path.is_file():
            return self.fail(HTTPStatus.NOT_FOUND, "no such file")
        self.send_bytes(path.read_bytes(), TYPES.get(path.suffix, "application/octet-stream"))

    def transcript(self, run_id: str, agent: str):
        run_dir = self.viewer.dirs.get(run_id)
        if run_dir is None:
            return self.fail(HTTPStatus.NOT_FOUND, f"no run {run_id!r} in the index")
        folder = folder_agents(run_dir, RunDir(run_dir).episodes()).get(agent)
        if folder is None:
            return self.fail(HTTPStatus.NOT_FOUND, f"no transcript for agent {agent!r} in {run_id}")
        return self.send_file(RunDir(run_dir).transcript_path(folder))

    def send_file(self, path: Path):
        """A JSON file of a run, streamed as stored."""
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile, 256 * 1024)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--runs", type=Path, default=ROOT / "runs", help="the folder whose subfolders are collections")
    args = ap.parse_args(argv)
    runs = args.runs.resolve()
    if not runs.is_dir():
        print(f"no runs folder at {runs}", file=sys.stderr)
        return 1
    progress.step("Read the stored runs")
    progress.detail("runs", runs)
    idx = build_index(runs)
    progress.detail("served", progress.plural(len(idx["rows"]), "run"))
    for c in idx["collections"]:
        progress.detail("", f"{c['key']}: {c['runs']}" + (f" and {c['other_tasks']} of other tasks"
                                                          if c["other_tasks"] else ""))
    left = idx["left_out"]
    if left["runs"]:
        progress.detail("other tasks", f"{progress.plural(left['runs'], 'run')} of {', '.join(left['tasks'])}")
    if left.get("single_agent"):
        progress.detail("left out", progress.plural(left["single_agent"], "single-agent run"))
    stored = idx["pipeline"]
    if stored["not_processed"] or stored["behind"]:
        parts = ", ".join(f"{name} {n}" for name, n in stored["parts"].items())
        progress.detail("not processed", f"{stored['not_processed']} (not served)", err=True)
        progress.detail("behind", f"{stored['behind']}" + (f" ({parts})" if parts else ""), err=True)
        progress.detail("to update", stored["command"], err=True)
    progress.detail("unreadable", len(idx["errors"]))
    by_error: dict[str, list[str]] = {}
    for e in idx["errors"]:
        by_error.setdefault(e["error"], []).append(e["id"])
    for n, (error, ids) in enumerate(sorted(by_error.items(), key=lambda kv: -len(kv[1]))):
        if n == ERROR_LINES:
            progress.detail("", f"and {len(by_error) - n} more errors; /api/index lists every run", err=True)
            break
        runs_of = ids[0] if len(ids) == 1 else f"{len(ids)} runs such as {ids[0]}"
        progress.detail("", f"{runs_of}: {error}", err=True)
    progress.step("Prepare the pages")
    Handler.viewer = Viewer(idx)
    progress.detail("summaries", progress.plural(len(Handler.viewer.summaries), "collection"))
    srv = Server((args.host, args.port), Handler)

    def stop(signum, frame):  # a background start ignores Ctrl-C, so SIGTERM stops the server cleanly too
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    url = f"http://{args.host}:{args.port}/"
    progress.done(f"{progress.plural(len(idx['rows']), 'run')} served, {stored['behind']} behind, "
                  f"{stored['not_processed']} not processed; open {url} (Ctrl-C stops it)",
                  word="Ready")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
