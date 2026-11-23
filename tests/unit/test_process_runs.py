"""The process command (src/measures/process_runs.py) on synthetic runs: every computed part stored in the run's own
folder and stamped, nothing redone when nothing is behind, a forced part redone with what follows it, a failing part
keeping its old file and blocking what needs it, a label's new version redoing that label and the row only with the
topology byte for byte the same, and the stored topology giving the Coordination ladder the board gives. No model
call, no Docker: the ladders that read transcripts are stood in for."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from src.cli import main
from src.measures import code_ladder, coordination, pipeline_parts, process_runs, stored_versions, topology_fact
from src.measures.run_directory import RunDir
from src.ui import viewer_parts as vp
from tests.support.run_files import write_text

ROLES = {"a01": None, "a02": None, "a03": "regulator"}
BOARD = {
    "messages": [{"id": "m0", "sender": "m6p6z", "recipients": ["a01", "a02", "a03"], "content": "seeded",
                  "sequence": 1, "invocation_id": "seed_pack"},
                 {"id": "m1", "sender": "a01", "recipients": ["a02", "a03"], "content": "a02, take B", "sequence": 3},
                 {"id": "m2", "sender": "a02", "recipients": ["a01", "a03"], "content": "done, a01", "sequence": 5},
                 {"id": "m3", "sender": "a01", "recipients": ["a02", "a03"], "content": "thanks", "sequence": 7}],
    "events": [{"sequence": 2, "kind": "read", "agent_id": "a02", "tool_call_id": "c1", "message_ids": ["m0"],
                "session_id": "x"},
               {"sequence": 3, "kind": "sent", "agent_id": "a01", "tool_call_id": "c2", "message_ids": ["m1"]},
               {"sequence": 4, "kind": "read", "agent_id": "a02", "tool_call_id": "c3", "message_ids": ["m1"]},
               {"sequence": 5, "kind": "sent", "agent_id": "a02", "tool_call_id": "c4", "message_ids": ["m2"]},
               {"sequence": 6, "kind": "read", "agent_id": "a01", "tool_call_id": "c5", "message_ids": ["m2"]},
               {"sequence": 7, "kind": "sent", "agent_id": "a01", "tool_call_id": "c6", "message_ids": ["m3"]},
               {"sequence": 8, "kind": "model_exposure", "agent_id": "a03", "tool_call_id": None,
                "message_ids": ["m3"]}]}


def write_run(path: Path, task: str = "creative_writings") -> Path:
    path.mkdir(parents=True)
    episodes = [{"episode": f"s#1@{a}", "agent": a, "role": r} for a, r in ROLES.items()]
    write_text(path / "processed/episodes.jsonl", "".join(json.dumps(e) + "\n" for e in episodes))
    write_text(path / "analysis/signals.jsonl", "")
    write_text(path / "processed/run.json", json.dumps({"model": "m", "role_models": {}, "config": {},
                                                        "swarm": {"roles": ROLES}, "task": task}, indent=1))
    write_text(path / "processed/communication" / "s#1.json", json.dumps(BOARD))
    return path


@pytest.fixture
def stood_in(monkeypatch):
    """The code record, Cheating, Collusion and the judge labels stood in for: the synthetic run has no transcript."""
    monkeypatch.setattr(code_ladder, "signals_part", lambda job: [{"row": "team", "agent": None}])
    monkeypatch.setattr(vp, "cheating_part", lambda job: {"applicable": False, "reason": "stood in"})
    monkeypatch.setattr(vp, "collusion_part", lambda job: {"applicable": False, "reason": "stood in"})
    monkeypatch.setattr(vp, "judged_part", lambda job: {"labels": {"judge": None, "agents": {a: None for a in ROLES},
                                                                   "team": {"disclosure": None, "manipulation": None},
                                                                   "influencers": {}},
                                                        "cheatbench": {}})


def study_with_run(tmp_path: Path) -> tuple[Path, Path]:
    study = tmp_path / "runs" / "study_x"
    return study, write_run(study / "poem_minimal_luna1")


COMPUTED = ["signals", "topology", "cheating", "coordination", "collusion", "judged", "row"]


def test_topology_is_the_board_without_labels_and_coordination_reads_it(tmp_path):
    run = RunDir(write_run(tmp_path / "r"))
    fact = topology_fact.topology(run)
    assert fact["agents"] == [{"agent": a, "role": r} for a, r in ROLES.items()]
    [board] = fact["boards"]
    assert board["sample"] == "s#1" and [m["P"] for m in board["messages"]] == [1, 2, 3, 4]
    assert [(m["sender"], m["seeded"], m["in_roster"], m["names"]) for m in board["messages"]] == [
        ("m6p6z", True, False, []), ("a01", False, True, ["a02"]), ("a02", False, True, ["a01"]),
        ("a01", False, True, [])]
    assert "content" not in board["messages"][0] and set(board["events"][0]) == set(topology_fact.EVENT_KEYS)
    assert [e["kind"] for e in board["events"]][-1] == "model_exposure"
    want = coordination.run_coordination(run.path)
    assert coordination.coordination_of(topology_fact.boards(fact), topology_fact.roles(fact)) == want
    stored = json.loads(json.dumps(fact))
    assert coordination.coordination_of(topology_fact.boards(stored), topology_fact.roles(stored)) == want


def test_a_run_is_processed_once_and_nothing_is_redone_after(tmp_path, stood_in):
    study, run = study_with_run(tmp_path)
    [job] = process_runs.jobs_of(study)
    first = process_runs.process_job(job)
    assert list(first.done) == COMPUTED and set(first.done.values()) == {"redone"} and not first.failed
    assert first.stamped == ["export"]
    stored = RunDir(run)
    for part in COMPUTED:
        assert stored.output(part).is_file(), part
    assert stored.output("topology") == run / "processed/facts/topology.json"
    assert stored.output("row") == run / "analysis/row.json"
    assert stored.output("signals") == run / "analysis/signals.jsonl"
    ledgers = {layer: json.loads(path.read_text())["parts"] for layer, path in stored.versions().items()}
    assert set(ledgers["processed"]) == {"export", "topology"}
    assert set(ledgers["analysis"]) == set(COMPUTED) - {"topology"}
    assert ledgers["analysis"]["coordination"]["inputs"] == {
        "topology": [1, stored_versions.sha256_file(stored.output("topology"))]}
    row = json.loads(stored.output("row").read_text())
    assert row["id"] == "study_x/poem_minimal_luna1" and row["levels"]["team"]["coordination"]
    again = process_runs.process_job(job)
    assert again.behind == {} and again.done == {} and again.stamped == []


def test_a_forced_part_is_redone_with_what_follows_it(tmp_path, stood_in):
    study, run = study_with_run(tmp_path)
    [job] = process_runs.jobs_of(study)
    process_runs.process_job(job)
    forced = process_runs.process_job(job, force=("coordination",))
    assert forced.done == {"coordination": "restamped", "row": "restamped"}


def test_a_failing_part_keeps_its_file_and_blocks_what_needs_it(tmp_path, stood_in, monkeypatch):
    study, run = study_with_run(tmp_path)
    [job] = process_runs.jobs_of(study)
    process_runs.process_job(job)
    before = RunDir(run).output("cheating").read_bytes()

    def broken(job):
        raise ValueError("no ladder today")

    monkeypatch.setattr(vp, "cheating_part", broken)
    report = process_runs.process_job(job, force=("cheating",))
    assert report.failed == {"cheating": "ValueError: no ladder today"} and report.blocked == {"row": "cheating"}
    assert RunDir(run).output("cheating").read_bytes() == before


def test_a_new_label_version_redoes_that_label_and_the_row_only(tmp_path, stood_in):
    study, run = study_with_run(tmp_path)
    [job] = process_runs.jobs_of(study)
    process_runs.process_job(job)
    topology = RunDir(run).output("topology").read_bytes()
    registry = copy.deepcopy(pipeline_parts.registry())
    was = registry.parts["cheating"].version
    registry.parts["cheating"].version += 1
    assert stored_versions.behind(job.run, registry) == {"cheating": f"v{was} → v{was + 1}", "row": "cheating behind"}
    report = process_runs.process_job(job, registry)
    assert set(report.done) == {"cheating", "row"}
    assert RunDir(run).output("topology").read_bytes() == topology
    assert stored_versions.entry(job.run, "cheating")["version"] == was + 1


def test_the_signals_block_restamps_the_export_it_changes(tmp_path, stood_in):
    """run.json is the export's: when the code record's block changes it, the export's stamp follows, so no part that
    needs the export falls behind on the next pass."""
    study, run = study_with_run(tmp_path)
    [job] = process_runs.jobs_of(study)
    process_runs.process_job(job)
    old = stored_versions.entry(job.run, "export")
    code_ladder.write_block(job.run, {"row": "team", "ended_by": "submit"})
    new = stored_versions.entry(job.run, "export")
    assert json.loads((run / "processed/run.json").read_text())["signals"] == \
        {"team": {"row": "team", "ended_by": "submit"}}
    assert new["sha256"] != old["sha256"] and new["version"] == old["version"] and new["made"] == old["made"]
    code_ladder.write_block(job.run, {"row": "team", "ended_by": "submit"})
    assert stored_versions.entry(job.run, "export") == new


def test_the_jobs_are_a_studys_valid_runs_or_one_run_folder(tmp_path):
    study, run = study_with_run(tmp_path)
    write_run(study / "attempts" / "poem_minimal_luna1.1")
    assert [j.listed.key for j in process_runs.jobs_of(study)] == ["poem_minimal_luna1"]
    [one] = process_runs.jobs_of(study / "attempts" / "poem_minimal_luna1.1")
    assert (one.col, one.listed.key, one.listed.status) == ("study_x", "attempts/poem_minimal_luna1.1", "attempt")
    loose = write_run(tmp_path / "elsewhere" / "r1")
    [alone] = process_runs.jobs_of(loose)
    assert (alone.col, alone.listed.key, alone.listed.status) == ("elsewhere", "r1", "valid")


def test_the_command_dry_run_writes_nothing(tmp_path, capsys):
    study, run = study_with_run(tmp_path)
    assert main(["process", str(study), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "behind       signals 1, topology 1" in out and "nothing written" in out
    assert not any(path.exists() for path in RunDir(run).versions().values())

