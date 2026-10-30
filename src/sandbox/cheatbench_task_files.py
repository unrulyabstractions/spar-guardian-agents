"""CheatBench's task folder and its built rows, as every domain reads them: task.yaml, data.jsonl, the instruction
template, grader.py, and the metadata block a sample carries.

    cfg = load_task_yaml(task_dir)
    row = load_row(load_rows(build_dir, hint="..."), row_id, build_dir)
    instruction = render_prompt(task_dir / cfg["prompts"]["instruction"], row) + suffix
    metadata = {"cheatbench": sample_metadata(cfg, row, instruction)}
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import yaml
from inspect_ai.tool import ToolParam, ToolParams


def load_task_yaml(task_dir: Path) -> dict:
    return yaml.safe_load((task_dir / "task.yaml").read_text())


def load_rows(build_dir: Path, *, hint: str) -> list[dict]:
    """The rows of build_dir/data.jsonl; `hint` says how to build the task when the file is missing."""
    data = build_dir / "data.jsonl"
    if not data.exists():
        raise FileNotFoundError(f"{data} missing: build the task first{hint}")
    return [json.loads(line) for line in data.read_text().splitlines() if line.strip()]


def load_row(rows: list[dict], row_id: str, build_dir: Path) -> dict:
    row = next((r for r in rows if r["id"] == row_id), None)
    if row is None:
        raise ValueError(f"no row {row_id!r} in {build_dir / 'data.jsonl'}")
    return row


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


def load_grader(task_dir: Path, name: str):
    """CheatBench's grader.py of a task as a module named `name`, with no __pycache__ in the submodule's task folder.
    Each grader's top level imports the standard library only; the Claude SDK import sits inside make_grader, which
    is never called here."""
    spec = importlib.util.spec_from_file_location(name, task_dir / "grader.py")
    module = importlib.util.module_from_spec(spec)
    before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = before
    return module


def _param(schema: dict, description: str | None) -> ToolParam:
    items = _param(schema["items"], None) if "items" in schema else None
    return ToolParam(type=schema["type"], items=items, description=description)


def tool_params(schema: dict, descriptions: dict[str, str]) -> ToolParams:
    """A grader's INPUT_SCHEMA as Inspect's parameters, each with our description: CheatBench's schemas give none
    and Inspect requires one (a deviation every domain records)."""
    properties = {name: _param(prop, descriptions[name]) for name, prop in schema["properties"].items()}
    return ToolParams(properties=properties, required=list(schema["required"]))
