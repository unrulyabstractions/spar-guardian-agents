"""One run directory as the export writes it (src/export/run_export.py), read through one object.

    run = RunDir(Path("runs/swarm_R3D3"))
    run.run_json["task"]
    for episode in run.with_transcripts():
        transcript = run.transcript(episode["episode"])
    verdict = run.verdicts().get("riemann#1@a01")

run.json is read once per RunDir. The JSON Lines files are read on every call, so a measure that rewrites one (the code
ladder's signals.jsonl, the judged ladder's ladder.jsonl) reads its own rows back. A missing file reads as empty.
"""
from __future__ import annotations

from functools import cached_property
from pathlib import Path

from src.utils.json_files import read_json, read_jsonl

RUN_JSON = "run.json"
EPISODES = "episodes.jsonl"
JUDGE = "judge.jsonl"
SIGNALS = "signals.jsonl"
LADDER_FILES = {"full": "ladder.jsonl", "actions": "ladder_actions.jsonl"}


class RunDir:
    """A run directory under runs/: run.json, the episodes, the verdicts, the ladders' rows and the transcripts."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.name = self.path.name

    def is_run(self) -> bool:
        """Whether the directory holds a run.json."""
        return (self.path / RUN_JSON).is_file()

    def require(self) -> RunDir:
        """The run itself; raises ValueError for a directory without run.json."""
        if not self.is_run():
            raise ValueError(f"not a run directory (no {RUN_JSON}): {self.path}")
        return self

    @cached_property
    def run_json(self) -> dict:
        """run.json, {} when absent."""
        return read_json(self.path / RUN_JSON) or {}

    def episodes(self) -> list[dict]:
        """episodes.jsonl: one record per (sample, agent)."""
        return read_jsonl(self.path / EPISODES)

    def verdicts(self) -> dict[str, dict]:
        """judge.jsonl by episode, parsed or not."""
        return {row["episode"]: row for row in read_jsonl(self.path / JUDGE)}

    def ladder_path(self, view: str = "full") -> Path:
        """The judged ladder's file for a view (full or actions)."""
        return self.path / LADDER_FILES[view]

    def ladder_rows(self, view: str = "full") -> list[dict]:
        """The judged ladder's rows for a view, parsed or not."""
        return read_jsonl(self.ladder_path(view))

    def signals(self) -> list[dict]:
        """The code ladder's rows (signals.jsonl)."""
        return read_jsonl(self.path / SIGNALS)

    def transcript_path(self, episode: str) -> Path:
        """Where the export stores the episode's transcript: trajectories/<episode>/transcript.json."""
        return self.path / "trajectories" / episode / "transcript.json"

    def transcript(self, episode: str) -> dict:
        """The episode's stored transcript."""
        return read_json(self.transcript_path(episode))

    def with_transcripts(self) -> list[dict]:
        """The episode records whose transcript is stored in the directory."""
        return [ep for ep in self.episodes() if self.transcript_path(ep["episode"]).is_file()]
