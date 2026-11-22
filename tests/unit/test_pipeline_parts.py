"""The pipeline's registry (src/measures/pipeline_parts.py) and its guard: the repository's registry has no problem,
and each rule refuses what it should on a small registry built under tmp_path."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from src.configuration import settings
from src.measures import pipeline_parts as pp


def test_the_repository_registry_has_no_problem():
    """The guard: every part locked at its code, its log complete, its needs a graph with no cycle."""
    assert pp.problems(pp.load()) == []


def make_registry(tmp_path: Path, parts: dict) -> pp.Registry:
    (tmp_path / "code").mkdir(exist_ok=True)
    (tmp_path / "code" / "a.py").write_text("A = 1\n")
    (tmp_path / "code" / "b.py").write_text("B = 1\n")
    path = tmp_path / "parts.yaml"
    path.write_text(yaml.safe_dump({"parts": parts}))
    return pp.load(path, root=tmp_path)


def recorded(file, files=("code/a.py",), needs=()) -> dict:
    return {"file": file, "make": None, "needs": list(needs), "files": list(files), "pins": []}


def locked(registry: pp.Registry) -> pp.Registry:
    for name in registry.parts:
        pp.add(registry, name, "first version")
    return registry


def test_two_parts_may_share_a_code_file(tmp_path):
    registry = locked(make_registry(tmp_path, {"x": recorded("processed/x.json"),
                                               "y": recorded("processed/y.json", needs=["x"])}))
    assert pp.problems(registry) == []
    assert pp.code_files(registry, "x") == pp.code_files(registry, "y") == ["code/a.py"]


def test_a_fingerprint_covers_its_own_files_only(tmp_path):
    registry = locked(make_registry(tmp_path, {"x": recorded("processed/x.json"),
                                               "y": recorded("processed/y.json", files=["code/b.py"])}))
    (tmp_path / "code" / "b.py").write_text("B = 2\n")
    assert pp.state(registry, "x") == "locked"
    assert pp.state(registry, "y") == "changed"
    assert [p.split(" ")[0] for p in pp.fingerprint_problems(registry)] == ["y"]


def test_bump_and_re_lock(tmp_path):
    registry = locked(make_registry(tmp_path, {"x": recorded("processed/x.json")}))
    with pytest.raises(ValueError, match="locked at its current code"):
        pp.re_lock(registry, "x", "nothing moved")
    (tmp_path / "code" / "a.py").write_text("A = 1  # a comment\n")
    part = pp.re_lock(registry, "x", "a comment")
    assert part.version == 1 and "re-locked" in part.log[1] and pp.problems(registry) == []
    (tmp_path / "code" / "a.py").write_text("A = 2\n")
    part = pp.bump(registry, "x", "A is 2")
    assert part.version == 2 and part.log[2].endswith("A is 2") and pp.problems(registry) == []
    with pytest.raises(ValueError, match="at v2 already"):
        pp.add(registry, "x", "again")


def test_save_writes_what_load_reads(tmp_path):
    registry = locked(make_registry(tmp_path, {"x": recorded("processed/x.json")}))
    pp.save(registry)
    again = pp.load(registry.path, root=tmp_path)
    assert again.parts["x"].entry() == registry.parts["x"].entry()
    assert registry.path.read_text().startswith(pp.HEADER)


def test_a_cycle_is_refused(tmp_path):
    registry = locked(make_registry(tmp_path, {"x": recorded("processed/x.json", needs=["y"]),
                                               "y": recorded("processed/y.json", needs=["x"])}))
    with pytest.raises(ValueError, match="cycle"):
        pp.order(registry)
    assert any("cycle" in problem for problem in pp.problems(registry))


def test_order_puts_needs_first(tmp_path):
    registry = make_registry(tmp_path, {"z": recorded("processed/z.json", needs=["x"]),
                                        "x": recorded("processed/x.json")})
    assert pp.order(registry) == ["x", "z"]
    assert pp.downstream(registry, "x") == {"z"} and pp.upstream(registry, "z") == {"x"}


@pytest.mark.parametrize(("parts", "found"), [
    ({"x": recorded("processed/x.json")}, "is not added"),
    ({"x": recorded("x.json")}, "lies outside"),
    ({"x": recorded(["processed/x.json", "analysis/x.json"])}, "one layer"),
    ({"x": recorded("processed/x.json", files=["code/none.py"])}, "matches no file"),
    ({"x": recorded("processed/x.json", files=[])}, "lists no files"),
    ({"x": recorded("analysis/j/"), "y": recorded("analysis/j/y.jsonl")}, "both store"),
    ({"x": recorded("analysis/x.json"), "y": recorded("processed/y.json", needs=["x"])}, "a label would change"),
    ({"x": {**recorded("processed/facts/x.json"), "make": "src.measures.pipeline_parts:nothing"}}, "names no callable"),
    ({"x": {**recorded("processed/x.json"), "make": "src.measures.pipeline_parts:order"}}, "outside processed/facts/"),
])
def test_each_rule_refuses(tmp_path, parts, found):
    registry = make_registry(tmp_path, parts)
    for name in registry.parts:
        if found != "is not added":
            pp.add(registry, name, "first version")
    assert any(found in problem for problem in pp.problems(registry)), pp.problems(registry)


def test_the_status_command_passes_on_the_repository():
    done = subprocess.run([sys.executable, "-m", "src", "pipeline", "status"], cwd=settings.ROOT, capture_output=True,
                          text=True, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "0 problems" in done.stdout + done.stderr
