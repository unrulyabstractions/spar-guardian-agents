"""Write a fixture's file at its place in a run folder, the folders above it made first: a run folder holds its files
in processed/ and analysis/ (src/measures/run_directory.py FILES).

    write_text(run / "processed/run.json", "{}")
"""
from __future__ import annotations

from pathlib import Path


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def write_bytes(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path
