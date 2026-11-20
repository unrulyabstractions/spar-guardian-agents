"""The join of the three replayers into replay_run: the task names, each task's line state, the sandbox's user and
timeout, and the platform cause. SWE's lines must come out byte for byte as the code before the join wrote them: four
lines recorded by a replay of flipt_minimal_luna_fixed1 at a36746d (tests/support/replay_swe_lines.jsonl) are rebuilt
from the states they show."""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.replay import replay_sandbox
from src.replay.creative_replay import ContainerBox, CreativeReplayer
from src.replay.output_match import cause_of
from src.replay.protein_replay import ProteinReplayer
from src.replay.recorded_calls import RecordedCall
from src.replay.replay_run import RECORDED, REPLAYERS, step_rows
from src.replay.replay_sandbox import ReplaySandbox
from src.replay.step_snapshots import encode
from src.replay.swe_replayer import SweReplayer

RECORDED_LINES = Path(__file__).parents[1] / "support" / "replay_swe_lines.jsonl"
T0 = 1000.0


def call(line: dict, t: float | None = None) -> RecordedCall:
    return RecordedCall(n=line["n"], agent=line["agent"], k=line["k"], id=line["call_id"], tool=line["tool"],
                        arguments={}, result="", error=None, t0=T0 + (line["t"] if t is None else t), t1=T0 + 1)


def swe_state(line: dict) -> tuple[dict, dict]:
    """The SWE replayer's state and the team's, as far as a line in full shows them."""
    state, pieces = line["state"], line["state"]["pieces"]

    def holders(key: str) -> dict[str, list[str]]:
        refs = {ref for held in pieces.values() for ref in held[key]}
        return {ref: sorted(p for p, held in pieces.items() if ref in held[key]) for ref in sorted(refs)}
    agent = {"head": state["head"], "branch": state["branch"], "refs": state["refs"], "tree": "t",
             "tree_pieces": sorted(p for p, held in pieces.items() if held["tree"]),
             "tree_lines": {p: {"lines": held["lines"], "whole": held["whole"]} for p, held in pieces.items()},
             "dirty": state["dirty"], "ref_pieces": holders("refs")}
    return agent, {"refs": state["team_refs"], "ref_pieces": holders("team")}


def test_the_replayers_are_registered_by_the_task_names_run_json_records():
    assert REPLAYERS == {"software_engineering": SweReplayer, "protein_design": ProteinReplayer,
                         "creative_writings": CreativeReplayer}


def test_swe_lines_come_out_byte_for_byte_as_recorded():
    text = RECORDED_LINES.read_text()
    recorded = [json.loads(line) for line in text.splitlines()]
    full = {line["n"]: line for line in recorded}
    full[32]["state"]["refs"] = full[1]["state"]["refs"]              # n=32 changed no ref since q2x8k's n=1
    full[32]["state"]["team_refs"] = full[1]["state"]["team_refs"]
    calls = [call(line) for line in recorded]
    played = {}
    for n in (1, 2, 32):
        state, team = swe_state(full[n])
        played[n] = {"state": state, "team": team, "match": full[n]["match"]}
    replayer = SweReplayer.__new__(SweReplayer)
    replayer.pieces = ["A", "B", "C"]
    rows = step_rows(replayer, calls, played, {}, {"refs": {}, "ref_pieces": {}})
    assert [r["match"] for r in rows] == ["exact", "exact", RECORDED, "exact"]
    assert "".join(json.dumps(line) + "\n" for line in encode(rows)) == text


def protein_replayer() -> ProteinReplayer:
    replayer = ProteinReplayer.__new__(ProteinReplayer)
    replayer.pieces = ["segment1", "segment2"]
    return replayer


def protein_state(**extra) -> dict:
    return {"head": "c1", "branch": "main", "refs": {"refs/heads/main": "c1"}, "tree_pieces": ["segment1"],
            "ref_pieces": {}, "dirty": True, "files": {"/workspace/a.fasta": {"pieces": ["segment1"], "design": None}},
            "ref_designs": {}, **extra}


def test_a_protein_check_is_on_its_own_line_alone():
    a1 = [RecordedCall(n, "a1", n - 1, f"c{n}", tool, {}, "", None, T0 + n, T0 + n + 1)
          for n, tool in ((1, "check_design"), (2, "board_post"), (3, "shell"))]
    check = {"pieces": ["segment1"], "design": None, "verdict": "rejected"}
    team = {"refs": {"refs/heads/main": "c0"}, "ref_pieces": {"refs/heads/main": ["segment2"]},
            "ref_designs": {"refs/heads/main": ["D1"]}}
    played = {1: {"state": protein_state(check=check), "team": team, "match": "exact"},
              3: {"state": protein_state(dirty=False), "team": None, "match": "exact"}}
    rows = step_rows(protein_replayer(), a1, played, {"a1": protein_state()}, team)
    assert [r["state"]["check"] for r in rows] == [check, None, None]
    first = rows[0]["state"]
    assert list(first) == ["head", "branch", "refs", "team_refs", "pieces", "dirty", "files", "ref_designs",
                           "team_designs", "check"]
    assert first["pieces"]["segment1"] == {"tree": True, "team": [], "refs": []}
    assert first["pieces"]["segment2"]["team"] == ["refs/heads/main"]
    assert first["team_designs"] == {"refs/heads/main": ["D1"]}
    assert rows[1]["state"]["files"] == first["files"]                # the host call keeps a1's files
    assert rows[2]["state"]["dirty"] is False


def test_a_creative_line_is_the_world_and_the_calls_own_checks():
    replayer = CreativeReplayer.__new__(CreativeReplayer)
    seen = []
    replayer.reading = SimpleNamespace(line=lambda c, own, world: seen.append((c.n, own, world)) or {"n": c.n})
    calls = [RecordedCall(n, "a", n, f"c{n}", "shell", {}, "", None, T0 + n, T0 + n + 1) for n in (1, 2)]
    world0, world1 = {"files": {}}, {"files": {"/workspace/writing.md": "abc"}}
    played = {1: {"state": {"checks": [{"via": "log"}]}, "team": world1, "match": "exact"}}
    step_rows(replayer, calls, played, {"a": {"checks": []}}, world0)
    assert seen == [(1, {"checks": [{"via": "log"}]}, world1), (2, None, world1)]


def test_each_replayer_names_its_volumes():
    protein = ProteinReplayer.__new__(ProteinReplayer)
    protein.team = True
    assert protein.volumes() == {"team": {}}
    protein.team = False
    assert protein.volumes() == {}
    assert SweReplayer.__new__(SweReplayer).volumes() == {"team": {}}
    assert CreativeReplayer.__new__(CreativeReplayer).volumes() == {}


@pytest.fixture
def ran(monkeypatch):
    """subprocess.run in replay_sandbox, recorded: each argv and timeout, answered by `answer`."""
    seen, answer = [], {"rc": 0, "out": b"ok\n", "err": b""}

    def fake(argv, input=None, capture_output=True, timeout=None, stdin=None):
        seen.append({"argv": argv, "timeout": timeout, "input": input})
        return subprocess.CompletedProcess(argv, answer["rc"], answer["out"], answer["err"])
    monkeypatch.setattr(replay_sandbox.subprocess, "run", fake)
    return SimpleNamespace(seen=seen, answer=answer)


def test_the_sandbox_runs_as_a_user_with_a_timeout(ran, tmp_path):
    box = ReplaySandbox("replay-x", tmp_path / "compose.yaml")
    assert box.must("k7q2m", "git status", "status", user="user", timeout=30) == "ok\n"
    assert ran.seen[-1] == {"argv": ["docker", "exec", "--user", "user", "replay-x-k7q2m-1", "bash", "-c",
                                     "git status"], "timeout": 30, "input": None}
    box.must("k7q2m", "true", "true")
    assert ran.seen[-1]["argv"] == ["docker", "exec", "replay-x-k7q2m-1", "bash", "-c", "true"]
    assert ran.seen[-1]["timeout"] == 1800
    output, timed_out = box.tool("default", ["bash", "--login", "-c", "ls"], 180, "/workspace", stdin=b"x",
                                 user="k7q2m")
    assert ran.seen[-1]["argv"] == ["docker", "exec", "-i", "--user", "k7q2m", "--workdir", "/workspace",
                                    "replay-x-default-1", "/usr/bin/timeout", "-k", "5s", "180s", "bash", "--login",
                                    "-c", "ls"]
    assert (output, timed_out, ran.seen[-1]["timeout"]) == ("ok\n", False, 240)
    ran.answer.update(rc=1, out=b"", err=b"fatal: nope")
    with pytest.raises(RuntimeError, match="status failed in k7q2m \\(rc=1\\): fatal: nope"):
        box.must_run("k7q2m", ["git", "status"], "status")


def test_the_container_box_runs_as_inspects_docker_sandbox(ran, tmp_path, monkeypatch):
    box = ContainerBox(ReplaySandbox("replay-x", tmp_path / "compose.yaml"))
    done = asyncio.run(box.exec(["bash", "--login", "-c", "ls"], input="code", cwd="/workspace", user="k7q2m",
                                timeout=180))
    assert (done.success, done.returncode, done.stdout) == (True, 0, "ok\n")
    assert ran.seen[-1] == {"argv": ["docker", "exec", "-i", "--user", "k7q2m", "--workdir", "/workspace",
                                     "replay-x-default-1", "/usr/bin/timeout", "-k", "5s", "180s", "bash", "--login",
                                     "-c", "ls"], "timeout": 190, "input": b"code"}
    asyncio.run(box.exec(["python3", "-I", "-c", "print(1)"], user="root"))
    assert ran.seen[-1]["argv"] == ["docker", "exec", "--user", "root", "replay-x-default-1", "python3", "-I", "-c",
                                    "print(1)"]
    assert ran.seen[-1]["timeout"] is None
    ran.answer.update(rc=124)
    clock = iter([0.0, 200.0])
    monkeypatch.setattr(replay_sandbox.time, "time", lambda: next(clock))
    with pytest.raises(TimeoutError, match="after 180 seconds"):
        asyncio.run(box.exec(["sleep", "999"], timeout=180))


def test_a_cpu_name_and_its_sizes_are_the_platform():
    recorded = ("total 32\n-rw-r----- 1 root team 16328 Nov  1 03:31 _score.cpython-311-x86_64-linux-gnu.so\n"
                "-rw-r----- 1 root team 10242 Nov  1 03:31 grade.pyc\n")
    replayed = ("total 88\n-rw-r----- 1 root team 70528 Nov 19 22:48 _score.cpython-311-aarch64-linux-gnu.so\n"
                "-rw-r----- 1 root team 10242 Nov 19 22:48 grade.pyc\n")
    assert cause_of(recorded, replayed, "ls -la grading") == "platform"
    assert cause_of("grading/_score.cpython-311-x86_64-linux-gnu.so\ngrading/a\n",
                    "grading/a\ngrading/_score.cpython-311-aarch64-linux-gnu.so\n", "find grading") == "platform"
    assert cause_of("x86_64 one\n", "aarch64 two\n", "uname -m; cat x") != "platform"
