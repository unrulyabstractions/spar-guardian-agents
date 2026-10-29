"""HTTP server for the results browser. Stdlib only, plus inspect_ai for eval logs.

Start it with `python -m src.ui`. The server routes requests and writes JSON. The run readers live in
run_store.py (`Store`) and the timeline in timeline_builder.py.

    serve(Path("runs"), "127.0.0.1", 8765)

Endpoints (all JSON unless noted; see API.md):
    GET /                                       index.html
    GET /static/<path>                          UI assets (index.html, style.css, app/<module>.js)
    GET /api/runs                               every run with its episodes, verdicts, config and rules facts
    GET /api/summary                            cross-run aggregates for the overview charts
    GET /api/runs/<run>                         run.json, episodes, verdicts, communication, defense evidence
    GET /api/runs/<run>/transcript/<episode>    the ATIF transcript plus the episode record and verdict
    GET /api/runs/<run>/sample                  sample facts from the eval log (scores, probe, limit, error)
    GET /api/runs/<run>/context                 index of every model call in the eval log (agents and defenses)
    GET /api/runs/<run>/context/<i>             one model call: exact provider input, output, usage
    GET /api/runs/<run>/timeline                tool calls, model calls, interventions and board messages in time
    GET /api/runs/<run>/tools/<tool>/<file>     one file an analysis tool wrote (tools/, runs/<run>/tools/<tool>/)
"""
from __future__ import annotations

import json
import mimetypes
import sys
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from src.ui.run_store import Store
from src.ui.timeline_builder import timeline

STATIC = Path(__file__).resolve().parent / "static"


def _content_type(path: Path) -> str:
    ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    if ctype.startswith("text/") or ctype.endswith("javascript"):
        return f"{ctype}; charset=utf-8"
    return ctype


def _static_file(parts: list[str]) -> Path | None:
    """A file under static/ by its path segments, or None for a path that resolves outside static/."""
    path = STATIC.joinpath(*parts).resolve()
    return path if path.is_relative_to(STATIC) else None


class Handler(SimpleHTTPRequestHandler):
    store: Store = None  # set by serve()

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send_body(self, body: bytes, ctype: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, default=str).encode()
        self._send_body(body, "application/json; charset=utf-8", status)

    def _send_file(self, path: Path):
        if not path.is_file():
            return self._send_json({"error": "not found"}, 404)
        self._send_body(path.read_bytes(), _content_type(path))

    def do_GET(self):
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.split("/") if p]
        try:
            return self._route(parts)
        except KeyError as ex:
            return self._send_json({"error": f"not found: {ex}"}, 404)
        except Exception as ex:  # noqa: BLE001 - report to the client, keep serving
            error = {"error": f"{type(ex).__name__}: {ex}", "traceback": traceback.format_exc()[-2000:]}
            return self._send_json(error, 500)

    def _route(self, parts: list[str]):
        if not parts or parts == ["index.html"]:
            return self._send_file(STATIC / "index.html")
        if parts[0] == "static" and len(parts) >= 2:
            path = _static_file(parts[1:])
            if path is None:
                return self._send_json({"error": "not found"}, 404)
            return self._send_file(path)
        if parts[0] != "api":
            return self._send_json({"error": "not found"}, 404)
        store = self.store
        if parts[1:] == ["runs"]:
            return self._send_json([store.run_summary(n) for n in store.run_names()])
        if parts[1:] == ["summary"]:
            return self._send_json(store.summary())
        if len(parts) >= 3 and parts[1] == "runs":
            return self._route_run(parts[2], parts[3:])
        return self._send_json({"error": "not found"}, 404)

    def _route_run(self, name: str, rest: list[str]):
        store = self.store
        if not rest:
            return self._send_json(store.run_detail(name))
        if rest[0] == "transcript" and len(rest) == 2:
            return self._send_json(store.transcript(name, rest[1]))
        if rest == ["sample"]:
            return self._send_json(store.sample(name))
        if rest == ["context"]:
            return self._send_json(store.context_list(name))
        if rest == ["timeline"]:
            return self._send_json(timeline(store, name))
        if rest[0] == "context" and len(rest) == 2 and rest[1].isdigit():
            return self._send_json(store.context_call(name, int(rest[1])))
        if rest[0] == "tools" and len(rest) >= 3:
            try:
                path = store.tool_file(name, rest[1], rest[2:])
            except ValueError as ex:
                return self._send_json({"error": str(ex)}, 400)
            return self._send_file(path)
        return self._send_json({"error": "not found"}, 404)


def serve(runs_dir: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    Handler.store = Store(runs_dir.resolve())
    httpd = ThreadingHTTPServer((host, port), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
