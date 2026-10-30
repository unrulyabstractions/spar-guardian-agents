"""One run directory as the export writes it (src/export/run_export.py), read through one object.

    run = RunDir(Path("runs/swarm_R3D3"))
    run.run_json["task"]
    for episode in run.with_transcripts():
        transcript = run.transcript(episode["episode"])
    verdict = run.verdicts().get("riemann#1@a01")
    team = run.team_signals()[0]                    # the code ladder's team record of the first sample

run.json is read once per RunDir and again after write_run_json_block. The JSON Lines files are read on every call, so
a measure that rewrites one (the code ladder's signals.jsonl, the judged ladder's ladder.jsonl) reads its own rows back.
A missing file reads as empty. signals.jsonl and ladder.jsonl hold agent rows ("row": "agent") and team rows ("row":
"team"); each reader returns one kind.
"""
from __future__ import annotations

import json
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

    def write_run_json_block(self, key: str, value) -> None:
        """Set one key of run.json, keeping every other key, and read run.json again."""
        run = {**(read_json(self.path / RUN_JSON) or {}), key: value}
        (self.path / RUN_JSON).write_text(json.dumps(run, indent=1))
        self.__dict__.pop("run_json", None)

    def ladder_rows(self, view: str = "full") -> list[dict]:
        """The judged ladder's agent rows for a view, parsed or not."""
        return rows_of(read_jsonl(self.ladder_path(view)), "agent")

    def ladder_team_rows(self, view: str = "full") -> list[dict]:
        """The judged ladder's team rows for a view: one per sample."""
        return rows_of(read_jsonl(self.ladder_path(view)), "team")

    def signals(self) -> list[dict]:
        """The code ladder's agent rows (signals.jsonl)."""
        return rows_of(read_jsonl(self.path / SIGNALS), "agent")

    def team_signals(self) -> list[dict]:
        """The code ladder's team rows (signals.jsonl): one per sample."""
        return rows_of(read_jsonl(self.path / SIGNALS), "team")

    def board(self, sample: str) -> dict:
        """The sample's board, communication/<sample>.json ({messages, events}); {} when the run had none."""
        return read_json(self.path / "communication" / f"{sample}.json") or {}

    def transcript_path(self, episode: str) -> Path:
        """Where the export stores the episode's transcript: trajectories/<episode>/transcript.json."""
        return self.path / "trajectories" / episode / "transcript.json"

    def transcript(self, episode: str) -> dict:
        """The episode's stored transcript."""
        return read_json(self.transcript_path(episode))

    def with_transcripts(self) -> list[dict]:
        """The episode records whose transcript is stored in the directory."""
        return [ep for ep in self.episodes() if self.transcript_path(ep["episode"]).is_file()]


def rows_of(rows: list[dict], kind: str) -> list[dict]:
    """The rows of one kind ("agent" or "team")."""
    return [row for row in rows if row.get("row") == kind]
