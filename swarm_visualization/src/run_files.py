"""Read the stored files of one run. Pure reads: nothing is written.

A run folder holds `processed/` (what the run recorded) and `analysis/` (the labels computed from it). This module
reads only the files the swarm picture needs and returns them as plain dicts and lists.
"""
from __future__ import annotations

import json
from pathlib import Path


def read_json(path: Path, default=None):
    if not path.is_file():
        return default
    with path.open() as handle:
        return json.load(handle)


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


class RunFiles:
    """The files of one run folder, read on demand."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.processed = folder / "processed"
        self.analysis = folder / "analysis"

    @property
    def name(self) -> str:
        return self.folder.name

    def run_json(self) -> dict:
        return read_json(self.processed / "run.json", {}) or {}

    def episodes(self) -> list[dict]:
        return read_jsonl(self.processed / "episodes.jsonl")

    def roles(self) -> dict[str, str]:
        """agent -> worker, regulator or disregulator (from facts/topology.json, else the episodes)."""
        topology = read_json(self.processed / "facts" / "topology.json", {}) or {}
        roles = {row["agent"]: row.get("role") or "worker" for row in topology.get("agents") or [] if row.get("agent")}
        for episode in self.episodes():
            roles.setdefault(episode.get("agent"), episode.get("role") or "worker")
        return {agent: role for agent, role in roles.items() if agent}

    def transcript(self, episode: dict) -> dict:
        relative = episode.get("transcript_path")
        if not relative:
            return {}
        return read_json(self.processed / relative, {}) or {}

    def boards(self) -> list[dict]:
        """Each board's {messages, events} (processed/communication/*.json)."""
        folder = self.processed / "communication"
        return [read_json(p, {}) or {} for p in sorted(folder.glob("*.json"))] if folder.is_dir() else []

    def signals(self) -> list[dict]:
        return read_jsonl(self.analysis / "signals.jsonl")

    def cheating(self) -> dict:
        return read_json(self.analysis / "labels" / "cheating.json", {}) or {}
