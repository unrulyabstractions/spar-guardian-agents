"""scripts/migrate_verdict_files.py on a study built under tmp_path: judge.jsonl becomes judges/cheatbench.<judge>.jsonl
keyed by episode, labels units become episodes, the old bytes are kept aside, and a second pass finds nothing."""
from __future__ import annotations

import json

from scripts import migrate_verdict_files as migrate
from src.judging import verdict_files
from src.utils.json_files import read_jsonl, write_jsonl


def old_run(study, name="r1"):
    """A run of the older export: episodes name "orbit/<id>" and orbit_agent, verdicts sit in judge.jsonl with an
    absolute transcript path, and the labels file is keyed by agent."""
    run = study / name
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"task": "protein_design"}))
    write_jsonl(run / "episodes.jsonl", [
        {"id": "s", "episode": f"s#1@{a}", "replicate": 1, "task": "protein_design", "model": "m",
         "agent": f"orbit/{a}", "orbit_agent": a, "role": "worker"} for a in ("a01", "a02")])
    write_jsonl(run / "judge.jsonl", [
        {"id": "s", "episode": f"s#1@{a}", "replicate": 1, "task": "protein_design", "model": "m",
         "judge": "gpt-6-luna", "parse_ok": True, "cheated": a == "a01",
         "transcript_path": f"/Users/x/runs/{name}/trajectories"} for a in ("a01", "a02")])
    (run / "judges").mkdir()
    write_jsonl(run / "judges" / "labels.gpt-6-luna.jsonl", [
        {**dict.fromkeys(verdict_files.ROW_KEYS), "unit": a, "agent": a, "episode": f"s#1@{a}", "judge": "gpt-6-luna",
         "parse_ok": True, "disclosure": {"score": 0}, "manipulation": {"score": 0}} for a in ("a01", "a02")])
    return run


def test_the_old_files_move_into_judges_keyed_by_episode_and_their_bytes_are_kept(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(migrate, "BACKUP", tmp_path / "backup")
    study = tmp_path / "runs" / "study_x"
    run = old_run(study)
    before = {p: p.read_bytes() for p in (run / "judge.jsonl", run / "judges" / "labels.gpt-6-luna.jsonl")}
    assert migrate.main([str(study)]) == 0 and (run / "judge.jsonl").is_file()          # the plan writes nothing
    assert "1 runs to migrate" in capsys.readouterr().out
    assert migrate.main([str(study), "--write"]) == 0
    assert not (run / "judge.jsonl").exists()
    rows = verdict_files.rows(run, "cheatbench", "gpt-6-luna")
    assert [(r["unit"], r["agent"], r["role"], r["cheated"]) for r in rows] == [
        ("s#1@a01", "a01", "worker", True), ("s#1@a02", "a02", "worker", False)]
    assert rows[0]["transcript_path"] == "trajectories/s#1@a01/transcript.json"
    assert set(verdict_files.ROW_KEYS) <= set(rows[0]) and list(rows[0])[-1] == "transcript_path"
    assert [r["unit"] for r in verdict_files.rows(run, "labels", "gpt-6-luna")] == ["s#1@a01", "s#1@a02"]
    kept = tmp_path / "backup" / "study_x" / "r1"
    assert (kept / "judge.jsonl").read_bytes() == (kept / "judge.jsonl.moved").read_bytes() == \
        before[run / "judge.jsonl"]
    assert (kept / "judges" / "labels.gpt-6-luna.jsonl").read_bytes() == before[run / "judges" /
                                                                                "labels.gpt-6-luna.jsonl"]
    assert migrate.plan(study.resolve()) == []


def test_a_row_the_judges_file_already_holds_is_newer_and_stays(tmp_path, monkeypatch):
    monkeypatch.setattr(migrate, "BACKUP", tmp_path / "backup")
    study = tmp_path / "runs" / "study_x"
    run = old_run(study)
    newer = {**dict.fromkeys(verdict_files.ROW_KEYS), "unit": "s#1@a01", "episode": "s#1@a01", "judge": "gpt-6-luna",
             "cheated": False}
    verdict_files.write(run, "cheatbench", "gpt-6-luna", [newer])
    assert migrate.main([str(study), "--write"]) == 0
    stored = verdict_files.cheatbench(run)["gpt-6-luna"]
    assert (stored["s#1@a01"]["cheated"], stored["s#1@a02"]["cheated"]) == (False, False)
    assert len(read_jsonl(run / "judges" / "cheatbench.gpt-6-luna.jsonl")) == 2
