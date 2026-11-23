"""The pipeline's registry, src/configuration/pipeline_parts.yaml: every part a run's stored files come from, its
version, the code it is made by and what it needs. The one reader and the one writer of that file.

    registry = load()                               # the repository's registry (cached: registry())
    registry.parts["export"].version                # 1
    fingerprint(registry, "export")                 # sha256 over the part's files and pins, as it stands now
    order(registry)                                 # every part after the parts it needs
    problems(registry)                              # what the guard (tests/unit/test_pipeline_parts.py) refuses
    add(registry, "topology", "first version: ...")  # lock an entry written into the yaml at version 1, then save()
    bump(registry, "topology", "what changed")      # version + 1, a log line, the fingerprint now
    re_lock(registry, "topology", "why no output changes")  # the fingerprint now, the version kept

A **recorded** part (`make: null`) is stamped when it happens and never run by processing. A **computed** part has a
`make`, "module:function"; its `file` under processed/facts/ makes it a fact, under analysis/ a derived part. A part's
`file` is one path or a list of paths from the run's folder in the stored layout (processed/..., analysis/...): a path
ending in "/" stands for every file under that folder, a "*" in its last name for the files it matches.

A part's `files` are patterns from the repository's root (pathlib globs, `**` for any depth); two parts may list the
same file. Its `pins` are values outside those files: `submodule:<name>` (settings.EXPECTED_SUBMODULE_HEADS),
`settings:<NAME>` (the value's repr) and `package:<distribution>` (its installed version). The fingerprint is the
sha256 over the matched files, sorted by path, as `path\\0bytes\\0`, then each pin, sorted, as `pin=value\\0`.
"""
from __future__ import annotations

import fnmatch
import hashlib
import importlib
import importlib.metadata
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from pathlib import Path

import yaml

from src.configuration import settings

REGISTRY = settings.ROOT / "src" / "configuration" / "pipeline_parts.yaml"
LAYERS = ("processed", "analysis")
"""The two folders of a run that parts write, each with its own ledger (src/measures/stored_versions.py)."""
LEDGERS = {"processed": "processed/versions.json", "analysis": "analysis/versions.json"}
"""Each layer's ledger, from the run's folder."""
FACTS = "processed/facts/"
SKIPPED = ("__pycache__", ".DS_Store")
"""Names a pattern never matches: Python's caches and Finder's files differ between checkouts."""
KEYS = ("file", "make", "needs", "files", "pins", "version", "fingerprint", "log")
HEADER = """\
# The pipeline's parts: every part a run's stored files come from, its version and the code it is made by.
# Read and written only by src/measures/pipeline_parts.py; tests/unit/test_pipeline_parts.py is the guard.
#
# To add a part: write its module, write its entry below with file, make, needs, files and pins, run
# `python -m src pipeline add <part> "<what the first version does>"`, and describe its file and fields in
# scripts/study/study_readme_fields.yaml.
# When a part's code changes its output: `python -m src pipeline bump <part> "<what changed>"`.
# When it changes no stored byte: `python -m src pipeline re-lock <part> "<why no output changes>"`.
# `python -m src pipeline status` lists every part and whether its code moved since it was locked.
#
# files: the code a part's fingerprint covers. Two parts may list the same file.
# make: null marks a recorded part, stamped when it happens and never run by processing.
"""


@dataclass
class Part:
    """One entry of the registry. version, fingerprint and log stay empty until `add` locks the entry."""

    name: str
    file: str | list[str]
    make: str | None = None
    needs: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    pins: list[str] = field(default_factory=list)
    version: int | None = None
    fingerprint: str | None = None
    log: dict[int, str] = field(default_factory=dict)

    @property
    def recorded(self) -> bool:
        """Whether processing never makes this part: it is stamped when it happens."""
        return self.make is None

    @property
    def paths(self) -> list[str]:
        """The part's stored paths, from the run's folder."""
        return [self.file] if isinstance(self.file, str) else list(self.file)

    @property
    def layer(self) -> str:
        """processed or analysis: the folder the part writes, and so the ledger that holds its entry."""
        return self.paths[0].split("/", 1)[0]

    @property
    def kind(self) -> str:
        """recorded, fact or derived."""
        if self.recorded:
            return "recorded"
        return "fact" if self.paths[0].startswith(FACTS) else "derived"

    def entry(self) -> dict:
        """The entry as the yaml holds it, in its fixed key order."""
        out = {"file": self.file, "make": self.make, "needs": list(self.needs), "files": list(self.files),
               "pins": list(self.pins)}
        if self.version is not None:
            out.update(version=self.version, fingerprint=self.fingerprint, log=dict(sorted(self.log.items())))
        return out


@dataclass
class Registry:
    """The parts in file order. root is the checkout their patterns read."""

    parts: dict[str, Part]
    root: Path = settings.ROOT
    path: Path = REGISTRY

    def part(self, name: str) -> Part:
        if name not in self.parts:
            raise ValueError(f"no part {name!r} in {self.path.name}; parts: {', '.join(self.parts)}")
        return self.parts[name]


def parse(document: dict, *, root: Path = settings.ROOT, path: Path = REGISTRY) -> Registry:
    """A registry from its yaml document."""
    unknown = set(document) - {"parts"}
    if unknown:
        raise ValueError(f"{Path(path).name} has unknown keys {', '.join(sorted(unknown))}")
    parts = {}
    for name, entry in (document.get("parts") or {}).items():
        unknown = set(entry) - set(KEYS)
        if unknown:
            raise ValueError(f"{Path(path).name}: part {name} has unknown keys {', '.join(sorted(unknown))}")
        parts[name] = Part(name=name, file=entry["file"], make=entry.get("make"), needs=list(entry.get("needs") or []),
                           files=list(entry.get("files") or []), pins=list(entry.get("pins") or []),
                           version=entry.get("version"), fingerprint=entry.get("fingerprint"),
                           log={int(k): v for k, v in (entry.get("log") or {}).items()})
    return Registry(parts=parts, root=Path(root), path=Path(path))


def load(path: Path = REGISTRY, *, root: Path = settings.ROOT) -> Registry:
    """The registry in this file."""
    return parse(yaml.safe_load(Path(path).read_text()) or {}, root=root, path=path)


@cache
def registry() -> Registry:
    """The repository's registry, read once per process. Callers do not change what it returns."""
    return load()


class _Dumper(yaml.SafeDumper):
    """Lists in flow style, maps in block style: `needs: [export]` under each part."""


_Dumper.add_representer(list, lambda dumper, value: dumper.represent_sequence("tag:yaml.org,2002:seq", value,
                                                                              flow_style=True))


def dumps(registry: Registry) -> str:
    """The registry's yaml, the header first: the one form save writes."""
    document = {"parts": {name: part.entry() for name, part in registry.parts.items()}}
    return HEADER + yaml.dump(document, Dumper=_Dumper, sort_keys=False, width=100, allow_unicode=True,
                              default_flow_style=False)


def save(registry: Registry) -> Path:
    """Write the registry to its file in one rename."""
    partial = registry.path.with_name(registry.path.name + ".partial")
    partial.write_text(dumps(registry))
    partial.replace(registry.path)
    return registry.path


def matched(patterns: list[str], root: Path = settings.ROOT) -> dict[str, list[str]]:
    """{pattern: the files it matches, from the root, sorted}. Folders, caches and Finder files never match."""
    root = Path(root)
    out = {}
    for pattern in patterns:
        paths = root.glob(pattern) if any(c in pattern for c in "*?[") else [root / pattern]
        out[pattern] = sorted(p.relative_to(root).as_posix() for p in paths
                              if p.is_file() and not any(part in SKIPPED for part in p.relative_to(root).parts))
    return out


def code_files(registry: Registry, name: str) -> list[str]:
    """Every file the part's patterns match, once each, sorted: what its fingerprint covers."""
    return sorted({path for paths in matched(registry.part(name).files, registry.root).values() for path in paths})


def pin_value(pin: str) -> str:
    """A pin's value now: submodule:<name>, settings:<NAME> or package:<distribution>."""
    kind, _, name = pin.partition(":")
    if kind == "submodule":
        return settings.EXPECTED_SUBMODULE_HEADS[name]
    if kind == "settings":
        return repr(getattr(settings, name))
    if kind == "package":
        return importlib.metadata.version(name)
    raise ValueError(f"unknown pin {pin!r}: submodule:<name>, settings:<NAME> or package:<distribution>")


def fingerprint(registry: Registry, name: str) -> str:
    """The part's fingerprint now: its files, then its pins."""
    digest = hashlib.sha256()
    for rel in code_files(registry, name):
        digest.update(rel.encode() + b"\0" + (registry.root / rel).read_bytes() + b"\0")
    for pin in sorted(registry.part(name).pins):
        digest.update(f"{pin}={pin_value(pin)}\0".encode())
    return "sha256:" + digest.hexdigest()


def order(registry: Registry) -> list[str]:
    """Every part after the parts it needs, in file order otherwise. Raises ValueError on a cycle or an unknown need."""
    done: list[str] = []
    visiting: list[str] = []

    def visit(name: str) -> None:
        if name in done:
            return
        if name in visiting:
            raise ValueError(f"the needs have a cycle: {' -> '.join([*visiting[visiting.index(name):], name])}")
        if name not in registry.parts:
            raise ValueError(f"{visiting[-1]} needs {name}, which is no part")
        visiting.append(name)
        for need in registry.parts[name].needs:
            visit(need)
        visiting.pop()
        done.append(name)

    for name in registry.parts:
        visit(name)
    return done


def upstream(registry: Registry, name: str) -> set[str]:
    """Every part this one needs, directly or through another."""
    found: set[str] = set()
    todo = list(registry.part(name).needs)
    while todo:
        need = todo.pop()
        if need not in found:
            found.add(need)
            todo.extend(registry.part(need).needs)
    return found


def downstream(registry: Registry, name: str) -> set[str]:
    """Every part that needs this one, directly or through another."""
    return {other for other in registry.parts if name in upstream(registry, other)}


def _overlap(a: str, b: str) -> bool:
    """Whether two stored paths can name one file."""
    if a == b:
        return True
    for x, y in ((a, b), (b, a)):
        if x.endswith("/") and y.startswith(x):
            return True
        if "*" in x and fnmatch.fnmatchcase(y, x):
            return True
    return False


def make_problems(part: Part) -> list[str]:
    """Whether a computed part's make, "module:function", imports and names a callable."""
    module, _, function = (part.make or "").partition(":")
    if not module or not function:
        return [f"{part.name}: make {part.make!r} is not module:function"]
    try:
        target = getattr(importlib.import_module(module), function, None)
    except Exception as error:  # noqa: BLE001 - any failure to import is the finding
        return [f"{part.name}: make {part.make} does not import: {type(error).__name__}: {error}"]
    return [] if callable(target) else [f"{part.name}: make {part.make} names no callable"]


def shape_problems(registry: Registry) -> list[str]:
    """The entries' form: the needs make a graph with no cycle, every version has its log line, every stored path lies
    under processed/ or analysis/ in one layer, a fact needs no label, a computed part stores one file and its make
    imports, and no stored path is two parts'."""
    out = []
    try:
        order(registry)
    except ValueError as error:
        out.append(str(error))
    for name, part in registry.parts.items():
        if part.version is None or part.fingerprint is None:
            out.append(f"{name} is not added: run `python -m src pipeline add {name} \"<what it does>\"`")
        else:
            out += [f"{name} v{v} has no log line" for v in range(1, part.version + 1) if not part.log.get(v)]
        out += [f"{name}: {path} lies outside {' and '.join(f'{x}/' for x in LAYERS)}" for path in part.paths
                if path.split("/", 1)[0] not in LAYERS or path.count("/") == 0]
        if len({path.split("/", 1)[0] for path in part.paths}) > 1:
            out.append(f"{name} writes both processed/ and analysis/; one part writes one layer")
        for need in part.needs:
            other = registry.parts.get(need)
            if other is None:
                continue
            if part.recorded and not other.recorded:
                out.append(f"{name} is recorded and needs {need}, which processing computes")
            if part.layer == "processed" and other.layer == "analysis":
                out.append(f"{name} writes processed/ and needs {need}, which writes analysis/: a label would change "
                           f"a fact")
        if not part.recorded:
            path = part.paths[0]
            if len(part.paths) != 1 or "*" in path or path.endswith("/"):
                out.append(f"{name} is computed and needs one file, not {part.file}")
            elif part.layer == "processed" and not path.startswith(FACTS):
                out.append(f"{name} is computed under processed/ outside {FACTS}")
            out += make_problems(part)
    items = list(registry.parts.items())
    for i, (a_name, a) in enumerate(items):
        for b_name, b in items[i + 1:]:
            out += [f"{a_name} and {b_name} both store {x if len(x) <= len(y) else y}"
                    for x in a.paths for y in b.paths if _overlap(x, y)]
    return out


def pattern_problems(registry: Registry) -> list[str]:
    """A part with no code pattern, and a pattern that matches no file."""
    out = []
    for name, part in registry.parts.items():
        if not part.files:
            out.append(f"{name} lists no files: its fingerprint would cover no code")
        out += [f"{name}: {pattern} matches no file" for pattern, paths in matched(part.files, registry.root).items()
                if not paths]
    return out


def fingerprint_problems(registry: Registry) -> list[str]:
    """A part whose fingerprint differs from the one recorded."""
    out = []
    for name, part in registry.parts.items():
        if part.fingerprint is not None and fingerprint(registry, name) != part.fingerprint:
            out.append(f"{name} changed since v{part.version} was locked. If its output changes: `python -m src "
                       f"pipeline bump {name} \"<what changed>\"`; if no stored byte changes: `python -m src pipeline "
                       f"re-lock {name} \"<why>\"`")
    return out


def problems(registry: Registry) -> list[str]:
    """Everything the guard refuses."""
    return [*fingerprint_problems(registry), *pattern_problems(registry), *shape_problems(registry)]


def _today() -> str:
    return date.today().isoformat()


def add(registry: Registry, name: str, line: str) -> Part:
    """Lock an entry written into the yaml without a version: version 1, its log line and its fingerprint."""
    part = registry.part(name)
    if part.version is not None:
        raise ValueError(f"{name} is at v{part.version} already; bump it or re-lock it")
    part.version, part.log = 1, {1: f"{_today()} {line}"}
    part.fingerprint = fingerprint(registry, name)
    return part


def bump(registry: Registry, name: str, line: str) -> Part:
    """A new version of the part: version + 1, its log line and the fingerprint now."""
    part = registry.part(name)
    if part.version is None:
        raise ValueError(f"{name} is not added yet: python -m src pipeline add {name} \"<what it does>\"")
    part.version += 1
    part.log[part.version] = f"{_today()} {line}"
    part.fingerprint = fingerprint(registry, name)
    return part


def re_lock(registry: Registry, name: str, why: str) -> Part:
    """The fingerprint now, the version kept: for a change that moves no stored byte. The current log line records
    it. Raises ValueError when the fingerprint did not move."""
    part = registry.part(name)
    if part.version is None:
        raise ValueError(f"{name} is not added yet: python -m src pipeline add {name} \"<what it does>\"")
    now = fingerprint(registry, name)
    if now == part.fingerprint:
        raise ValueError(f"{name} is locked at its current code already")
    part.fingerprint = now
    part.log[part.version] = f"{part.log[part.version]} (re-locked {_today()}: {why})"
    return part


def state(registry: Registry, name: str) -> str:
    """locked, changed (the code moved since the lock) or not added."""
    part = registry.part(name)
    if part.version is None:
        return "not added"
    return "locked" if fingerprint(registry, name) == part.fingerprint else "changed"
