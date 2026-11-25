"""The few fields of an eval log's events that the measures read, streamed from the log one sample at a time.

    samples = log_samples(run.eval_log)       # each sample's events, in the order read_eval_log gives the samples
    samples[0].events[3].function             # "bash": a LogEvent holds only FIELDS
    list(tool_events(run.eval_log))           # every sample's tool events

A sample of an eval log can hold gigabytes, the attachments mostly, which Inspect writes after the events. Reading it
whole with read_eval_log or json.loads took 8 to 26 GB of memory for one run on 2026-11-22. This module streams each
sample member of the zip through ijson, keeps each event's FIELDS and nothing else, and stops reading the member where
its events end. The last log read is kept for the next measure of the same run.

yajl, ijson's parser, refuses two things json.dumps writes and json.loads reads: an integer past 64 bits, which the
sandbox's network log holds, and NaN or Infinity. `numbers_yajl_reads` rewrites them where a number stands, an integer
as a float and NaN or Infinity as null; no field FIELDS keeps is a number.

Pure: nothing written.
"""
from __future__ import annotations

import re
import zipfile
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Iterator, NamedTuple

import ijson
import inspect_ai.log  # noqa: F401 - registers the zstd compression of Inspect's .eval zips with zipfile

SAMPLES_DIR = "samples/"
SUMMARIES = "summaries.json"
FIELDS = ("event", "id", "span_id", "parent_id", "type", "name", "function", "timestamp", "completed")
"""What a LogEvent keeps of an event: its kind and id, its span, a span's parent, type and name, a tool event's
function, and the times."""
EVENT = "events.item"
"""ijson's prefix of an event in a sample member."""
KEPT = {f"{EVENT}.{k}": k for k in FIELDS}
SCALARS = ("string", "number", "null", "boolean")
CHUNK = 1 << 22
"""Bytes read from the zip member at a time."""
NUMBER = re.compile(rb"(?<=[:,\[])(\s*)(-?\d{19,}|-?Infinity|NaN)(?=\s*[,}\]])")
"""A number yajl cannot read, where a number stands: after a colon, a comma or a bracket, before a comma or a closing
bracket. 19 digits is where a 64-bit integer may end."""
TAIL = re.compile(rb"[\s\d\-INaftiny]*$")
"""The end of a chunk that may be the start of such a number."""
WINDOW = 4096
"""The bytes at a chunk's end searched for TAIL."""


class LogEvent(NamedTuple):
    """An event's FIELDS, with its times as datetimes; read like an Inspect event (event.function, event.span_id)."""
    event: str | None
    id: str | None
    span_id: str | None
    parent_id: str | None
    type: str | None
    name: str | None
    function: str | None
    timestamp: datetime | None
    completed: datetime | None


class LogSample(NamedTuple):
    """One sample member of the log: its name in the zip and its events."""
    member: str
    events: list[LogEvent]


def numbers_yajl_reads(data: bytes, cut: int | None = None) -> bytes:
    """data[:cut] with every number yajl refuses (NUMBER) rewritten, an integer as a float and NaN or Infinity as
    null. A match is read over the whole of `data`, so the character after a number before `cut` still counts."""
    cut = len(data) if cut is None else cut
    out, last = [], 0
    for m in NUMBER.finditer(data):
        if m.start() >= cut:
            break
        value = m.group(2)
        out += [data[last:m.start()], m.group(1),
                b"null" if value.lstrip(b"-")[:1] in (b"I", b"N") else value + b".0"]
        last = m.end()
    out.append(data[last:cut])
    return b"".join(out)


class Readable:
    """A zip member as a file ijson reads, its numbers rewritten (numbers_yajl_reads). A chunk's end that may start
    such a number waits for the next chunk, with the character before it."""

    def __init__(self, raw):
        self.raw, self.carry, self.done = raw, b"", False

    def read(self, size: int = -1) -> bytes:
        if size == 0:  # ijson asks read(0) whether the file gives bytes
            return b""
        while not self.done:
            data = self.raw.read(CHUNK)
            if not data:
                self.done = True
                out, self.carry = self.carry, b""
                return numbers_yajl_reads(out)
            data = self.carry + data
            cut = max(TAIL.search(data, max(len(data) - WINDOW, 0)).start() - 1, 0)
            out, self.carry = numbers_yajl_reads(data, cut), data[cut:]
            if out:
                return out
        return b""


def when(value) -> datetime | None:
    """An ISO time as Inspect writes it, as a datetime; None when absent."""
    return datetime.fromisoformat(value) if isinstance(value, str) and value else None


def light(event: dict) -> LogEvent:
    """The event's FIELDS."""
    return LogEvent(*(event.get(k) for k in FIELDS[:7]), when(event.get("timestamp")), when(event.get("completed")))


def member_events(archive: zipfile.ZipFile, member: str) -> list[LogEvent]:
    """The events of one sample member as LogEvents, read up to the end of its events array. A member yajl still
    cannot read raises ValueError."""
    out, event = [], None
    with archive.open(member) as raw:
        try:
            for prefix, kind, value in ijson.parse(Readable(raw), use_float=True):
                if prefix == EVENT:
                    if kind == "start_map":
                        event = {}
                    elif kind == "end_map":
                        out.append(light(event))
                        event = None
                elif event is not None and kind in SCALARS and prefix in KEPT:
                    event[KEPT[prefix]] = value
                elif prefix == "events" and kind == "end_array":
                    break
        except ijson.JSONError as e:
            raise ValueError(f"{member}: {e}") from e
    return out


def sample_order(archive: zipfile.ZipFile, members: list[str]) -> list[str]:
    """The members in the order read_eval_log gives their samples (Inspect's sort_samples: by epoch, then by id, an
    int id zero-filled), with the ids and epochs from summaries.json; the zip's order where it names no sample."""
    import json  # noqa: PLC0415 - summaries.json is small

    try:
        summaries = json.loads(archive.read(SUMMARIES))
    except KeyError:
        return members
    key = {f"{SAMPLES_DIR}{s.get('id')}_epoch_{s.get('epoch')}.json":
           (s.get("epoch"), s.get("id") if isinstance(s.get("id"), str) else str(s.get("id")).zfill(20))
           for s in summaries if isinstance(s, dict)}
    if not all(m in key for m in members):
        return members
    return sorted(members, key=lambda m: key[m])


@lru_cache(maxsize=1)
def _read(path: str, mtime_ns: int, size: int) -> tuple[LogSample, ...]:
    with zipfile.ZipFile(path) as archive:
        # a re-logged sample repeats its member name and zip.open(name) gives the last one: read each name once
        members = [n for n in dict.fromkeys(archive.namelist()) if n.startswith(SAMPLES_DIR) and n.endswith(".json")]
        return tuple(LogSample(m, member_events(archive, m)) for m in members)


def zip_samples(path: Path) -> tuple[LogSample, ...]:
    """Each sample member's events in the zip's order, the last log read kept (by its path, time and size)."""
    st = Path(path).stat()
    return _read(str(path), st.st_mtime_ns, st.st_size)


def log_samples(path: Path) -> list[LogSample]:
    """Each sample's events in the order read_eval_log gives the samples (sample_order)."""
    samples = {s.member: s for s in zip_samples(path)}
    with zipfile.ZipFile(path) as archive:
        return [samples[m] for m in sample_order(archive, list(samples))]


def tool_events(path: Path) -> Iterator[LogEvent]:
    """Every sample's tool events, in the zip's order."""
    for sample in zip_samples(path):
        yield from (e for e in sample.events if e.event == "tool")
