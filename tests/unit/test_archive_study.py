"""scripts/archive_study.py: a study's runs and logs move into OLD/ with a record, and nothing moves when it refuses."""
from __future__ import annotations

import json
import os
import time

from scripts import archive_study


def study(root, name="s1", age_s=3600):
    run = root / "runs" / name / "run_a" / "processed"
    run.mkdir(parents=True)
    (run / "run.json").write_text('{"a": 1}')
    (run / "episodes.jsonl").write_text("x\n")
    log = root / "logs" / name / "run_a"
    log.mkdir(parents=True)
    (log / "a.eval").write_bytes(b"\x00" * 100)
    old = time.time() - age_s
    os.utime(run / "run.json", (old, old))
    return root


def test_a_dry_run_moves_nothing_and_apply_moves_records_and_verifies(tmp_path, capsys):
    root = study(tmp_path)
    assert archive_study.main(["s1"], root) == 0
    assert (root / "runs" / "s1").is_dir() and not (root / "OLD").exists()
    assert "dry run: nothing moved" in capsys.readouterr().out
    assert archive_study.main(["s1", "--apply"], root) == 0
    assert not (root / "runs" / "s1").exists() and not (root / "logs" / "s1").exists()
    assert (root / "OLD" / "runs" / "s1" / "run_a" / "processed" / "run.json").read_text() == '{"a": 1}'
    assert (root / "OLD" / "logs" / "s1" / "run_a" / "a.eval").stat().st_size == 100
    [record] = (root / "logs" / "moves").glob("*_archive_s1.jsonl")
    rows = [json.loads(line) for line in record.read_text().splitlines()]
    assert rows[-1] == {"verified": True, "files": 3, "bytes": sum(r["size"] for r in rows[:-1]), "mismatches": []}
    assert {r["target"] for r in rows[:-1]} == {"OLD/runs/s1/run_a/processed/run.json",
                                               "OLD/runs/s1/run_a/processed/episodes.jsonl",
                                               "OLD/logs/s1/run_a/a.eval"}
    assert "verified: every file arrived" in capsys.readouterr().out


def test_it_refuses_a_missing_study_a_taken_target_and_a_run_still_being_written(tmp_path, capsys):
    root = study(tmp_path)
    assert archive_study.main(["nope", "--apply"], root) == 2
    (root / "OLD" / "logs" / "s1").mkdir(parents=True)
    assert archive_study.main(["s1", "--apply"], root) == 2
    assert "OLD/logs/s1 exists" in capsys.readouterr().out
    (root / "OLD" / "logs" / "s1").rmdir()
    fresh = study(tmp_path, "s2", age_s=5)
    assert archive_study.main(["s2", "--apply"], fresh) == 2
    assert "changed in the last 10 minutes" in capsys.readouterr().out
    assert (root / "runs" / "s1").is_dir() and (root / "runs" / "s2").is_dir()
