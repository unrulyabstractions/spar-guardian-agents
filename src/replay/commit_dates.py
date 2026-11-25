"""The commit date that gives a commit its recorded id. A replayed commit holds the same tree, parents, identities and
message as the recorded one, and only its dates differ: the replay commits at today's clock. The recorded id fixes the
date, so a search over the seconds of the call's window finds it.

    ids = RecordedIds(["[k7q2m 77eabf6] fix(config): ...", team_log])
    found = find_date(body, ids.matching, t0, t1)       # Dated(seconds, tz, author_too, sha, recorded) or None
    body = reparent(body, {old_parent: new_parent})     # a child of a rewritten commit names the new parent

Pure: hashlib only.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable
from typing import NamedTuple

HEX = re.compile(r"(?<![0-9a-f])[0-9a-f]{7,40}(?![0-9a-f])")
SLACK_S = 5
"""Seconds before the call's start and after its end that the search also tries."""
UTC = b"+0000"


class Dated(NamedTuple):
    """The date found: committer seconds and zone, whether the author's date moved with it, the new id and the
    recorded id (or prefix) it matches."""
    seconds: int
    tz: str
    author_too: bool
    sha: str
    recorded: str


def object_id(kind: str, body: bytes) -> str:
    """Git's id of an object of this kind and body."""
    return hashlib.sha1(f"{kind} {len(body)}\0".encode() + body).hexdigest()


def split_commit(body: bytes) -> tuple[list[bytes], bytes]:
    """(header lines, the rest from the blank line on) of a commit object."""
    head, sep, message = body.partition(b"\n\n")
    return head.split(b"\n"), sep + message


def identity_name(line: bytes) -> bytes:
    """The name of an `author` or `committer` header line."""
    return line.split(b" ", 1)[1].rsplit(b" <", 1)[0] if b" " in line else b""


def with_time(line: bytes, when: int, tz: bytes) -> bytes:
    """An identity line with its date replaced."""
    who = line.rsplit(b" ", 2)[0]
    return who + b" " + str(when).encode() + b" " + tz


def header_index(lines: list[bytes], key: bytes) -> int | None:
    return next((i for i, line in enumerate(lines) if line.startswith(key + b" ")), None)


def retime(body: bytes, when: int, tz: bytes, author_too: bool) -> bytes:
    """The commit with the committer's date (and with author_too the author's) set to `when` in zone `tz`."""
    lines, rest = split_commit(body)
    for key in (b"committer", *((b"author",) if author_too else ())):
        i = header_index(lines, key)
        if i is not None:
            lines[i] = with_time(lines[i], when, tz)
    return b"\n".join(lines) + rest


def reparent(body: bytes, renames: dict[str, str]) -> bytes:
    """The commit with each parent (and tree) id in `renames` replaced by its new id."""
    lines, rest = split_commit(body)
    for i, line in enumerate(lines):
        key, _, sha = line.partition(b" ")
        if key in (b"parent", b"tree") and sha.decode() in renames:
            lines[i] = key + b" " + renames[sha.decode()].encode()
    return b"\n".join(lines) + rest


def variants(body: bytes) -> list[tuple[bytes, bool]]:
    """(zone, author_too) pairs to try: the committer's own zone, then UTC; the author's date moves too only where
    author and committer are one person at one date, as in a new commit or a merge."""
    lines, _ = split_commit(body)
    ci, ai = header_index(lines, b"committer"), header_index(lines, b"author")
    if ci is None:
        return []
    zone = lines[ci].rsplit(b" ", 1)[1]
    same = ai is not None and identity_name(lines[ai]) == identity_name(lines[ci]) and \
        lines[ai].rsplit(b" ", 2)[1:] == lines[ci].rsplit(b" ", 2)[1:]
    zones = [zone] + ([UTC] if zone != UTC else [])
    return [(z, a) for z in zones for a in ((True, False) if same else (False,))]


def find_date(body: bytes, matching: Callable[[str], str | None], t0: float, t1: float,
              slack: int = SLACK_S) -> Dated | None:
    """The first date in [t0 - slack, t1 + slack], over variants(), whose commit id a recorded id names (`matching`
    returns the recorded id or None). None when no second of the window gives one."""
    for tz, author_too in variants(body):
        for when in range(int(t0) - slack, int(t1) + slack + 1):
            sha = object_id("commit", retime(body, when, tz, author_too))
            recorded = matching(sha)
            if recorded:
                return Dated(when, tz.decode(), author_too, sha, recorded)
    return None


class RecordedIds:
    """The commit ids a record names, full or abbreviated (seven hex digits or more), found in its texts. matching(sha)
    gives the recorded id that sha starts with, the longest first, or None."""

    def __init__(self, texts: Iterable[str]) -> None:
        self.by_head: dict[str, set[str]] = {}
        for text in texts:
            for token in HEX.findall(text or ""):
                self.by_head.setdefault(token[:7], set()).add(token)

    def __len__(self) -> int:
        return sum(len(tokens) for tokens in self.by_head.values())

    def matching(self, sha: str) -> str | None:
        tokens = [t for t in self.by_head.get(sha[:7], ()) if sha.startswith(t)]
        return max(tokens, key=len) if tokens else None


def renamed_text(text: str, renames: dict[str, str]) -> str:
    """The text with every id an old commit's id starts with (seven digits or more) written as the new id's same
    length prefix: a replayed output as it reads once its commits carry their recorded ids."""
    def swap(match: re.Match) -> str:
        token = match.group(0)
        for old, new in renames.items():
            if old.startswith(token):
                return new[:len(token)]
        return token
    return HEX.sub(swap, text) if renames else text
