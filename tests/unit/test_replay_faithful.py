"""The faithful gate: steps.jsonl is read only from a replay whose replay.json says its end state equals the stored
one; a refused, failed, unfaithful or missing replay is refused. And how a replayed output is matched. No Docker."""
from __future__ import annotations

import json

import pytest

from src.replay.output_match import cause_of, match_of
from src.replay.replay_report import NotFaithful, ReplayReport, faithful_steps, is_faithful, read_report, write_report
from src.replay.step_snapshots import encode
from tests.unit.test_step_snapshots import row


def write(tmp_path, **fields):
    report = ReplayReport(run="r", run_dir="runs/r", **{"faithful": True, **fields})
    write_report(tmp_path, report)
    rows = [row(1, "a", {"refs/heads/a": "1"}, {}), row(2, "a", {"refs/heads/a": "2"}, {})]
    (tmp_path / "steps.jsonl").write_text("".join(json.dumps(line) + "\n" for line in encode(rows)))
    return rows


def test_a_faithful_replay_gives_its_steps_in_full(tmp_path):
    rows = write(tmp_path, match={"exact": 3, "masked": 1})
    assert faithful_steps(tmp_path) == rows
    report = read_report(tmp_path)
    assert is_faithful(report) and report["shares"] == {"exact": 0.75, "masked": 0.25}


@pytest.mark.parametrize("fields, why", [({"faithful": False}, "end state differs"),
                                         ({"refused": "the setup does not reproduce"}, "does not reproduce"),
                                         ({"error": "RuntimeError: boom"}, "boom"),
                                         ({"schema": 0}, "end state")])
def test_any_other_replay_is_refused(tmp_path, fields, why):
    write(tmp_path, **fields)
    assert not is_faithful(read_report(tmp_path))
    with pytest.raises(NotFaithful, match=why):
        faithful_steps(tmp_path)


def test_no_report_is_refused(tmp_path):
    (tmp_path / "steps.jsonl").write_text("")
    with pytest.raises(NotFaithful, match="no replay.json"):
        faithful_steps(tmp_path)


def test_match_classes():
    assert match_of("ok\n", "ok\n") == "exact"
    assert match_of("ok\t0.412s\n2026-11-01 19:20:24\n", "ok\t0.077s\n2026-11-19 14:00:01\n") == "masked"
    assert match_of("b\na\n", "a\nb\n") == "order"
    assert match_of("a\n", "b\n") == "differs"


def test_causes():
    assert cause_of("[k 77eabf6] fix", "[k d18ae13] fix", "git commit -m fix") == "commit_hash"
    assert cause_of("Could not resolve host", "nope", "curl https://x") == "network"
    assert cause_of("size-pack: 63405", "size-pack: 63403", "git count-objects -v") == "object_store"
    assert cause_of("x", "y", "git log --since=2024-05-20") == "clock"
    assert cause_of("nameserver 1", "nameserver 2 x", "cat /etc/resolv.conf") == "environment"
    assert cause_of("pid 41", "pid 97", "echo $$") == "numbers"
    assert cause_of("a", "b", "cat x") == "other"
