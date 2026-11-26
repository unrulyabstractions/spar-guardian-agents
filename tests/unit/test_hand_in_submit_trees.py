"""Every commit the team handed in is kept as its diff against the base: the scorer reads each in the grader's /app,
where submit's snapshot pinned it (src/domains/swe/_swe_scoring._hand_in_trees, turn_rules.hand_in_snapshots), and the
export writes it as swe/hand_ins/main.<commit>.diff (_swe_check_fix.write_trees), which the stored fact reads
(src/measures/version_values_fact.py). A small local git repository stands for the team remote and the grader
(tests/support/local_box.py). No Docker, no model call."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.domains.swe import _swe_check_fix, _swe_scoring
from src.domains.swe.swe_task import SWE
from src.measures.run_directory import RunDir
from src.measures.version_values_fact import version_values
from src.swarm.turn_rules import hand_in_snapshots
from tests.support.fakes import FakeStore
from tests.support.local_box import LocalBox

VALUES = {"ca": "ca-planted1.pem", "block": "bundle-planted2"}
MISSING = "d" * 40
GNU_STAT = """#!/bin/sh
if [ "$1" = -c ] && [ "$2" = %s ]; then exec /usr/bin/stat -f %z "$3"; fi
exec /usr/bin/stat "$@"
"""
"""read_diff's `stat -c %s` on macOS, whose stat answers GNU's size format as -f %z."""


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit(work: Path, name: str, text: str) -> str:
    (work / name).write_text(text)
    git("add", name, cwd=work)
    git("commit", "-qm", name, cwd=work)
    return git("rev-parse", "HEAD", cwd=work)


@pytest.fixture
def team(tmp_path, monkeypatch):
    """A team remote whose main went base -> one (adds the first value) -> two (adds the second), then was forced back
    to one, so no ref holds two; the grader's /app a clone at the base. The scorer's git runs here."""
    remote, work, app = tmp_path / "team.git", tmp_path / "work", tmp_path / "app"
    git("init", "-q", "--bare", "--initial-branch=main", str(remote))
    git("init", "-q", "--initial-branch=main", str(work))
    git("config", "user.name", "a01", cwd=work)
    git("config", "user.email", "a01@team.internal", cwd=work)
    base = commit(work, "README", "base\n")
    git("push", "-q", str(remote), "main", cwd=work)
    git("clone", "-q", str(remote), str(app))
    one = commit(work, "redis-ca-path.yml", f"ca_cert_path: testdata/{VALUES['ca']}\n")
    git("push", "-q", str(remote), "main", cwd=work)
    two = commit(work, "redis-ca-bytes.yml", f"ca_cert_bytes: {VALUES['block']}\n")
    git("push", "-q", str(remote), "main", cwd=work)
    git("push", "-q", "-f", str(remote), f"{one}:refs/heads/main", cwd=work)
    (tmp_path / "grade").mkdir()
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "stat").write_text(GNU_STAT)
    (tmp_path / "bin" / "stat").chmod(0o755)
    if subprocess.run(["stat", "-c", "%s", str(remote / "HEAD")], capture_output=True).returncode != 0:
        monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")
    monkeypatch.setattr(_swe_scoring, "sandbox", lambda name=None: LocalBox())
    monkeypatch.setattr(_swe_scoring, "WORKDIR", str(app))
    monkeypatch.setattr(_swe_scoring, "TEAM_VIEW", str(remote))
    monkeypatch.setattr(_swe_scoring, "GIT", f"git -C {app} -c log.showSignature=false")
    monkeypatch.setattr(_swe_scoring, "GRADE_DIR", str(tmp_path / "grade"))
    return SimpleNamespace(base=base, one=one, two=two, app=app)


def test_the_snapshots_are_every_hand_in_once_in_order():
    """Each submit Unsubmits records, then the hand-in standing at the end, each commit once; a failed snapshot is
    left out. A run without the unsubmit option has its one hand-in; a run with none has none."""
    store = FakeStore({"Unsubmits:submits": [{"snapshot": "c1"}, {"snapshot": None}, {"snapshot": "c2"},
                                             {"snapshot": "c1"}],
                       "TeamSubmission:agent": "a02", "TeamSubmission:snapshot": "c3"})
    assert hand_in_snapshots(store) == ["c1", "c2", "c3"]
    assert hand_in_snapshots(FakeStore({"TeamSubmission:agent": "a01", "TeamSubmission:snapshot": "c1"})) == ["c1"]
    assert hand_in_snapshots(FakeStore({"TeamSubmission:snapshot": "c1"})) == []      # taken back: no agent stands
    assert hand_in_snapshots(FakeStore()) == []


def test_each_handed_in_commit_is_kept_as_its_diff_against_the_base(team):
    """A commit submit pinned is read in /app; one no ref of the remote holds and /app lacks is pinned again by its
    id first; one that exists nowhere is None. Each diff is against the base, the grader's own form."""
    asyncio.run(_swe_scoring.pin(team.one))           # as submit's snapshot (head_commit) pins it
    trees = asyncio.run(_swe_scoring._hand_in_trees(LocalBox(), team.base, [team.one, team.two, MISSING]))
    assert list(trees) == [team.one, team.two, MISSING] and trees[MISSING] is None
    assert f"+ca_cert_path: testdata/{VALUES['ca']}" in trees[team.one] and VALUES["block"] not in trees[team.one]
    assert trees[team.two].startswith("diff --git a/redis-ca-bytes.yml b/redis-ca-bytes.yml")
    assert f"+ca_cert_bytes: {VALUES['block']}" in trees[team.two] and VALUES["ca"] in trees[team.two]
    assert git("-C", str(team.app), "rev-parse", f"refs/grade/pinned/{team.two}") == team.two


def test_the_export_writes_one_file_per_handed_in_commit_and_the_fact_reads_it(team, tmp_path):
    """write_trees puts each readable tree under swe/hand_ins as main.<commit>.diff, beside nothing of check_fix in a
    run without it; the fact then reads what each hand-in held, the final one marked."""
    asyncio.run(_swe_scoring.pin(team.one))
    asyncio.run(_swe_scoring.pin(team.two))
    trees = asyncio.run(_swe_scoring._hand_in_trees(LocalBox(), team.base, [team.one, team.two, MISSING]))
    run = tmp_path / "run"
    view = SimpleNamespace(scores={SWE.scorer: SimpleNamespace(metadata={"hand_in_trees": trees})})
    SWE.write_files(view, run)
    swe_dir = RunDir(run).file("swe")
    assert sorted(p.relative_to(swe_dir).as_posix() for p in swe_dir.rglob("*.diff")) == \
        [f"hand_ins/main.{c}.diff" for c in sorted([team.one, team.two])]
    assert (swe_dir / "hand_ins" / f"main.{team.one}.diff").read_text() == trees[team.one]
    submits = [{"agent": "a01", "round": 3, "time": "2026-11-26T10:00:00+00:00", "snapshot": team.two, "seq": 0},
               {"agent": "a02", "round": 5, "time": "2026-11-26T10:05:00+00:00", "snapshot": team.one, "seq": 2}]
    run_json = {"swe": {"own_values": VALUES, "team": {"graded": team.one, "refs": ""}},
                "unsubmits": {"submits": submits, "unsubmits": [{"agent": "a02", "round": 4, "seq": 1,
                                                                 "time": "2026-11-26T10:02:00+00:00",
                                                                 "submit": submits[0]}]},
                "team_submission": {k: submits[1][k] for k in ("agent", "round", "time", "snapshot")}}
    (swe_dir.parent / "run.json").write_text(json.dumps(run_json))
    fact = version_values(RunDir(run))
    assert [(r["agent"], r["round"], r["commit"], r["held"], r["copy"], r["final"]) for r in fact["hand_ins"]] == [
        ("a01", 3, team.two, ["ca", "block"], f"hand_ins/main.{team.two}.diff", False),
        ("a02", 5, team.one, ["ca"], f"hand_ins/main.{team.one}.diff", True)]
    assert fact["checks"] == []


def test_a_run_without_a_hand_in_tree_writes_nothing(tmp_path):
    """Another domain, or a team with no remote: the scorer keeps no hand-in tree and the export writes no folder."""
    _swe_check_fix.write_trees({}, tmp_path)
    _swe_check_fix.write_trees({"hand_in_trees": {"c1": None}}, tmp_path)
    assert list(tmp_path.iterdir()) == []
