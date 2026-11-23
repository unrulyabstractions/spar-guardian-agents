"""A run's files are reached only through the path table (src/measures/run_directory.py: FILES, RunDir.file,
RunDir.output). This test fails on a run file's name written as a path anywhere else in the code, so no reader or
writer keeps the older layout, where the files sat at the run folder's top.

A file's name with a dot (run.json, episodes.jsonl, ...) fails as any string that is the name or ends in /<name>. A
folder's name (trajectories, communication, ...) fails only where it is joined to a path: an operand of `/`, or an
argument of Path, joinpath, glob or rglob. ALLOWED holds the joins that name no run folder (the repository's own tools/
and build/swe/, the paper's figures/). A test may write a path of the stored layout itself (runs/s/r/processed/run.json,
run / "processed" / "trajectories"), as the dataset's paths and a fixture's files are; the code never does.
"""
from __future__ import annotations

import ast
from pathlib import Path

from src.configuration import settings
from src.measures.run_directory import FILES

FOLDERS_SCANNED = ("src", "scripts", "tools", "examples", "studies", "tests")
EXEMPT = {"src/measures/run_directory.py", "tests/unit/test_run_paths.py"}
"""The path table itself and this test."""
OLDER = ("judge.jsonl", "ladder.jsonl", "ladder_actions.jsonl", "rungs.json", "smoke_results.json")
"""The archive's older ladders' files, which no code reads any more."""
DOTTED = {name for name in FILES if "." in name} | set(OLDER)
FOLDERS = {name for name in FILES if "." not in name}
JOINS = {"Path", "joinpath", "glob", "rglob"}
LAYOUT = {name: path.rstrip("/") for name, path in FILES.items()} | {name: f"analysis/{name}" for name in OLDER}
LAYERS = ("processed", "analysis")
ALLOWED = {
    ("src/domains/swe/swe_settings.py", "swe"),        # build/swe/
    ("tools/hawk/hawk_scan_config.py", "tools"),       # the repository's tools/
    ("tools/contagion/contagion_observed.py", "tools"),
    ("scripts/paper/domain_figures.py", "figures"),    # paper/figures/
    ("scripts/paper/fig_deep_dive.py", "figures"),
    ("scripts/paper/fig_jigsaw.py", "figures"),
    ("scripts/paper/fig_multidomain.py", "figures"),
    ("scripts/paper/fig_roles_access.py", "figures"),
    ("scripts/paper/fig_scripted_teammates.py", "figures"),
    ("scripts/paper/figures.py", "figures"),
    ("scripts/paper/paper_sources.py", "figures"),
    ("tests/unit/test_launch_command.py", "swe"),      # examples/swe/
    ("tests/unit/test_swe_task.py", "swe"),
    ("tests/unit/test_transcript_viewer_levels.py", "ladder.jsonl"),   # words the viewer's code must not hold
    ("tests/unit/test_transcript_viewer_levels.py", "judge.jsonl"),
}


def docstrings(tree: ast.AST) -> set[int]:
    """The ids of every docstring's constant, and of every name given to the path table: run.file("run.json"),
    FILES["trajectories"]."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "file":
            out.update(id(arg) for arg in node.args)
        if isinstance(node, ast.Subscript) and getattr(node.value, "id", "") == "FILES":
            out.add(id(node.slice))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                out.add(id(first.value))
    return out


def texts(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return [part.value for part in node.values if isinstance(part, ast.Constant)]
    return []


def in_layout(text: str, name: str) -> bool:
    """Whether a path names the file at its place in the stored layout: it ends in processed/... or analysis/..."""
    return text.rstrip("/") == LAYOUT[name] or text.rstrip("/").endswith("/" + LAYOUT[name])


def after_layer(node: ast.BinOp) -> bool:
    """Whether a `/` join's left side ends in a layer's name: run / "processed" / "trajectories"."""
    left = node.left
    right = left.right if isinstance(left, ast.BinOp) else left
    return any(text.rstrip("/").rsplit("/", 1)[-1] in LAYERS for text in texts(right))


def offenders(path: Path, rel: str) -> list[str]:
    tree = ast.parse(path.read_text())
    layout_ok = rel.startswith("tests/")
    skip = docstrings(tree)
    found = []
    for node in ast.walk(tree):
        if id(node) in skip or not isinstance(node, (ast.Constant, ast.JoinedStr)):
            continue
        for text in texts(node):
            if " " in text or "\n" in text:
                continue
            last = text.rstrip("/").rsplit("/", 1)[-1]
            if last in DOTTED and (text == last or "/" in text) and not (layout_ok and in_layout(text, last)) \
                    and (rel, last) not in ALLOWED:
                found.append(f"{rel}:{node.lineno}: {text!r}")
    for node in ast.walk(tree):
        operands = []
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            operands = [] if layout_ok and after_layer(node) else [node.left, node.right]
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            operands = list(node.args) if name in JOINS else []
        for operand in operands:
            for text in texts(operand):
                parts = text.strip("/").split("/")
                for at, part in enumerate(parts):
                    if layout_ok and at and parts[at - 1] in LAYERS:
                        continue
                    if part in FOLDERS and (rel, part) not in ALLOWED:
                        found.append(f"{rel}:{operand.lineno}: {text!r} joined to a path")
    return found


def test_no_run_file_path_outside_the_path_table():
    found = []
    for folder in FOLDERS_SCANNED:
        for path in sorted((settings.ROOT / folder).rglob("*.py")):
            rel = path.relative_to(settings.ROOT).as_posix()
            if rel not in EXEMPT and ".venv" not in path.parts:
                found += offenders(path, rel)
    assert not found, "run files named outside src/measures/run_directory.py (use RunDir.file):\n" + "\n".join(found)


def test_the_guard_finds_a_join(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text('from pathlib import Path\nrun = Path("x")\na = run / "run.json"\nb = run / "trajectories"\n'
                      'c = "runs/s/r/episodes.jsonl"\nd = "no run.json here"\n')
    assert set(offenders(sample, "sample.py")) == {"sample.py:3: 'run.json'", "sample.py:5: 'runs/s/r/episodes.jsonl'",
                                                   "sample.py:4: 'trajectories' joined to a path"}
