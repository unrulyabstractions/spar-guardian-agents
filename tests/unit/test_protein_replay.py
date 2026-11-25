"""ProteinReplayer without Docker: the check of each agent's copy against the record, check_design re-run in its
owner's state, the pieces and designs a file and a commit hold, which calls run and which run alone, the snapshot's
parse, and the commands that move refs onto a dated commit, on a git repository of the host."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from src.domains.protein._protein_honeypot import agent_files, records, segment_text
from src.domains.protein._protein_rows import sha256s
from src.domains.protein._protein_settings import BUILD, TEAM_REMOTE
from src.replay.commit_dates import object_id, retime
from src.replay.protein_replay import (
    ProteinReplayer,
    parse_snapshot,
    recorded_check,
    rename_command,
    triples,
    write_object_command,
)
from src.replay.recorded_calls import RecordedCall, ReplayRefused
from src.sandbox import cheatbench_task_files as task_files

ROW = task_files.load_row(BUILD, "alfalfa_chi")
PIECES = {"a1": [1], "a2": [2], "a3": [3]}
PAYLOAD = agent_files(ROW, None, None)[ROW["payload_file"]].decode()


def run_of() -> SimpleNamespace:
    """run.json of a shared_task team of three, each holding one segment, as the export records its seats."""
    seats = {a: {"user": "user", "uid": 1000, "home": "/home/user", "workdir": "/workspace", "service": a,
                 "reap": True, "holds": [f"segment{p[0]}"], "pieces": p, "payloads": [ROW["payload_file"]],
                 "staged": sha256s(agent_files(ROW, p, 3))} for a, p in PIECES.items()}
    block = {"row_id": ROW["id"], "mode": "shared_task", "split": True, "n_pieces": 3,
             "work_dir": ROW["vars"]["work_dir"], "payload_file": ROW["payload_file"], "agents": seats,
             "owner_of": dict.fromkeys(PIECES, "team"), "team_remote": TEAM_REMOTE,
             "team": {"branches": {}, "hand_in": {"by": "end", "commit": None, "files": []}, "log": []}}
    return SimpleNamespace(run_json={"protein": block,
                                     "access": {"agents": {a: {"holds": s["holds"]} for a, s in seats.items()}}})


def call(tool: str, agent: str = "a1", **arguments) -> RecordedCall:
    return RecordedCall(n=1, agent=agent, k=0, id="call_1", tool=tool, arguments=arguments, result="", error=None,
                        t0=0.0, t1=1.0)


def test_each_copy_matches_the_recorded_staging():
    replayer = ProteinReplayer(run_of(), [])
    assert replayer.copy_problems(replayer.copies()) == []
    assert replayer.pieces == ["segment1", "segment2", "segment3"]


def test_a_copy_other_than_the_recorded_one_is_refused_before_any_container_is_touched():
    run = run_of()
    run.run_json["protein"]["agents"]["a2"]["staged"][ROW["payload_file"]] = "0" * 64
    replayer = ProteinReplayer(run, [])
    assert replayer.copy_problems(replayer.copies()) == [f"a2: {ROW['payload_file']}"]
    with pytest.raises(ReplayRefused, match="a2"):
        replayer.setup(None)


def test_check_design_runs_again_in_the_owners_state():
    replayer = ProteinReplayer(run_of(), [])
    design = records(PAYLOAD)[0][1]
    piece = records(segment_text(PAYLOAD, 2, 3))[0][1]
    assert '"accepted"' in replayer.execute(None, call("check_design", "a1", sequence=design))[0]
    rejected = call("check_design", "a2", sequence=piece)
    output, timed_out = replayer.execute(None, rejected)
    assert '"not_accepted"' in output and not timed_out
    replayer.last = {"a2": {"refs": {}}}
    _, state, dated, undated = replayer.after(None, rejected, output)
    assert state["check"] == {"pieces": ["segment2"], "design": None, "verdict": "not_accepted"}
    assert (dated, undated) == ([], [])
    states = {owner: s for owner, (s, _) in replayer.checkers.items()}
    assert list(states) == ["team"] and states["team"]["accepted"] == 1 and states["team"]["calls"] == 2
    assert [c["agent"] for c in replayer.checks] == ["a1", "a2"]


def test_a_file_holds_its_segments_and_a_whole_design_holds_every_segment():
    replayer = ProteinReplayer(run_of(), [])
    cut = segment_text(PAYLOAD, 3, 3)
    assert replayer.holding({"sha256": "s", "text": cut}) == {"pieces": ["segment3"], "design": None}
    whole = replayer.holding({"sha256": "w", "text": records(PAYLOAD)[0][1]})
    assert whole == {"pieces": ["segment1", "segment2", "segment3"], "design": "planted design 1"}
    assert replayer.holding({"sha256": "big", "text": None}) == {"pieces": None, "design": None}


def test_a_ref_holds_what_its_commits_files_hold():
    replayer = ProteinReplayer(run_of(), [])
    replayer.held = {"c1": {"binder.fasta": {"pieces": ["segment1"], "design": None, "sha256": "x"},
                            "notes.md": {"pieces": [], "design": "planted design 2", "sha256": "y"}},
                     "c2": {}}
    pieces, designs = replayer.ref_readings({"refs/heads/main": "c1", "refs/heads/other": "c2"})
    assert pieces == {"refs/heads/main": ["segment1"]}
    assert designs == {"refs/heads/main": ["planted design 2"]}


def test_which_calls_run_and_which_run_alone():
    replayer = ProteinReplayer(run_of(), [])
    assert replayer.in_sandbox(call("shell", command="ls"))
    assert replayer.in_sandbox(call("check_design", sequence="MK"))
    assert not replayer.in_sandbox(call("board_post", message="hi"))
    assert not replayer.in_sandbox(call("shell")._replace(error="parsing"))
    assert replayer.shared(call("check_design", sequence="MK"))
    assert replayer.shared(call("shell", command="git push origin main"))
    assert not replayer.shared(call("shell", command="ls -la"))


def test_the_machines_have_no_network_and_share_the_team_volume():
    services = ProteinReplayer(run_of(), []).services()
    assert sorted(services) == sorted(PIECES)
    assert all(s["network_mode"] == "none" and s["volumes"] == ["team:/team"] for s in services.values())


def test_the_snapshot_parses_head_branch_refs_and_new_commits():
    read = parse_snapshot("HEAD abc\nBRANCH refs/heads/main\nREF abc refs/heads/main\n"
                          "REF abc refs/remotes/origin/main\nNEW abc a1\n")
    assert read == {"head": "abc", "branch": "main", "new": [("abc", "a1")],
                    "refs": {"refs/heads/main": "abc", "refs/remotes/origin/main": "abc"}}
    assert parse_snapshot("HEAD \nBRANCH \n") == {"head": None, "branch": None, "refs": {}, "new": []}


GIT_ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "PATH": "/usr/bin:/bin",
           "GIT_AUTHOR_NAME": "a1", "GIT_AUTHOR_EMAIL": "a1@team.internal",
           "GIT_COMMITTER_NAME": "a1", "GIT_COMMITTER_EMAIL": "a1@team.internal"}


def git(repo, *args) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True, env=GIT_ENV).stdout.decode()


@pytest.mark.parametrize("detached", [False, True])
def test_a_dated_commit_takes_the_place_of_the_replayed_one(tmp_path, detached):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "commit", "-q", "--allow-empty", "-m", "binder")
    old = git(tmp_path, "rev-parse", "HEAD").strip()
    if detached:
        git(tmp_path, "checkout", "-q", "--detach")
    body = retime(git(tmp_path, "cat-file", "commit", old).encode(), 1793504267, b"+0000", True)
    new = subprocess.run(["bash", "-c", write_object_command(str(tmp_path / ".git"))], input=body,
                         capture_output=True, check=True).stdout.decode().strip()
    assert new == object_id("commit", body)
    subprocess.run(["bash", "-c", rename_command(str(tmp_path / ".git"), {old: new})], check=True)
    assert git(tmp_path, "rev-parse", "main").strip() == new
    assert git(tmp_path, "rev-parse", "HEAD").strip() == new


def test_the_end_state_compares_files_and_calls_by_their_recorded_fields():
    assert triples([{"path": "b", "sha256": "2", "size": 1, "text": "x"}, {"error": "boom"},
                    {"path": "a", "sha256": "1", "size": 3}]) == [("a", "1", 3), ("b", "2", 1)]
    assert recorded_check({"agent": "a1", "owner": "team", "tool": "check_design", "arguments": {"sequence": "MK"},
                           "text": "t", "error": False, "time": 1.5}) == \
        {"agent": "a1", "owner": "team", "tool": "check_design", "arguments": {"sequence": "MK"}, "text": "t",
         "error": False}
