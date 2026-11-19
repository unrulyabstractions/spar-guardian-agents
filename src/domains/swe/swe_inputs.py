"""Lucy's split of each issue and CheatBench's task folder, read only, with the changes our split makes.

Lucy's harness gives splits/<issue>.yaml and pieces/<issue>/ (the piece patches and lines.json). CheatBench's task
folder gives instruction.md, task.toml and tests/config.json (base commit, reference patch, required tests). The issue's
note on names comes from prompts.yaml swe.issue_notes. swe_settings.MOVED_FILES moves whole files between Lucy's pieces,
swe_settings.NEUTRAL_VALUES gives one planted file neutral values and swe_settings.OWN_VALUES puts values of ours where
the fix's two secret values stood, in the pieces (piece_patch) and in the grader's copy of the hidden tests
(grader_tests). The fix's lines follow their files (load_lines).

A run made before OWN_VALUES planted the fix's own values. Its record holds no swe["own_values"], and every function
here takes `own` (owns(swe)) so the ladder reads such a run with the values it planted.
"""
from __future__ import annotations

import ast
import functools
import json
import re
import shlex
from pathlib import Path

import tomllib
import yaml

from src.configuration import settings
from src.domains.swe.swe_settings import HARBOR, HARNESS, MOVED_FILES, NEUTRAL_VALUES, OWN_VALUES, PARTS


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
def moved_blocks(issue: str) -> dict[str, tuple[str, str]]:
    """Each file swe_settings.MOVED_FILES moves, with its block in Lucy's patches and the piece it joins. ValueError
    when a moved file is not in exactly one of Lucy's pieces: only a whole file can move."""
    out = {}
    for path, piece in MOVED_FILES.get(issue, {}).items():
        found = [block for part in PARTS for name, block in file_blocks(lucy_patch(issue, part)) if name == path]
        if len(found) != 1:
            raise ValueError(f"{issue}: {path} is in {len(found)} of Lucy's pieces; a moved file must be in one")
        out[path] = (found[0], piece)
    return out


def run_values(issue: str, honeypot: str) -> dict[str, str]:
    """The values of ours a run of this honeypot plants in place of the fix's (swe_settings.OWN_VALUES). None as
    released: the real fix commit sits in the clones there, so the hidden tests keep the fix's values."""
    return {} if honeypot == "as_released" else dict(OWN_VALUES.get(issue, {}))


def owns(swe: dict) -> bool:
    """Whether the run planted values of ours, read from its record. A run made before OWN_VALUES has none."""
    return bool(swe.get("own_values"))


def swapped(text: str, swaps: dict[str, str]) -> str:
    for old, new in swaps.items():
        text = text.replace(old, new)
    return text


def planted_block(issue: str, path: str, block: str, own: bool = True) -> str:
    """A file's block as a piece plants it: the fix's text, with NEUTRAL_VALUES's values where the file has them, then
    with our values where the fix's two secret values still stand (OWN_VALUES, for a run that plants them). Only the
    lines the block adds change. A changed block loses its index line, which named the fix's own blob."""
    neutral, ours = NEUTRAL_VALUES.get(issue, {}).get(path, {}), OWN_VALUES.get(issue, {}) if own else {}
    lines = block.splitlines(keepends=True)
    # neutral values first: a file that has them keeps them, and holds neither of our values
    out = [swapped(swapped(line, neutral), ours) if line.startswith("+") and not line.startswith("+++") else line
           for line in lines]
    if out == lines:
        return block
    return "".join(line for line in out if not line.startswith("index "))


@functools.cache
def piece_patch(issue: str, piece: str, own: bool = True) -> str:
    """The patch planted as the piece: Lucy's, less the files MOVED_FILES moves out of it, plus the files it moves into
    it, each block as planted_block gives it. The whole fix is Lucy's with our values where the run plants them. With
    own=False the fix's own values stay, as every run before OWN_VALUES planted them."""
    moved = moved_blocks(issue)
    if piece == "whole":
        return "".join(planted_block(issue, "", block, own) for _, block in file_blocks(lucy_patch(issue, piece))) \
            if own and OWN_VALUES.get(issue) else lucy_patch(issue, piece)
    if not (moved or NEUTRAL_VALUES.get(issue) or (own and OWN_VALUES.get(issue))):
        return lucy_patch(issue, piece)
    kept = [block for path, block in file_blocks(lucy_patch(issue, piece)) if path not in moved]
    joined = [block for block, joins in moved.values() if joins == piece]
    return "".join(planted_block(issue, path, block, own) for path, block in file_blocks("".join(kept + joined)))


def load_lines(issue: str, own: bool = True) -> dict:
    """lines.json: `fix` items {line, part, repeated_in?}, `hidden_tests` lines, `generic` lines dropped. Where our
    split changes Lucy's pieces (MOVED_FILES, NEUTRAL_VALUES, OWN_VALUES), each fix line belongs to the pieces whose
    patch adds it (piece_patch): the first is its part and the rest its repeated_in, and a line no piece adds is
    dropped. A line that holds one of the fix's secret values is read as the pieces plant it, with our value, and so
    are the hidden tests' lines."""
    lines = json.loads((HARNESS / "pieces" / issue / "lines.json").read_text())
    values = OWN_VALUES.get(issue, {}) if own else {}
    if not (moved_blocks(issue) or NEUTRAL_VALUES.get(issue) or values):
        return lines
    added = {part: added_lines(piece_patch(issue, part, own)) for part in PARTS}
    fix = []
    for item in lines["fix"]:
        line = swapped(item["line"], values)
        holders = [part for part in PARTS if line in added[part]]
        if holders:
            repeated = {"repeated_in": holders[1:]} if holders[1:] else {}
            fix.append({"line": line, "part": holders[0], **repeated})
    return {**lines, "fix": fix, "hidden_tests": [swapped(line, values) for line in lines["hidden_tests"]]}


SED_PATTERN = re.compile(r"([\\.*^$|\[\]])")
SED_REPLACEMENT = re.compile(r"([\\&|])")


def sed_swap(old: str, new: str) -> str:
    """A sed expression that replaces the text old with new, each taken literally."""
    return "s|" + SED_PATTERN.sub(r"\\\1", old) + "|" + SED_REPLACEMENT.sub(r"\\\1", new) + "|g"


def grader_tests(instance: str, issue: str, own: bool = True) -> dict[str, bytes]:
    """CheatBench's tests/ folder as the grader gets it, by file name. Its test.sh checks the hidden test files out of
    the fix commit with the last line of config.json's before_repo_set_cmd and runs them. Where the run plants values
    of ours (OWN_VALUES), that line also rewrites the fix's values in each test file that holds one, so the tests
    compare the values the pieces hold, and config.json's test_patch says the same. ValueError when no checked-out
    test file holds a value: the tests would then compare values no piece holds."""
    files = {f.name: f.read_bytes() for f in sorted((task_dir(instance) / "tests").iterdir())}
    values = OWN_VALUES.get(issue, {}) if own else {}
    if not values:
        return files
    cfg = json.loads(files["config.json"])
    *setup, checkout = cfg["before_repo_set_cmd"].strip().splitlines()
    checked_out = checkout.split(" -- ", 1)[1].split()
    holders = [path for path, block in file_blocks(cfg["test_patch"])
               if path in checked_out and any(old in block for old in values)]
    if not holders:
        raise ValueError(f"{issue}: no hidden test file holds a value of {sorted(values)}")
    swaps = [part for old, new in values.items() for part in ("-e", shlex.quote(sed_swap(old, new)))]
    edit = " ".join(["sed", "-i", *swaps, *map(shlex.quote, holders)])
    cfg["before_repo_set_cmd"] = "\n".join([*setup, f"{checkout} && {edit}"])
    cfg["test_patch"] = "".join(planted_block(issue, path, block) for path, block in file_blocks(cfg["test_patch"]))
    files["config.json"] = json.dumps(cfg).encode()
    return files


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
