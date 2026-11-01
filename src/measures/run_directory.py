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
    find_run(Path("runs/study_exploration_1"), "binder_minimal_luna1")      # its folder, wherever it sits
    figures_dir(Path("runs/study_exploration_1/mock/binder_minimal_lunamock1"))  # out/study_exploration_1/mock/...
    set_aside_attempt(Path("runs/study_exploration_1/flipt_minimal_luna1"))      # into attempts/flipt_minimal_luna1.1

run.json is read once per RunDir and again after write_run_json_block. The JSON Lines files are read on every call, so
a measure that rewrites one (the code ladder's signals.jsonl, the judged ladder's ladder.jsonl) reads its own rows back.
A missing file reads as empty. signals.jsonl and ladder.jsonl hold agent rows ("row": "agent") and team rows ("row":
"team"); each reader returns one kind.

A study keeps a run at runs/<study>/<run> or in one of its groups, runs/<study>/<group>/<run> (GROUPS). The run's eval
log sits at the same path under logs/, logs/<study>/[<group>/]<run>/, and its figures at the same path under out/,
out/<study>/[<group>/]<run>/. A run keeps its folder name wherever it sits. The name is its run.json run_id and the salt
its agents' IDs are drawn from.

One group breaks that rule. An earlier attempt of a run, superseded by a later run of the same name, sits at
runs/<study>/attempts/<name>.<n>/, its log at logs/<study>/attempts/<name>.<n>/ and its figures at
out/<study>/attempts/<name>.<n>/, n the first number from 1 free in all three (set_aside_attempt). Its run.json run_id
stays the run's name. A reader therefore keys runs by folder, never by name, and an attempt never answers to its run's
name (find_run).
"""
from __future__ import annotations

import json
import re
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
ATTEMPTS = "attempts"
"""The group of a run's earlier attempts: runs/<study>/attempts/<name>.<n>/."""
GROUPS = ("invalid", "incomplete", "mock", "set_aside", ATTEMPTS)
"""The folders a study sorts runs into, beside the runs at its top level: runs/<study>/<group>/<run>."""
ATTEMPT_FOLDER = re.compile(r"^(?P<name>.+)\.(?P<n>[1-9][0-9]*)$")
"""An earlier attempt's folder name: the run's name, a dot and n."""


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


def run_name(run: Path) -> str:
    """The run's own name: its folder's name, or for an earlier attempt, attempts/<name>.<n>, the name without .<n>."""
    path = Path(run).resolve()
    match = ATTEMPT_FOLDER.match(path.name) if path.parent.name == ATTEMPTS else None
    return match["name"] if match else path.name


def run_places(study: Path, name: str) -> list[Path]:
    """Every folder of the study that holds a run.json of this name: its top level, then each group in GROUPS order. An
    earlier attempt never answers to its run's name."""
    study = Path(study)
    places = [study / name, *(study / group / name for group in GROUPS if group != ATTEMPTS)]
    return [place for place in places if (place / RUN_JSON).is_file()]


def find_run(study: Path, name: str) -> Path | None:
    """The folder of the run of this name, at the study's top level or in the one group that holds it; None when no
    folder holds it. Raises ValueError when two folders hold it: scripts/sort_study_runs.py sets the earlier aside."""
    places = run_places(study, name)
    if len(places) > 1:
        raise ValueError(f"{len(places)} folders hold a run named {name}: {', '.join(map(str, places))}. Set the "
                         f"earlier aside with `uv run python -m scripts.sort_study_runs {study}`")
    return places[0] if places else None


def mirrored(path: Path, top: str) -> Path | None:
    """The same path under the folder `top` beside the runs/ it sits in: runs/<study>/[<group>/]<run> gives
    <top>/<study>/[<group>/]<run>, for top logs (the eval log) or out (the figures). None for a path under no folder
    named runs."""
    path = Path(path).resolve()
    runs = next((parent for parent in path.parents if parent.name == "runs"), None)
    return None if runs is None else runs.parent / top / path.relative_to(runs)


def mirrored_in_logs(path: Path) -> Path | None:
    """The same path under the logs/ beside the runs/ it sits in: runs/<study>/[<group>/]<run> gives
    logs/<study>/[<group>/]<run>. None for a path under no folder named runs."""
    return mirrored(path, "logs")


def figures_dir(run: Path) -> Path:
    """Where the run's figures go: out/<study>/[<group>/]<run>/ beside the runs/ it sits in, so two runs that share a
    name never share a folder. A run under no runs/ folder takes this checkout's out/<its folder's name>/."""
    return mirrored(run, "out") or settings.ROOT / "out" / Path(run).name


def set_aside_attempt(run: Path, *, keep: Path | None = None) -> list[tuple[Path, Path]]:
    """Move an earlier attempt of a run into the attempts group, before a later run of its name writes. The run folder
    goes to runs/<study>/attempts/<name>.<n>/, its eval log folder to logs/<study>/attempts/<name>.<n>/ and its figures
    to out/<study>/attempts/<name>.<n>/, n the first number from 1 free in all three. run is the folder at the study's
    top level or in a group other than attempts. A folder that does not exist is skipped, so a run that left nothing
    returns []. keep is a file left where it is: the eval log a caller is about to export, when it sits in the log
    folder; the folder's other entries move. Each move is one rename: nothing is copied or deleted. Returns each
    (source, target) in the order moved. Raises ValueError for a run under no runs/ folder, an attempt, or a keep
    inside a subfolder."""
    run = Path(run).resolve()
    place = run.parent if run.parent.name in GROUPS else None
    if place is not None and place.name == ATTEMPTS:
        raise ValueError(f"{run} is an earlier attempt already")
    study = run.parent.parent if place is not None else run.parent
    sources = [run, mirrored(run, "logs"), mirrored(run, "out")]
    if sources[1] is None:
        raise ValueError(f"{run} sits under no runs/ folder, so its logs and figures have no place")
    keep = Path(keep).resolve() if keep is not None else None
    existing = [source for source in sources if source.exists()]
    if not existing:
        return []
    n = 1
    while any(target.exists() for target in attempt_places(study, run.name, n)):
        n += 1
    moved = []
    for source, target in zip(sources, attempt_places(study, run.name, n)):
        if not source.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if keep is not None and keep.is_relative_to(source):
            if keep.parent != source:
                raise ValueError(f"{keep} sits in a subfolder of {source}; set it aside by hand")
            target.mkdir()
            for child in sorted(source.iterdir()):
                if child != keep:
                    child.rename(target / child.name)
                    moved.append((child, target / child.name))
        else:
            source.rename(target)
            moved.append((source, target))
    return moved


def attempt_places(study: Path, name: str, n: int) -> list[Path]:
    """Attempt n of the run of this name in the study: its run folder, its eval log folder and its figures folder."""
    folder = Path(study).resolve() / ATTEMPTS / f"{name}.{n}"
    return [folder, mirrored(folder, "logs"), mirrored(folder, "out")]


class RunDir:
    """A run directory under runs/: run.json, the episodes, the verdicts, the ladders' rows and the transcripts. name is
    its folder's name and run_name the run's own name, which differ for an earlier attempt."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.name = self.path.name
        self.run_name = run_name(self.path)

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
        """The run's eval log, or None when none exists. The log is the file run.json names. The first of these places
        that holds it wins:
        - the path run.json records;
        - the run's own path under the logs/ beside its runs/, logs/<study>/[<group>/]<run>/, at any depth. A run pulled
          from a box or moved since keeps its log there;
        - for a run in a group, the same at its study's top level, logs/<study>/<run>/. A log not moved with its run
          stays there;
        - this checkout's logs/<study>/<run>/, for the study the run's folder sits in;
        - the current study's logs/<run>/;
        - the run's name beside the recorded folder, for a run set aside under another name."""
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
