"""A run's two ledgers (src/measures/stored_versions.py): a stamp records what a part stores, a computed part's write
rewrites only what changed, and behind names every part to redo and why. Every case builds a small registry and a run
folder under tmp_path, the run's files at today's places."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.measures import pipeline_parts as pp
from src.measures import stored_json
from src.measures import stored_versions as sv
from src.measures.run_directory import RunDir
from tests.support.run_files import write_text

MAKE = "src.measures.pipeline_parts:order"


@pytest.fixture
def setup(tmp_path: Path):
    (tmp_path / "code").mkdir()
    for name in ("export", "topology", "label"):
        (tmp_path / "code" / f"{name}.py").write_text(f"{name} = 1\n")
    parts = {
        "export": {"file": ["processed/run.json", "processed/trajectories/"], "make": None, "needs": [],
                   "files": ["code/export.py"], "pins": []},
        "judge_scores": {"file": "analysis/judges/labels.*.jsonl", "make": None, "needs": ["export"],
                         "files": ["code/label.py"], "pins": []},
        "topology": {"file": "processed/facts/topology.json", "make": MAKE, "needs": ["export"],
                     "files": ["code/topology.py"], "pins": []},
        "label": {"file": "analysis/labels/label.json", "make": MAKE, "needs": ["topology", "judge_scores"],
                  "files": ["code/label.py"], "pins": []},
    }
    path = tmp_path / "parts.yaml"
    path.write_text(yaml.safe_dump({"parts": parts}))
    registry = pp.load(path, root=tmp_path)
    for name in registry.parts:
        pp.add(registry, name, "first version")
    assert pp.problems(registry) == []
    run = tmp_path / "runs" / "study_x" / "a1"
    (run / "processed/trajectories" / "e1").mkdir(parents=True)
    write_text(run / "processed/run.json", json.dumps({"run_id": "a1"}))
    (run / "processed/trajectories" / "e1" / "transcript.json").write_text("{}")
    return registry, RunDir(run)


def test_a_stamp_hashes_a_recorded_part_into_its_layers_ledger(setup):
    registry, run = setup
    entry = sv.stamp(run, "export", registry=registry, version=0, made="migration")
    assert set(entry["files"]) == {"processed/run.json", "processed/trajectories/e1/transcript.json"}
    assert entry["version"] == 0 and entry["made"] == "migration" and entry["inputs"] == {}
    assert sv.ledger(run, "processed") == {"export": entry} and sv.ledger(run, "analysis") == {}
    assert run.versions()["processed"] == run.path / "processed" / "versions.json"


def test_behind_names_each_part_and_why(setup):
    registry, run = setup
    sv.stamp(run, "export", registry=registry)
    assert sv.behind(run, registry) == {"topology": "not processed", "label": "not processed"}
    assert sv.write_output(run, "topology", {"edges": [["a", "b"]]}, registry=registry) == "redone"
    assert sv.write_output(run, "label", {"cheated": True}, registry=registry) == "redone"
    assert sv.behind(run, registry) == {}
    assert run.output("topology", registry).read_bytes() == stored_json.dumps({"edges": [["a", "b"]]})


def test_a_label_change_leaves_the_facts_alone(setup):
    """Changing a label redoes that label only: topology, a fact, stays current and keeps its bytes."""
    registry, run = setup
    sv.stamp(run, "export", registry=registry)
    sv.write_output(run, "topology", {"edges": []}, registry=registry)
    sv.write_output(run, "label", {"cheated": False}, registry=registry)
    facts_ledger = run.versions()["processed"].read_bytes()
    (Path(registry.root) / "code" / "label.py").write_text("label = 2\n")
    pp.bump(registry, "label", "a new rule")
    assert sv.behind(run, registry) == {"label": "v1 → v2"}
    assert sv.write_output(run, "label", {"cheated": True}, registry=registry) == "redone"
    assert sv.behind(run, registry) == {}
    assert run.versions()["processed"].read_bytes() == facts_ledger
    assert sv.part_versions(run, registry)["label"] == {"stored": 2, "current": 2}


def test_equal_bytes_restamp_and_a_new_value_replaces_the_old(setup):
    registry, run = setup
    sv.stamp(run, "export", registry=registry)
    sv.write_output(run, "topology", {"edges": []}, registry=registry)
    assert sv.write_output(run, "topology", {"edges": []}, registry=registry) == "restamped"
    assert sv.write_output(run, "topology", {"edges": [["a", "b"]]}, registry=registry) == "redone"
    assert run.output("topology", registry).read_bytes() == stored_json.dumps({"edges": [["a", "b"]]})


def test_an_input_that_moved_puts_its_readers_behind(setup):
    registry, run = setup
    sv.stamp(run, "export", registry=registry)
    sv.write_output(run, "topology", {"edges": []}, registry=registry)
    sv.write_output(run, "label", {"cheated": False}, registry=registry)
    (run.path / "processed/trajectories" / "e1" / "transcript.json").write_text('{"moved": 1}')
    assert sv.behind(run, registry) == {}
    assert sv.behind(run, registry, deep=True) == {"topology": "export changed", "label": "topology behind"}
    sv.stamp(run, "export", registry=registry)
    assert sv.behind(run, registry) == {"topology": "export changed", "label": "topology behind"}


def test_a_judge_entry_keeps_its_verdict_files(setup):
    registry, run = setup
    sv.stamp(run, "export", registry=registry)
    (run.path / "analysis/judges").mkdir(parents=True)
    (run.path / "analysis/judges" / "labels.luna.jsonl").write_text(json.dumps({"judge": "luna",
                                                                                "prompt_sha256": "p1"}) + "\n")
    entry = sv.stamp(run, "judge_scores", registry=registry)
    assert entry["verdicts"]["analysis/judges/labels.luna.jsonl"]["judge"] == ["luna"]
    assert sv.ledger(run, "analysis")["judge_scores"] == entry
    sv.write_output(run, "topology", {"edges": []}, registry=registry)
    sv.write_output(run, "label", {"cheated": False}, registry=registry)
    (run.path / "analysis/judges" / "labels.luna.jsonl").write_text(json.dumps({"judge": "luna",
                                                                                "prompt_sha256": "p2"}) + "\n")
    sv.stamp(run, "judge_scores", registry=registry)
    assert sv.behind(run, registry) == {"label": "judge_scores changed"}


def test_stored_json_gives_equal_bytes_for_equal_values_and_keeps_the_order():
    """Equal values built in the same order give equal bytes; a dict keeps its order, which the viewer shows."""
    assert stored_json.dumps({"b": 1, "a": [1.5, None]}) == stored_json.dumps(dict([("b", 1), ("a", [1.5, None])]))
    assert stored_json.dumps({"b": 1, "a": 2}) == b'{\n "b": 1,\n "a": 2\n}\n'
    assert list(stored_json.decode("x.json", stored_json.dumps({"moved": 1, "opened": 2}))) == ["moved", "opened"]
    assert stored_json.decode("x.jsonl", stored_json.encode("x.jsonl", [{"k": 2}, {"k": 1}])) == [{"k": 2}, {"k": 1}]
    with pytest.raises(ValueError):
        stored_json.dumps({"x": float("nan")})
    with pytest.raises(TypeError):
        stored_json.dumps({"x": {1, 2}})
