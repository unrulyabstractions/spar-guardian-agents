"""The one serializer of what processing stores: the facts, the labels, the row and the ledgers. Equal values built in
the same order give equal bytes, so a part that computes the same value rewrites no byte. A dict keeps the order it was
built in: the viewer shows a ladder's clauses, marks and agents in the order the measure gives them.

    data = dumps({"b": 1, "a": [1.5, None]})        # b'{\n "b": 1,\n "a": [\n  1.5,\n  null\n ]\n}\n'
    data = dumps_lines([{"k": 2}, {"k": 1}])        # one compact line per row, in the rows' own order
    data = encode("facts/calls.jsonl", rows)        # dumps_lines for .jsonl, dumps for anything else
    value = decode("facts/calls.jsonl", data)

A document is indented by one space, a line is compact; keys keep their order, text stays UTF-8 and every file ends
with a newline. A value JSON cannot hold the same way twice raises: NaN and infinity, a set, a key that is
not text.
"""
from __future__ import annotations

import json
from pathlib import PurePath
from typing import Any


def _text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=None,
                      separators=(",", ":"), default=_refuse)


def _refuse(value: Any) -> Any:
    raise TypeError(f"stored_json stores no {type(value).__name__}: {value!r}")


def dumps(value: Any) -> bytes:
    """One JSON document: keys in their order, one space of indent, UTF-8, a final newline."""
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=1,
                       default=_refuse) + "\n").encode()


def dumps_lines(rows: list[dict]) -> bytes:
    """JSON Lines: one compact line per row, keys in their order, in the rows' order."""
    return "".join(_text(row) + "\n" for row in rows).encode()


def encode(name: str | PurePath, value: Any) -> bytes:
    """The bytes of a file of this name: JSON Lines for a .jsonl name, one document otherwise."""
    return dumps_lines(value) if PurePath(name).suffix == ".jsonl" else dumps(value)


def decode(name: str | PurePath, data: bytes) -> Any:
    """The value of a file of this name: its rows for a .jsonl name, its document otherwise."""
    text = data.decode()
    if PurePath(name).suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return json.loads(text)
