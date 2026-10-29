"""Lucy's split of each issue and CheatBench's task folder, read only.

Lucy's harness gives splits/<issue>.yaml and pieces/<issue>/ (the piece patches and lines.json). CheatBench's task
folder gives instruction.md, task.toml and tests/config.json (base commit, reference patch, required tests).
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import tomllib
import yaml

from src.benchmark.domains.swe.swe_settings import HARBOR, HARNESS


def issues() -> list[str]:
    return sorted(p.stem for p in (HARNESS / "splits").glob("*.yaml"))


def load_split(issue: str) -> dict:
    path = HARNESS / "splits" / f"{issue}.yaml"
    if not path.exists():
        raise ValueError(f"unknown issue {issue!r}; choose one of {issues()}")
    return yaml.safe_load(path.read_text())


def load_lines(issue: str) -> dict:
    """lines.json: `fix` items {line, part, repeated_in?}, `hidden_tests` lines, `generic` lines dropped."""
    return json.loads((HARNESS / "pieces" / issue / "lines.json").read_text())


def piece_patch(issue: str, piece: str) -> str:
    return (HARNESS / "pieces" / issue / f"{piece}.patch").read_text()


def task_dir(instance: str) -> Path:
    path = HARBOR / instance
    if not path.is_dir():
        raise FileNotFoundError(path)
    return path


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
