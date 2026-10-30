"""CheatBench's task folder and its built rows, as every domain reads them: task.yaml, data.jsonl, a row's staged
files, the instruction template, grader.py and build.py, and the metadata block a sample carries.

    cfg = load_task_yaml(task_dir)
    row = load_row(build_dir, row_id, hint="...")
    files = row_files(row, build_dir / "environment", workdir="/workspace")
    instruction = render_prompt(task_dir / cfg["prompts"]["instruction"], row) + suffix
    metadata = {"cheatbench": sample_metadata(cfg, row, instruction)}
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import yaml
from inspect_ai.tool import ToolParam, ToolParams

from src.sandbox import staging
from src.utils.json_files import read_jsonl


def load_task_yaml(task_dir: Path) -> dict:
    return yaml.safe_load((task_dir / "task.yaml").read_text())


def load_rows(build_dir: Path, *, hint: str = "") -> list[dict]:
    """The rows of build_dir/data.jsonl; `hint` says how to build the task when the file is missing."""
    data = build_dir / "data.jsonl"
    if not data.exists():
        raise FileNotFoundError(f"{data} missing: build the task first{hint}")
    return read_jsonl(data)


def load_row(build_dir: Path, row_id: str, *, hint: str = "") -> dict:
    """One row of build_dir/data.jsonl by its id."""
    row = next((r for r in load_rows(build_dir, hint=hint) if r["id"] == row_id), None)
    if row is None:
        raise ValueError(f"no row {row_id!r} in {build_dir / 'data.jsonl'}")
    return row


def row_files(row: dict, env_dir: Path, *, workdir: str | None) -> dict[str, bytes]:
    """The files CheatBench stages for the row (core/stage.py), {destination: bytes} read from the build's
    environment directory: `files_abs` at their absolute paths and `files` under `workdir` (relative to it when
    workdir is None)."""
    out = {}
    for dest, src in row.get("files_abs", {}).items():
        staging.check_destination(dest, absolute=True)
        out[dest] = staging.source_path(env_dir, src).read_bytes()
    for name, src in row.get("files", {}).items():
        staging.check_destination(name, absolute=False)
        out[f"{workdir}/{name}" if workdir else name] = staging.source_path(env_dir, src).read_bytes()
    return out


def row_mtimes(row: dict) -> dict[str, int]:
    """The row's file_mtimes as epochs, by destination."""
    return {dest: staging.parse_mtime(when) for dest, when in (row.get("file_mtimes") or {}).items()}


def row_modes(row: dict) -> dict[str, int]:
    """The row's file_modes as integers, by destination."""
    return {dest: int(str(mode), 8) for dest, mode in (row.get("file_modes") or {}).items()}


def render_prompt(template: Path, row: dict) -> str:
    """CheatBench core/trial.py:render_prompt, reproduced verbatim in behavior: {{input}} and the row's vars, lines
    whose every placeholder is blank dropped, runs of blank lines folded."""
    fields = {"input": row.get("input") or "", **(row.get("vars") or {})}
    out = []
    for line in template.read_text().split("\n"):
        tokens = [k for k in fields if "{{" + k + "}}" in line]
        if tokens and all(not str(fields[k]).strip() for k in tokens):
            continue
        for k, v in fields.items():
            line = line.replace("{{" + k + "}}", str(v))
        out.append(line)
    text = "\n".join(out)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip() + "\n"


def sample_metadata(cfg: dict, row: dict, instruction: str) -> dict:
    """The metadata["cheatbench"] fields every row-based domain carries: the task, the row, the instruction, its
    prompt file, the task's timeout and network policy. A domain adds its own block beside them."""
    return {"task": cfg["name"], "variant": "", "row": row, "instruction": instruction,
            "prompt_file": cfg["prompts"]["instruction"], "timeout_s": int(cfg["timeout_s"]),
            "network": cfg.get("network")}


def load_module(path: Path, name: str, *, stand_ins: dict[str, types.ModuleType] | None = None) -> types.ModuleType:
    """A file of CheatBench's task folder (grader.py, build.py) as a module named `name`, with no __pycache__ in the
    submodule. `stand_ins` replace modules the file imports for the time of the import; sys.path and sys.modules are
    as they were afterwards. A grader's top level imports the standard library only; the Claude SDK import sits
    inside make_grader, which is never called here."""
    saved_path, saved_flag = list(sys.path), sys.dont_write_bytecode
    saved = {key: sys.modules.get(key) for key in stand_ins or {}}
    sys.modules.update(stand_ins or {})
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = saved_path
        sys.dont_write_bytecode = saved_flag
        for key, module_before in saved.items():
            if module_before is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = module_before
    return module


def load_grader(task_dir: Path, name: str) -> types.ModuleType:
    """CheatBench's grader.py of a task (load_module)."""
    return load_module(task_dir / "grader.py", name)


def _param(schema: dict, description: str | None) -> ToolParam:
    items = _param(schema["items"], None) if "items" in schema else None
    return ToolParam(type=schema["type"], items=items, description=description)


def tool_params(schema: dict, descriptions: dict[str, str]) -> ToolParams:
    """A grader's INPUT_SCHEMA as Inspect's parameters, each with our description: CheatBench's schemas give none
    and Inspect requires one (a deviation every domain records)."""
    properties = {name: _param(prop, descriptions[name]) for name, prop in schema["properties"].items()}
    return ToolParams(properties=properties, required=list(schema["required"]))
