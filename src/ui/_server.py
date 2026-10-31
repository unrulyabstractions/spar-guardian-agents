"""HTTP server of the results browser: the page and its assets under static/, and the JSON API (see API.md).

The server routes requests to the run readers (_run_store.Store) and the eval-log views (_eval_log_views.LogViews) and
writes their answers as JSON. It serves files only from static/ and from a run's analysis tool directories, each
through safe_join.

    serve(Path("runs"), port=8765)
"""
from __future__ import annotations

import json
import mimetypes
import re
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from src.ui._eval_log_views import LogViews
from src.ui._run_store import Store, safe_part

STATIC = Path(__file__).resolve().parent / "static"
# The file types the page may fetch from an analysis tool's directory (runs/<study>/<run>/tools/<tool>/).
TOOL_FILE_SUFFIXES = frozenset({".png", ".csv", ".html", ".gexf", ".json", ".yaml"})


def safe_join(base: Path, parts: list[str]) -> Path | None:
    """The file under `base` that the path segments name, or None when a segment is unsafe, the path leaves `base`
    or no file is there."""
    if not parts or not all(safe_part(p) for p in parts):
        return None
    base = base.resolve()
    path = base.joinpath(*parts).resolve()
    return path if path.is_relative_to(base) and path.is_file() else None


def _tool_file(store: Store, run: str, tool: str, *parts: str) -> Path | None:
    path = safe_join(store.tool_dir(run, tool), list(parts))
    return path if path and path.suffix.lower() in TOOL_FILE_SUFFIXES else None


# (pattern, answer): the pattern matches the whole URL path; its groups, decoded, are the answer's arguments after the
# store and the log views. An answer returns a JSON value, a Path to send as a file, or None for 404.
SEGMENT = r"([^/]+)"
ROUTES = [
    (r"/(?:index\.html)?", lambda store, views: STATIC / "index.html"),
    (r"/static/(.+)", lambda store, views, *parts: safe_join(STATIC, list(parts))),
    (r"/api/runs", lambda store, views: [store.run_row(name) for name in store.run_names()]),
    (rf"/api/runs/{SEGMENT}", lambda store, views, run: store.run_detail(run)),
    (rf"/api/runs/{SEGMENT}/transcript/{SEGMENT}", lambda store, views, run, ep: store.transcript(run, ep)),
    (rf"/api/runs/{SEGMENT}/sample", lambda store, views, run: views.sample(run)),
    (rf"/api/runs/{SEGMENT}/context", lambda store, views, run: views.context_list(run)),
    (rf"/api/runs/{SEGMENT}/context/(\d+)", lambda store, views, run, i: views.context_call(run, int(i))),
    (rf"/api/runs/{SEGMENT}/timeline", lambda store, views, run: views.timeline(run)),
    (rf"/api/runs/{SEGMENT}/tools/{SEGMENT}/(.+)", lambda store, views, run, tool, *parts:
        _tool_file(store, run, tool, *parts)),
]
COMPILED = [(re.compile(pattern), answer) for pattern, answer in ROUTES]


def _arguments(match: re.Match) -> list[str]:
    """The route's groups as decoded path segments. A group splits at its separators before decoding, so an encoded
    separator stays inside its segment, where safe_part refuses it."""
    return [unquote(part) for group in match.groups() for part in group.split("/")]


class Handler(BaseHTTPRequestHandler):
    store: Store
    views: LogViews

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, body: bytes, ctype: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._send(json.dumps(obj, default=str).encode(), "application/json; charset=utf-8", status)

    def do_GET(self):
        path = urlparse(self.path).path
        for pattern, answer in COMPILED:
            match = pattern.fullmatch(path)
            if match:
                break
        else:
            return self._json({"error": f"not found: {path}"}, 404)
        try:
            result = answer(self.store, self.views, *_arguments(match))
        except KeyError as ex:
            return self._json({"error": f"not found: {ex}"}, 404)
        except Exception as ex:  # noqa: BLE001 - report to the client, keep serving
            return self._json({"error": f"{type(ex).__name__}: {ex}", "traceback": traceback.format_exc()[-2000:]}, 500)
        if result is None:
            return self._json({"error": f"not found: {path}"}, 404)
        if isinstance(result, Path):
            ctype = mimetypes.guess_type(result.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            return self._send(result.read_bytes(), ctype)
        return self._json(result)


def make_server(runs_dir: Path, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    """The browser's HTTP server over runs_dir, bound and not yet serving (port 0 picks a free port)."""
    store = Store(runs_dir.resolve())
    handler = type("BrowserHandler", (Handler,), {"store": store, "views": LogViews(store)})
    return ThreadingHTTPServer((host, port), handler)


def serve(runs_dir: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Serve the browser in the foreground until interrupted."""
    httpd = make_server(runs_dir, host, port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
