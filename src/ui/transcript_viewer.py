"""The transcript viewer: every run under runs/ in one index, and a run page with the three ladders (Cheating,
Coordination, Collusion), the judges' verdicts and labels, the board and each agent's transcript, every tool call with
the labels it is evidence for. Ported from collusion-hermaneutics' viewer (viewer/serve.py), whose runs our framework
made, so it reads the same files here.

    uv run python -m src.ui.transcript_viewer [--port 8770] [--rebuild] [--runs runs] [--cache tmp/...]
    make transcripts

The index and its cache are src/ui/transcript_index.py's. This module answers the run page from a run's files and its
cached measures (run_detail, board_detail) and serves the page. The server reads the data and never writes it.

Routes: / (the page), /tv/<file> (src/ui/transcript_static/), /static/<path> (the results browser's assets in
src/ui/static/, whose dom.js, charts.js, components.js and style.css the page reuses), /api/index, /api/run?id=,
/api/board?id= (the run page's Board tab, src/ui/transcript_board.py), /api/transcript?id=&agent=, /api/ladders.
src/ui/API.md describes them.
"""
from __future__ import annotations

import argparse
import json
import shutil
import signal
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from src.configuration.settings import ROOT
from src.judging import verdict_files
from src.measures import judge_labels
from src.measures.run_directory import RunDir, episode_agent
from src.ui import transcript_board, transcript_ladder_tables, transcript_levels
from src.ui.transcript_index import CACHE, RunFiles, cheatbench_by_agent, folder_agents, load_or_build, served_index
from src.utils.json_files import read_jsonl

HERE = Path(__file__).resolve().parent
STATIC = HERE / "transcript_static"
BROWSER_STATIC = HERE / "static"
DEFAULT_PORT = 8770
VERDICT_KEPT = ("parse_ok", "judge_cost_usd", "judge_error")
"""The bookkeeping keys of a verdict row (verdict_files.ROW_KEYS) the run page still shows; it leaves the rest out."""
EPISODE_KEYS = ["orbit_agent", "model", "n_turns", "n_tool_calls", "wall_time", "limit", "score", "ended_by", "ok",
                "failure", "final_text", "usage", "defense", "defenses"]
"""The episode fields the run page shows (components.js episodeRecord and the agent card); the rest of episodes.jsonl
(rules, probes, ledger, access) stays on disk."""


# ------------------------------------------------------------------------------------------------------- run page
def eval_log_of(run_dir: Path) -> Path | None:
    """The run's eval log: the one in its own eval/ folder (a manifest collection's runs keep it there), else where
    RunDir finds it."""
    own = sorted((run_dir / "eval").glob("*.eval")) if (run_dir / "eval").is_dir() else []
    return own[0] if own else RunDir(run_dir).eval_log


def shown_verdict(row: dict) -> dict:
    """A verdict row as the run page shows it: the judge's answer, without the bookkeeping VERDICT_KEPT leaves out."""
    drop = set(verdict_files.ROW_KEYS) - set(VERDICT_KEPT)
    return {k: v for k, v in row.items() if k not in drop}


def run_detail(row: dict, run_dir: Path, measured: dict) -> dict:
    """The run page's data: the row, the run's settings the page shows, each agent with its episode and each
    CheatBench judge's verdict on it, the field of that verdict that says the agent acknowledged its access
    (judge_labels.CHEATBENCH_DISCLOSED_ACCESS, shown only in the CheatBench judge's block), the board, and the three
    ladders with the judge labels from the cached measures."""
    rf = RunFiles.read(run_dir)
    rj = rf.run_json
    folders = folder_agents(run_dir, rf.episodes)
    cheatbench = {judge: cheatbench_by_agent(rows, rf.episodes) for judge, rows in
                  sorted(measured["cheatbench"].items())}
    agents = []
    for ep in rf.episodes:
        a = episode_agent(ep)
        folder = folders.get(a)
        tpath = run_dir / "trajectories" / folder / "transcript.json" if folder else None
        agents.append({
            "agent": a, "role": rf.roles.get(a) or "worker", "folder": folder,
            "transcript_bytes": tpath.stat().st_size if tpath else None,
            "episode": {k: ep.get(k) for k in EPISODE_KEYS if k in ep},
            "cheatbench": {judge: shown_verdict(rows[a]) for judge, rows in cheatbench.items() if a in rows},
        })
    # Workers first, influencers last, as the results browser orders its lanes; then by agent id.
    agents.sort(key=lambda e: (e["role"] != "worker", e["agent"] or ""))
    log = eval_log_of(run_dir)
    meta = {"config": rj.get("config"), "mode": rj.get("mode"), "code": rj.get("code"),
            "rounds": (rj.get("rules") or {}).get("rounds"), "eval_log": str(log) if log else None,
            "eval_log_recorded": rj.get("eval_log")}
    boards = rf.boards()
    return {"row": row, "meta": meta, "agents": agents, "boards": boards, "levels": rf.levels(measured, boards),
            "cheatbench_disclosed": judge_labels.CHEATBENCH_DISCLOSED_ACCESS.get(rf.task)}


def board_detail(row: dict, run_dir: Path, measured: dict) -> dict:
    """The run page's Board tab (transcript_board.board_payload) from the run's files and its cached measures."""
    return transcript_board.board_payload(run_dir, RunFiles.read(run_dir).levels(measured))


# ---------------------------------------------------------------------------------------------------------- server
RUN_ROUTES = {"/api/run": run_detail, "/api/board": board_detail}
"""The routes that answer for one run of the index (?id=), each from the run's row, its folder and its cached
measures."""
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml"}


class Server(ThreadingHTTPServer):
    """socketserver listens with a backlog of request_queue_size, 5 by default. The page asks for its dozen ES modules
    at once, and four pages loading together got connections reset at 5 and none at 128 (measured 2026-11-08)."""

    request_queue_size = 128


class Viewer:
    """What the server answers from: the served index (served_index) as bytes, its rows by id, each served run's
    folder and cached measures, and the Ladders page's data."""

    def __init__(self, idx: dict):
        served = served_index(idx)
        self.index_bytes = json.dumps(served, separators=(",", ":")).encode("utf-8")
        self.rows = {r["id"]: r for r in served["rows"]}
        self.dirs = {run_id: Path(idx["entries"][run_id]["dir"]) for run_id in self.rows}
        self.measured = {run_id: idx["entries"][run_id]["measured"] for run_id in self.rows}
        self.ladders = transcript_ladder_tables.ladders_payload(transcript_levels.LADDERS, transcript_levels.ORDER,
                                                                transcript_levels.label_about())


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
            if url.path == "/api/transcript":
                return self.transcript(q.get("id", ""), q.get("agent", ""))
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
        folder = folder_agents(run_dir, read_jsonl(run_dir / "episodes.jsonl")).get(agent)
        if folder is None:
            return self.fail(HTTPStatus.NOT_FOUND, f"no transcript for agent {agent!r} in {run_id}")
        path = run_dir / "trajectories" / folder / "transcript.json"
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
    ap.add_argument("--rebuild", action="store_true", help="measure every run again, whatever the cache holds")
    ap.add_argument("--runs", type=Path, default=ROOT / "runs", help="the folder whose subfolders are collections")
    ap.add_argument("--cache", type=Path, default=CACHE, help="the index cache to read and write")
    args = ap.parse_args(argv)
    runs = args.runs.resolve()
    if not runs.is_dir():
        print(f"no runs folder at {runs}", file=sys.stderr)
        return 1
    t0 = time.time()
    idx = load_or_build(runs, args.rebuild, args.cache)
    Handler.viewer = Viewer(idx)
    counts = ", ".join(f"{c['key']} {c['runs']}" for c in idx["collections"])
    left_out = f"; {idx['left_out']['runs']} valid runs of {', '.join(idx['left_out']['tasks'])} left out" \
        if idx["left_out"]["runs"] else ""
    print(f"index: {len(idx['rows'])} valid runs ({counts}){left_out}; {len(idx['errors'])} unreadable; "
          f"{idx['measured']} runs measured in {idx['build_seconds']} s; ready in {time.time() - t0:.2f} s", flush=True)
    for e in idx["errors"]:
        print(f"  unreadable {e['id']}: {e['error']}", file=sys.stderr)
    srv = Server((args.host, args.port), Handler)

    def stop(signum, frame):  # a background start ignores Ctrl-C, so SIGTERM stops the server cleanly too
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    print(f"transcript viewer: http://{args.host}:{args.port}/  (runs: {runs})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
