"""What a run's stored files were made from: its two ledgers, processed/versions.json and analysis/versions.json, one
entry per part, and the one writer of a part's stored file.

    stamp(run, "export", made="migration 2026-11-23")   # hash the export's files into its entry
    write_output(run, "topology", value)                # "redone" or "restamped", the old file backed up first
    behind(run)                                         # {part: why}: versions and recorded inputs, for the viewer
    behind(run, deep=True)                              # and every stored file rehashed, for the process command
    part_versions(run)                                  # {part: {"stored": 1, "current": 2}}, for the version chips

A part's entry sits in the ledger of the layer it writes (pipeline_parts.Part.layer), so a label change writes inside
analysis/ only:

    {"parts": {
      "export": {"version": 0, "inputs": {}, "sha256": "sha256:...",
                 "files": {"processed/run.json": ["sha256:...", 18234, 1795357300.1], ...}, "made": "...",
                 "code": "..."},
      "topology": {"version": 1, "inputs": {"export": [0, "sha256:..."], "replay": null},
                 "sha256": "sha256:...", "made": "2026-11-23T10:02:11Z", "code": "abc1234"}}}

An input is [the version, the sha256 of the stored output] of a part it needs, null for a part that stored nothing (no
replay). A computed part's sha256 is its file's; a recorded part's is the sha256 of its file map {layout path: sha256},
and the map keeps each file's sha256, size and mtime, so a rehash reads only the files whose size or mtime moved. A
judge's entry adds "verdicts": per verdict file, the registry's version when that file last changed, and the judges
and prompt hashes its rows name. `made` and `code` live only here; a stored file holds no time and no commit.

Every write goes to <file>.partial and replaces the file in one rename, the file first and its ledger entry after.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import datetime, timezone
from functools import cache
from pathlib import Path

from src.configuration import settings
from src.measures import pipeline_parts, stored_json
from src.measures.run_directory import RunDir

JUDGES = "analysis/judges/"
"""The folder of the judges' verdict files: a part stored there keeps "verdicts" in its entry."""
BACKUPS = settings.ROOT / ".backups" / "pipeline"
"""Where write_output keeps a file it replaces: <stamp>/<the file's path from the repository's root>."""


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def write_atomic(path: Path, data: bytes) -> Path:
    """Write bytes to <path>.partial, then move it into place in one rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_bytes(data)
    os.replace(partial, path)
    return path


def now() -> str:
    """The time of a stamp, UTC to the second: 2026-11-23T10:02:11Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@cache
def code() -> str | None:
    """The checkout's short commit, None where git cannot say."""
    try:
        return subprocess.check_output(["git", "-C", str(settings.ROOT), "rev-parse", "--short", "HEAD"], text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _registry(registry: pipeline_parts.Registry | None) -> pipeline_parts.Registry:
    return registry or pipeline_parts.registry()


def ledger(run: RunDir, layer: str) -> dict[str, dict]:
    """One ledger's entries, {part: entry}; {} when the run has none."""
    path = run.versions()[layer]
    return stored_json.decode(path.name, path.read_bytes())["parts"] if path.is_file() else {}


def read(run: RunDir) -> dict[str, dict]:
    """Both ledgers' entries, {part: entry}."""
    return {**ledger(run, "processed"), **ledger(run, "analysis")}


def entry(run: RunDir, part: str) -> dict | None:
    """The part's entry, None when the run has none."""
    return read(run).get(part)


def map_sha(files: dict[str, list]) -> str:
    """The sha256 of a file map: of {layout path: sha256}, so sizes and mtimes leave it alone."""
    return sha256_bytes(stored_json.dumps({rel: record[0] for rel, record in files.items()}))


def file_map(run: RunDir, part: str, registry: pipeline_parts.Registry | None = None,
             previous: dict[str, list] | None = None) -> dict[str, list]:
    """{layout path: [sha256, size, mtime]} of every file the part stores now. A file whose size and mtime equal the
    previous map's keeps its sha256 unread."""
    previous = previous or {}
    out = {}
    for rel, path in run.outputs(part, _registry(registry)).items():
        info = path.stat()
        old = previous.get(rel)
        same = old is not None and old[1] == info.st_size and old[2] == info.st_mtime
        out[rel] = [old[0] if same else sha256_file(path), info.st_size, info.st_mtime]
    return out


def reference(run: RunDir, part: str, registry: pipeline_parts.Registry | None = None, *, deep: bool = False,
              entries: dict[str, dict] | None = None) -> list | None:
    """What a part that needs this one records of it now: [version, sha256], None when it stored nothing. deep rehashes
    a recorded part's files, so a file changed since its stamp shows; a recorded part with files and no entry gives
    [None, their sha256]."""
    registry = _registry(registry)
    spec = registry.part(part)
    stored = (read(run) if entries is None else entries).get(part)
    if not (deep and spec.recorded):
        return None if stored is None else [stored["version"], stored.get("sha256")]
    files = file_map(run, part, registry, (stored or {}).get("files"))
    if stored is None:
        return [None, map_sha(files)] if files else None
    return [stored["version"], map_sha(files)]


def _verdicts(files: dict[str, list], paths: dict[str, Path], old: dict, version: int | None) -> dict[str, dict]:
    """Per verdict file: the version when it last changed, and the judges and prompt hashes its rows name."""
    out = {}
    for rel, record in files.items():
        kept = old.get(rel)
        rows = stored_json.decode(rel, paths[rel].read_bytes())
        out[rel] = {"version": kept["version"] if kept and kept.get("sha256") == record[0] else version,
                    "sha256": record[0],
                    "judge": sorted({str(row.get("judge")) for row in rows if row.get("judge")}),
                    "prompt_sha256": sorted({str(row["prompt_sha256"]) for row in rows if row.get("prompt_sha256")})}
    return out


def stamp(run: RunDir, part: str, *, registry: pipeline_parts.Registry | None = None, version: int | None = None,
          made: str | None = None) -> dict:
    """Write the part's entry for what it stores now: the version (the registry's unless given; 0 for outputs made
    before the registry), its inputs as the ledgers hold them, the sha256 of its file or file map, made and code.
    Returns the entry."""
    registry = _registry(registry)
    spec = registry.part(part)
    entries = read(run)
    old = entries.get(part) or {}
    record: dict = {"version": spec.version if version is None else version,
                    "inputs": {need: reference(run, need, registry, entries=entries) for need in spec.needs}}
    if spec.recorded:
        files = file_map(run, part, registry, old.get("files"))
        record.update(files=files, sha256=map_sha(files))
        if spec.paths[0].startswith(JUDGES):
            record["verdicts"] = _verdicts(files, run.outputs(part, registry), old.get("verdicts") or {},
                                           record["version"])
    elif not spec.recorded:
        record["sha256"] = sha256_file(run.output(part, registry))
    record.update(made=made or now(), code=code())
    layer_entries = ledger(run, spec.layer)
    layer_entries[part] = record
    write_atomic(run.versions()[spec.layer], stored_json.dumps({"parts": layer_entries}))
    return record


def backup(path: Path, stamp_time: str, backups: Path = BACKUPS) -> Path:
    """Copy a file about to be replaced to <backups>/<stamp>/<its path from the repository's root> (its absolute path
    for a file outside the checkout)."""
    path = Path(path).resolve()
    rel = path.relative_to(settings.ROOT) if path.is_relative_to(settings.ROOT) else path.relative_to(path.anchor)
    target = Path(backups) / stamp_time / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(path.read_bytes())
    return target


def write_output(run: RunDir, part: str, value, *, registry: pipeline_parts.Registry | None = None,
                 backup_stamp: str | None = None, backups: Path = BACKUPS, made: str | None = None) -> str:
    """Store a computed part's value (stored_json.encode) and stamp it. Equal bytes rewrite only the entry:
    "restamped". Otherwise the old file goes to the backups, the new one replaces it and the entry follows: "redone"."""
    registry = _registry(registry)
    target = run.output(part, registry)
    data = stored_json.encode(target.name, value)
    if target.is_file() and target.read_bytes() == data:
        stamp(run, part, registry=registry, made=made)
        return "restamped"
    if target.is_file():
        backup(target, backup_stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), backups)
    write_atomic(target, data)
    stamp(run, part, registry=registry, made=made)
    return "redone"


def behind(run: RunDir, registry: pipeline_parts.Registry | None = None, *, deep: bool = False) -> dict[str, str]:
    """{computed part: why it is behind}, in registry order. A part is behind when it has no entry, its version is not
    the registry's, an input it recorded differs from that input now, or a part it needs is behind. deep also rehashes
    every stored file: the part's own against its entry, a recorded input's against its stamp. A recorded part is
    never behind: processing cannot redo it, and part_versions reports it."""
    registry = _registry(registry)
    entries = read(run)
    refs: dict[str, list | None] = {}
    out: dict[str, str] = {}
    for name in pipeline_parts.order(registry):
        spec = registry.parts[name]
        stored = entries.get(name)
        if spec.recorded:
            refs[name] = reference(run, name, registry, deep=deep, entries=entries)
            continue
        refs[name] = None if stored is None else [stored["version"], stored.get("sha256")]
        if stored is None:
            out[name] = "not processed"
            continue
        why = [] if stored["version"] == spec.version else [f"v{stored['version']} → v{spec.version}"]
        for need in spec.needs:
            if need in out:
                why.append(f"{need} behind")
            elif stored["inputs"].get(need) != refs[need]:
                why.append(f"{need} changed")
        if deep:
            path = run.output(name, registry)
            if not path.is_file():
                why.append("file missing")
            elif sha256_file(path) != stored.get("sha256"):
                why.append("file changed")
        if why:
            out[name] = "; ".join(why)
    return out


def part_versions(run: RunDir, registry: pipeline_parts.Registry | None = None) -> dict[str, dict]:
    """{part: {"stored": the entry's version or None, "current": the registry's}}, every part in registry order."""
    registry = _registry(registry)
    entries = read(run)
    return {name: {"stored": (entries.get(name) or {}).get("version"), "current": registry.parts[name].version}
            for name in pipeline_parts.order(registry)}
