"""The vast.ai API: one request, a client that refuses writes on a dry run, and what the account lists.

The API key is read from ~/.config/vastai/vast_api_key and goes only into the Authorization header, which follows a
redirect only to the API's own scheme and host. Nothing prints it, nor the instance key a create returns, nor the
account record beyond its credit.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode, urlsplit

API = "https://console.vast.ai"
KEY_FILE = Path.home() / ".config" / "vastai" / "vast_api_key"
TIMEOUT_S = 60
READ_BACKOFF_S = (2, 5, 10, 20, 30)
"""The waits before a read vast.ai refused with 429 goes again."""
SEARCH_PATH = "/api/v0/bundles/"


@dataclass(frozen=True)
class Call:
    """One request to the vast.ai API."""

    method: str
    path: str
    query: dict = field(default_factory=dict)
    body: dict | None = None

    @property
    def url(self) -> str:
        return API + self.path + ("?" + urlencode(self.query) if self.query else "")

    @property
    def reads(self) -> bool:
        """A GET, or the offer search: a POST that changes nothing."""
        return self.method == "GET" or (self.method == "POST" and self.path == SEARCH_PATH)

    def show(self) -> str:
        return f"{self.method} {self.url}" + ("" if self.body is None else " " + json.dumps(self.body, sort_keys=True))


def build_request(call: Call, key: str) -> urllib.request.Request:
    """The request for a call, the key in an unredirected header: urllib copies a request's other headers to a
    redirect's target, never these."""
    request = urllib.request.Request(call.url, method=call.method,
                                     data=None if call.body is None else json.dumps(call.body).encode(),
                                     headers={"Accept": "application/json", "Content-Type": "application/json"})
    request.add_unredirected_header("Authorization", f"Bearer {key}")
    return request


def origin(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return parts.scheme, parts.netloc


class KeyStaysHome(urllib.request.HTTPRedirectHandler):
    """Follows a redirect as urllib does, and carries the key along only to the API's own scheme and host (vast.ai
    sends /api/v0/users/current to /api/v0/users/current/); anywhere else the key stays behind."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        key = req.unredirected_hdrs.get("Authorization")
        if new is not None and key and origin(newurl) == origin(API) == origin(req.full_url):
            new.add_unredirected_header("Authorization", key)
        return new


def http(call: Call) -> dict:
    """Send one call with the account's key; the reply's JSON."""
    request = build_request(call, KEY_FILE.read_text().strip())
    with urllib.request.build_opener(KeyStaysHome).open(request, timeout=TIMEOUT_S) as reply:
        return json.loads(reply.read() or b"{}")


class Vast:
    """The API, through a transport (http, or a fake in the tests). A client built without writes refuses every call
    but a read, so a dry run cannot rent or destroy anything."""

    def __init__(self, writes: bool, transport: Callable[[Call], dict] | None = None):
        self.writes = writes
        self.transport = transport or http

    def send(self, call: Call) -> dict:
        if not (self.writes or call.reads):
            raise PermissionError(f"refused {call.method} {call.path}: this client only reads")
        for wait in (*READ_BACKOFF_S, None):
            try:
                return self.transport(call)
            except urllib.error.HTTPError as error:
                if error.code != 429 or not call.reads or wait is None:
                    raise
                time.sleep(wait)


def list_instances(vast: Vast) -> list[dict]:
    """Every instance of the account: every page of GET /api/v1/instances/, checked against the total it reports."""
    rows, token = [], None
    while True:
        query = {"select_filters": "{}", "order_by": json.dumps([{"col": "id", "dir": "asc"}]), "limit": 25}
        reply = vast.send(Call("GET", "/api/v1/instances/", {**query, **({"after_token": token} if token else {})}))
        rows += reply.get("instances") or []
        token = reply.get("next_token")
        if not token:
            break
    if reply.get("total_instances") is not None and reply["total_instances"] != len(rows):
        raise RuntimeError(f"the account reports {reply['total_instances']} instances, the pages held {len(rows)}")
    return rows


def instance(vast: Vast, instance_id: int) -> dict | None:
    """One instance's full record (GET /api/v0/instances/<id>/), None when the account has no such instance."""
    try:
        reply = vast.send(Call("GET", f"/api/v0/instances/{instance_id}/", {"owner": "me"}))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise
    return reply.get("instances") or None


def credit(vast: Vast) -> float | None:
    """The account's credit; the rest of the account record is never kept."""
    return vast.send(Call("GET", "/api/v0/users/current/")).get("credit")


def start_error(row: dict) -> str | None:
    """Why vast.ai cannot start an instance, from its status message; None when it does not say so. A failed start
    says "Error: ...", and a start tried again after it "retries exceeded"."""
    message = (row.get("status_msg") or "").strip()
    return message if message.startswith("Error") or "retries exceeded" in message else None


def ssh_endpoint(record: dict) -> dict | None:
    """Direct ssh (the public address and the port mapped to 22) and vast.ai's proxy, from an instance record."""
    mapped = (record.get("ports") or {}).get("22/tcp") or []
    direct = {"host": record.get("public_ipaddr"), "port": int(mapped[0]["HostPort"])} if mapped else None
    proxy = {"host": record["ssh_host"], "port": int(record["ssh_port"])} if record.get("ssh_host") else None
    return {"direct": direct, "proxy": proxy} if direct or proxy else None
