"""One run directory as the export writes it (src/export/run_export.py), read through one object, and where a study
keeps its runs.

    run = RunDir(Path("runs/swarm_R3D3"))
    run.run_json["task"]
    for episode in run.with_transcripts():
        transcript = run.transcript(episode["episode"])
    verdict = run.verdicts().get("riemann#1@k7q2m")
    team = run.team_signals()[0]                    # the code ladder's team record of the first sample
    log = run.access_log()                          # run.json's block with its rows, or None
    study_runs(Path("runs/study_exploration_1"))    # every run of the study, at its top level and in its groups

run.json is read once per RunDir and again after write_run_json_block. The JSON Lines files are read on every call, so
a measure that rewrites one (the code ladder's signals.jsonl, the judged ladder's ladder.jsonl) reads its own rows back.
A missing file reads as empty. signals.jsonl and ladder.jsonl hold agent rows ("row": "agent") and team rows ("row":
"team"); each reader returns one kind.

A study keeps a run at runs/<study>/<run> or in one of its groups, runs/<study>/<group>/<run> (GROUPS). The run's eval
log sits at the same path under logs/: logs/<study>/[<group>/]<run>/. A run keeps its folder name wherever it sits. The
name is its run.json run_id and the salt its agents' IDs are drawn from.
"""
from __future__ import annotations

import json
from functools import cached_property
from pathlib import Path

from src.configuration import settings
from src.utils.json_files import read_json, read_jsonl

RUN_JSON = "run.json"
EPISODES = "episodes.jsonl"
JUDGE = "judge.jsonl"
SIGNALS = "signals.jsonl"
ACCESS_LOG = "access_log.jsonl"
LADDER_FILES = {"full": "ladder.jsonl", "actions": "ladder_actions.jsonl"}
GROUPS = ("invalid", "incomplete", "mock", "set_aside")
"""The folders a study sorts runs into, beside the runs at its top level: runs/<study>/<group>/<run>."""


def study_runs(study: Path) -> list[Path]:
    """Every run of a study's folder: each folder that holds a run.json at its top level or in one of its groups, by
    name, then by path."""
    study = Path(study)
    folders = [*study.iterdir(), *(path for group in GROUPS if (study / group).is_dir()
                                   for path in (study / group).iterdir())]
    return sorted((path for path in folders if (path / RUN_JSON).is_file()), key=lambda path: (path.name, str(path)))


def run_place(run: Path) -> tuple[str, str | None]:
    """(the study, the group or None) of the run at runs/<study>/<run> or runs/<study>/<group>/<run>."""
    parent = Path(run).resolve().parent
    return (parent.parent.name, parent.name) if parent.name in GROUPS else (parent.name, None)


def mirrored_in_logs(path: Path) -> Path | None:
    """The same path under the logs/ beside the runs/ it sits in: runs/<study>/[<group>/]<run> gives
    logs/<study>/[<group>/]<run>. None for a path under no folder named runs."""
    path = Path(path).resolve()
    runs = next((parent for parent in path.parents if parent.name == "runs"), None)
    return None if runs is None else runs.parent / "logs" / path.relative_to(runs)


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

    @property
    def eval_log(self) -> Path | None:
        """The run's eval log: the file run.json names, at the first of these places that holds it. The path run.json
        records. The run's own path under logs/, beside the runs/ it sits in (logs/<study>/[<group>/]<run>/), which
        holds a run pulled from a box or moved since. For a run in a group, the same at its study's top level
        (logs/<study>/<run>/), for a log not moved with its run. This checkout's logs/<study>/<run>/, for the study the
        run belongs to. The current study's logs/<run>/. Under the run's name beside the recorded folder, for a run set
        aside under another name. None when none exists."""
        recorded = self.run_json.get("eval_log")
        if not recorded:
            return None
        path, (study, group) = Path(recorded), run_place(self.path)
        here = self.path.resolve()
        mirrors = [mirrored_in_logs(here), mirrored_in_logs(here.parent.parent / self.name) if group else None]
        candidates = [path, *(mirror / path.name for mirror in mirrors if mirror),
                      settings.ROOT / "logs" / study / self.name / path.name, settings.LOGS / self.name / path.name,
                      path.parent.parent / self.name / path.name]
        return next((candidate for candidate in candidates if candidate.is_file()), None)

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

    def access_log(self) -> dict | None:
        """run.json "access_log" with access_log.jsonl's rows as "rows"; None for a run that kept no log: no block, as
        in a run older than the log, or status off."""
        block = self.run_json.get("access_log")
        if block is None or block["status"] == "off":
            return None
        return {**block, "rows": read_jsonl(self.path / ACCESS_LOG)}

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
