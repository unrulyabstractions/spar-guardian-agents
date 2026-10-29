"""JSON and JSON Lines files as the export, the measures, the browser and the tools read and write them.

    run = read_json(run_dir / "run.json") or {}
    rows = read_jsonl(run_dir / "episodes.jsonl")
    write_jsonl(run_dir / "signals.jsonl", rows)

A missing file reads as its empty value, so a run exported before a file existed still loads.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json(path: Path, default: Any = None) -> Any:
    """The file's JSON document, or `default` when the file is absent."""
    return json.loads(path.read_text()) if path.is_file() else default


def read_jsonl(path: Path) -> list[dict]:
    """The file's rows, blank lines skipped; [] when the file is absent."""
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    """One JSON document per line."""
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def read_json_dir(directory: Path) -> dict[str, Any]:
    """Every JSON file of a directory by stem, or {} when the directory is absent."""
    if not directory.is_dir():
        return {}
    return {p.stem: read_json(p) for p in sorted(directory.glob("*.json"))}
