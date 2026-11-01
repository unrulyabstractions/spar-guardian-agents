"""Lucy's split of each issue and CheatBench's task folder, read only, with the files our split moves.

Lucy's harness gives splits/<issue>.yaml and pieces/<issue>/ (the piece patches and lines.json). CheatBench's task
folder gives instruction.md, task.toml and tests/config.json (base commit, reference patch, required tests). The issue's
note on names comes from prompts.yaml swe.issue_notes. swe_settings.MOVED_FILES moves whole files between Lucy's pieces
(piece_patch), and the fix's lines follow their files (load_lines).
"""
from __future__ import annotations

import ast
import functools
import json
import re
from pathlib import Path

import tomllib
import yaml

from src.configuration import settings
from src.domains.swe.swe_settings import HARBOR, HARNESS, MOVED_FILES, PARTS


def issues() -> list[str]:
    return sorted(p.stem for p in (HARNESS / "splits").glob("*.yaml"))


def load_split(issue: str) -> dict:
    path = HARNESS / "splits" / f"{issue}.yaml"
    if not path.exists():
        raise ValueError(f"unknown issue {issue!r}; choose one of {issues()}")
    return yaml.safe_load(path.read_text())


def file_blocks(patch: str) -> list[tuple[str, str]]:
    """Each file's block of a git patch with the file's path, in the patch's order."""
    out = []
    for block in re.split(r"(?m)^(?=diff --git )", patch):
        m = re.match(r"diff --git a/\S+ b/(\S+)", block)
        if m:
            out.append((m.group(1), block))
    return out


def added_lines(patch: str) -> set[str]:
    """The lines a patch adds, stripped as lines.json writes them."""
    return {line[1:].strip() for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")}


@functools.cache
def lucy_patch(issue: str, piece: str) -> str:
    """The piece's patch as Lucy's harness cut it, read once per process."""
    return (HARNESS / "pieces" / issue / f"{piece}.patch").read_text()


@functools.cache
def moved_blocks(issue: str) -> dict[str, tuple[str, str | None]]:
    """Each file swe_settings.MOVED_FILES moves, with its block in Lucy's patches and the piece it joins (None: no
    piece). ValueError when a moved file is not in exactly one of Lucy's pieces: only a whole file can move."""
    out = {}
    for path, piece in MOVED_FILES.get(issue, {}).items():
        found = [block for part in PARTS for name, block in file_blocks(lucy_patch(issue, part)) if name == path]
        if len(found) != 1:
            raise ValueError(f"{issue}: {path} is in {len(found)} of Lucy's pieces; a moved file must be in one")
        out[path] = (found[0], piece)
    return out


@functools.cache
def piece_patch(issue: str, piece: str) -> str:
    """The patch planted as the piece: Lucy's, less the files MOVED_FILES moves out of it, plus the files it moves into
    it, each block as Lucy's patches hold it. The whole fix stays Lucy's."""
    moved = moved_blocks(issue)
    if piece == "whole" or not moved:
        return lucy_patch(issue, piece)
    kept = [block for path, block in file_blocks(lucy_patch(issue, piece)) if path not in moved]
    return "".join(kept + [block for block, joins in moved.values() if joins == piece])


def load_lines(issue: str) -> dict:
    """lines.json: `fix` items {line, part, repeated_in?}, `hidden_tests` lines, `generic` lines dropped. Where our
    split moves files (MOVED_FILES), each fix line belongs to the pieces whose patch adds it (piece_patch): the first is
    its part and the rest its repeated_in, and a line no piece adds is dropped."""
    lines = json.loads((HARNESS / "pieces" / issue / "lines.json").read_text())
    if not moved_blocks(issue):
        return lines
    added = {part: added_lines(piece_patch(issue, part)) for part in PARTS}
    fix = []
    for item in lines["fix"]:
        holders = [part for part in PARTS if item["line"] in added[part]]
        if holders:
            repeated = {"repeated_in": holders[1:]} if holders[1:] else {}
            fix.append({"line": item["line"], "part": holders[0], **repeated})
    return {**lines, "fix": fix}


def task_dir(instance: str) -> Path:
    path = HARBOR / instance
    if not path.is_dir():
        raise FileNotFoundError(path)
    return path


def issue_note(issue: str) -> str | None:
    """The issue's note on the names its hidden tests use, or None for an issue that needs none."""
    return settings.load_prompts()["swe"]["issue_notes"].get(issue)


def instruction_text(issue: str, instance: str) -> str:
    """CheatBench's instruction, then the issue's note on its own paragraph."""
    text = (task_dir(instance) / "instruction.md").read_text()
    note = issue_note(issue)
    return f"{text}\n{note}\n" if note else text


def task_config(instance: str) -> dict:
    return json.loads((task_dir(instance) / "tests" / "config.json").read_text())


def agent_timeout_s(instance: str) -> int:
    """CheatBench's own agent timeout (task.toml [agent] timeout_sec)."""
    return int(tomllib.loads((task_dir(instance) / "task.toml").read_text())["agent"]["timeout_sec"])


def fix_commit(instance: str) -> str:
    """The fix commit is the 40-hex id in the instance name (Lucy's config.fix_commit)."""
    return re.search(r"__[\w.-]+?-([0-9a-f]{40})", instance).group(1)


def required_tests(cfg: dict) -> list[str]:
    out = []
    for key in ("fail_to_pass", "pass_to_pass"):
        value = cfg.get(key) or []
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                value = ast.literal_eval(value)
        out += list(value)
    return sorted(set(out))


def patch_files(patch: str) -> list[dict]:
    """Each file of the reference patch with its added and removed line counts."""
    out = []
    for block in re.split(r"(?m)^(?=diff --git )", patch):
        m = re.match(r"diff --git a/(\S+) b/(\S+)", block)
        if not m:
            continue
        body = [ln for ln in block.splitlines() if not ln.startswith(("+++", "---"))]
        out.append({"path": m.group(2), "added": sum(ln.startswith("+") for ln in body),
                    "removed": sum(ln.startswith("-") for ln in body)})
    return out
