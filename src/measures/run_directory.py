"""One run directory as the export writes it (src/export/run_export.py), read through one object, and where a study
keeps its runs.

    run = RunDir(Path("runs/swarm_R3D3"))
    run.run_json["task"]
    for episode in run.with_transcripts():
        transcript = run.transcript(episode["episode"])
    team = run.team_signals()[0]                    # the code record's team row of the first sample
    boards = run.boards()                           # every sample's board: {messages, events}
    log = run.access_log()                          # run.json's block with its rows, or None
    study_runs(Path("runs/ian_baseline_explorations_1"))    # every run of the study, at its top level and in its groups
    find_run(Path("runs/ian_baseline_explorations_1"), "binder_minimal_luna1")      # its folder, wherever it sits
    figures_dir(Path("OLD/runs/ian_baseline_explorations_1/mock/binder_minimal_lunamock1"))  # out/<study>/mock/...
    set_aside_attempt(Path("runs/ian_seed_prototyping_0/swe_luna_none_s1"))  # attempts/swe_luna_none_s1.1
    run.file("episodes.jsonl")                      # a stored file of the export or of no part, by name (FILES)
    run.output("judge_cheatbench")                  # a part's stored file, by the registry's path
    run.outputs("export")                           # {layout path: file} of every file a part stores now
    run.versions()                                  # the two ledgers: {"processed": ..., "analysis": ...}
    log_dir(run.path)                               # logs/<study>/[<group>/]<run>/

**The path table.** A run's files have one place each in the stored layout, written once: a part's paths in
src/configuration/pipeline_parts.yaml, every other path in NOT_PARTS. FILES names each of them by its last name. The
layout is runs/<study>/[<group>/]<run>/processed/ (what the eval log gives, no label) and analysis/ (what is made from
processed/). Until a run's files move there, TODAY says where each existing file sits, and RunDir.at reads a layout
path at that place. A path TODAY does not name (the ledgers, the facts) sits at its layout path already.

run.json is read once per RunDir and again after write_run_json_block. The JSON Lines files are read on every call, so
a measure that rewrites one (the code record's signals.jsonl) reads its own rows back. A missing file reads as empty.
signals.jsonl holds agent rows ("row": "agent") and team rows ("row": "team"); each reader returns one kind.

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
from src.measures import pipeline_parts
from src.utils.json_files import read_json, read_jsonl

RUN_JSON = "run.json"
EPISODES = "episodes.jsonl"
SIGNALS = "signals.jsonl"
ACCESS_LOG = "access_log.jsonl"
COMMUNICATION = "communication"
"""The run's folder of board records, one <sample>.json each. RunDir.board and RunDir.boards are its one reader."""
ATTEMPTS = "attempts"
"""The group of a run's earlier attempts: runs/<study>/attempts/<name>.<n>/."""
WORKER_ROLES = (None, "worker")
"""The roles that count as a worker: role worker, or no role at all (a run with no roles)."""
GROUPS = ("invalid", "incomplete", "mock", "set_aside", ATTEMPTS)
IN_RUN_LOGS = "eval"
"""The folder inside a run where a collection that keeps each run's log with the run holds it (apart_controls)."""
"""The folders a study sorts runs into, beside the runs at its top level: runs/<study>/<group>/<run>."""
ATTEMPT_FOLDER = re.compile(r"^(?P<name>.+)\.(?P<n>[1-9][0-9]*)$")
"""An earlier attempt's folder name: the run's name, a dot and n."""
NOT_PARTS = {
    "swe_summary.json": "processed/swe_summary.json",
    "board_reads.json": "processed/board_reads.json",
    "judges": "analysis/judges/",
    "tools": "analysis/tools/",
}
"""The stored paths no part of the registry owns: Lucy's SWE measures, the tools' board cache, the judges' folder and
the tools' outputs. A folder ends in "/"."""
TODAY = {
    "processed/run.json": "run.json",
    "processed/episodes.jsonl": "episodes.jsonl",
    "processed/access_log.jsonl": "access_log.jsonl",
    "processed/network.jsonl": "network.jsonl",
    "analysis/signals.jsonl": "signals.jsonl",
    "processed/swe_summary.json": "swe_summary.json",
    "processed/tmp_snapshots.json": "tmp_snapshots.json",
    "processed/board_reads.json": "tools/board_reads.json",
    "processed/trajectories/": "trajectories/",
    "processed/communication/": "communication/",
    "processed/defense/": "defense/",
    "processed/swe/": "swe/",
    "processed/replay/steps.jsonl": "steps.jsonl",
    "processed/replay/replay.json": "replay.json",
    "analysis/judges/": "judges/",
    "analysis/tools/": "tools/",
}
"""Where each stored path sits in a run folder not yet moved to the layout: layout path -> place, a folder for every
path under it. The move to the layout empties it."""


def today(rel: str) -> str:
    """Where a layout path sits in the run folder now (TODAY); the layout path itself when TODAY does not name it."""
    if rel in TODAY:
        return TODAY[rel]
    folder = next((layout for layout in TODAY if layout.endswith("/") and rel.startswith(layout)), None)
    return rel if folder is None else TODAY[folder] + rel[len(folder):]


def files_table(registry: pipeline_parts.Registry) -> dict[str, str]:
    """name -> layout path of every stored file or folder a part lists by name, and of NOT_PARTS. A path with a "*"
    (the judges' files) and the ledgers have no name. Raises ValueError when two paths share a name."""
    table: dict[str, str] = {}
    paths = [path for part in registry.parts.values() for path in part.paths]
    for path in [*paths, *NOT_PARTS.values()]:
        if "*" in path or path in pipeline_parts.LEDGERS.values():
            continue
        name = path.rstrip("/").rsplit("/", 1)[-1]
        if table.get(name, path) != path:
            raise ValueError(f"two stored paths share the name {name}: {table[name]} and {path}")
        table[name] = path
    return table


FILES = files_table(pipeline_parts.registry())
"""name -> layout path: run.json, episodes.jsonl, trajectories, steps.jsonl, signals.jsonl, ... (files_table)."""


def is_worker(role: str | None) -> bool:
    """Whether an agent of this role is a worker, whom the team's rung on every ladder counts (WORKER_ROLES)."""
    return role in WORKER_ROLES


def is_influencer(role: str | None) -> bool:
    """Whether an agent of this role is an influencer (a regulator or a disregulator): every agent that is not a
    worker. An influencer's level on every ladder is reported apart from the team's."""
    return not is_worker(role)


def episode_agent(episode: dict) -> str | None:
    """An episode's agent id, the bare ID its record names ("k7q2m")."""
    return episode.get("agent") or None


def roles_of(run_json: dict, episodes: list[dict]) -> dict[str, str | None]:
    """agent -> role (worker, regulator, disregulator), None in a run with no roles: every episode's agent, then
    run.json's swarm roles, then each episode's own role."""
    roles: dict[str, str | None] = {episode_agent(ep): None for ep in episodes}
    roles.update((run_json.get("swarm") or {}).get("roles") or {})
    roles.update({episode_agent(ep): ep["role"] for ep in episodes if ep.get("role")})
    roles.pop(None, None)
    return roles


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
    earlier attempt never answers to its run's name. An absolute path is no name and gives []."""
    study = Path(study)
    if Path(name).is_absolute():
        return []
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


def checkout_of(path: Path) -> Path | None:
    """The folder that holds the runs/ folder the path sits in (the checkout, or a pulled copy of the dataset's
    layout), None for a path under no folder named runs."""
    path = Path(path).resolve()
    runs = next((parent for parent in path.parents if parent.name == "runs"), None)
    return None if runs is None else runs.parent


def log_link(eval_log: Path, run: Path) -> str:
    """How run.json names the run's eval log: its path from the checkout the run sits in (checkout_of), as
    logs/<study>/[<group>/]<run>/<file>.eval; the absolute path for a log outside that checkout or a run under no
    runs/ folder."""
    eval_log, root = Path(eval_log).resolve(), checkout_of(run)
    if root is not None and eval_log.is_relative_to(root):
        return eval_log.relative_to(root).as_posix()
    return eval_log.as_posix()


def mirrored(path: Path, top: str) -> Path | None:
    """The same path under the folder `top` beside the runs/ it sits in: runs/<study>/[<group>/]<run> gives
    <top>/<study>/[<group>/]<run>, for top logs (the eval log) or out (the figures). None for a path under no folder
    named runs."""
    path = Path(path).resolve()
    runs = next((parent for parent in path.parents if parent.name == "runs"), None)
    return None if runs is None else runs.parent / top / path.relative_to(runs)


def log_dir(run: Path) -> Path | None:
    """The run's eval log folder, the same path under the logs/ beside the runs/ it sits in:
    runs/<study>/[<group>/]<run> gives logs/<study>/[<group>/]<run>. None for a path under no folder named runs."""
    return mirrored(run, "logs")


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

    def at(self, rel: str) -> Path:
        """Where a layout path of the run sits now (today)."""
        return self.path / today(rel)

    def file(self, name: str) -> Path:
        """A stored file or folder of the run by its name in FILES: run.json, episodes.jsonl, trajectories, ..."""
        if name not in FILES:
            raise ValueError(f"no stored file named {name!r}; names: {', '.join(sorted(FILES))}")
        return self.at(FILES[name])

    def output(self, part: str, registry: pipeline_parts.Registry | None = None) -> Path:
        """The one file a part stores, by its path in the registry. Raises ValueError for a part that stores several
        (outputs reads those)."""
        paths = (registry or pipeline_parts.registry()).part(part).paths
        if len(paths) != 1 or "*" in paths[0] or paths[0].endswith("/"):
            raise ValueError(f"{part} stores {len(paths)} paths ({', '.join(paths)}); read them with outputs")
        return self.at(paths[0])

    def outputs(self, part: str, registry: pipeline_parts.Registry | None = None) -> dict[str, Path]:
        """{layout path: file} of every file the part stores now, sorted by layout path: each file it names that
        exists, every file under a folder it names, every file a name with a "*" matches. A file being written
        (*.partial) is skipped."""
        found: dict[str, Path] = {}
        for rel in (registry or pipeline_parts.registry()).part(part).paths:
            if rel.endswith("/"):
                folder = self.at(rel)
                if folder.is_dir():
                    found.update((rel + p.relative_to(folder).as_posix(), p) for p in folder.rglob("*")
                                 if p.is_file())
            elif "*" in rel:
                folder_rel, _, pattern = rel.rpartition("/")
                folder = self.at(folder_rel + "/")
                if folder.is_dir():
                    found.update((f"{folder_rel}/{p.name}", p) for p in folder.glob(pattern) if p.is_file())
            elif self.at(rel).is_file():
                found[rel] = self.at(rel)
        return {rel: found[rel] for rel in sorted(found) if not rel.endswith(".partial")}

    def versions(self) -> dict[str, Path]:
        """The run's two ledgers, {"processed": ..., "analysis": ...} (src/measures/stored_versions.py)."""
        return {layer: self.at(rel) for layer, rel in pipeline_parts.LEDGERS.items()}

    def is_run(self) -> bool:
        """Whether the directory holds a run.json."""
        return self.file("run.json").is_file()

    def require(self) -> RunDir:
        """The run itself; raises ValueError for a directory without run.json."""
        if not self.is_run():
            raise ValueError(f"not a run directory (no {RUN_JSON}): {self.path}")
        return self

    @cached_property
    def run_json(self) -> dict:
        """run.json, {} when absent."""
        return read_json(self.file("run.json")) or {}

    @property
    def eval_log(self) -> Path | None:
        """The run's eval log, or None when none exists. The log is the file run.json names (log_link), found at the
        first of these places that holds it:
        - the recorded path, from the checkout the run sits in (checkout_of; this checkout for a run under no runs/
          folder), or as it is when absolute;
        - the run's own path under the logs/ beside its runs/, logs/<study>/[<group>/]<run>/<file>.eval, for a run
          moved into or out of a group with its log since it was recorded;
        - the run's own eval/ folder, <run>/eval/<file>.eval, where a collection keeps each run's log inside the run
          (apart_controls, as the dataset holds it).
        Any other .eval file in the log folder (an aborted start's journal) is not the run's log."""
        recorded = self.run_json.get("eval_log")
        if not recorded:
            return None
        path, mirror = Path(recorded), log_dir(self.path)
        candidates = [path if path.is_absolute() else (checkout_of(self.path) or settings.ROOT) / path,
                      *([mirror / path.name] if mirror else []), self.path / IN_RUN_LOGS / path.name]
        return next((candidate for candidate in candidates if candidate.is_file()), None)

    def episodes(self) -> list[dict]:
        """episodes.jsonl: one record per (sample, agent)."""
        return read_jsonl(self.file("episodes.jsonl"))

    def roles(self) -> dict[str, str | None]:
        """The run's agents and their roles (roles_of)."""
        return roles_of(self.run_json, self.episodes())

    def write_run_json_block(self, key: str, value) -> None:
        """Set one key of run.json, keeping every other key, and read run.json again."""
        run = {**(read_json(self.file("run.json")) or {}), key: value}
        self.file("run.json").write_text(json.dumps(run, indent=1))
        self.__dict__.pop("run_json", None)

    def signals(self) -> list[dict]:
        """The code record's agent rows (signals.jsonl)."""
        return rows_of(read_jsonl(self.file("signals.jsonl")), "agent")

    def team_signals(self) -> list[dict]:
        """The code record's team rows (signals.jsonl): one per sample."""
        return rows_of(read_jsonl(self.file("signals.jsonl")), "team")

    def board(self, sample: str) -> dict:
        """The sample's board, communication/<sample>.json ({messages, events}); {} when the run had none."""
        return read_json(self.file("communication") / f"{sample}.json") or {}

    def boards(self) -> list[dict]:
        """Every sample's board (board), in file name order; [] for a run without boards."""
        folder = self.file("communication")
        return [read_json(path) or {} for path in sorted(folder.glob("*.json"))] if folder.is_dir() else []

    def access_log(self) -> dict | None:
        """run.json "access_log" with access_log.jsonl's rows as "rows"; None for a run that kept no log: no block, as
        in a run older than the log, or status off."""
        block = self.run_json.get("access_log")
        if block is None or block["status"] == "off":
            return None
        return {**block, "rows": read_jsonl(self.file("access_log.jsonl"))}

    def transcript_path(self, episode: str) -> Path:
        """Where the export stores the episode's transcript: trajectories/<episode>/transcript.json."""
        return self.file("trajectories") / episode / "transcript.json"

    def transcript(self, episode: str) -> dict:
        """The episode's stored transcript."""
        return read_json(self.transcript_path(episode))

    def with_transcripts(self) -> list[dict]:
        """The episode records whose transcript is stored in the directory."""
        return [ep for ep in self.episodes() if self.transcript_path(ep["episode"]).is_file()]


def rows_of(rows: list[dict], kind: str) -> list[dict]:
    """The rows of one kind ("agent" or "team")."""
    return [row for row in rows if row.get("row") == kind]
