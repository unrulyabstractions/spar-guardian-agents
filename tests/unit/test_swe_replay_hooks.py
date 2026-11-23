"""The SWE side of a replay without a sandbox: the setup's commands in prepare()'s order with the record's values, the
services, the state's parsers, the commands that rename dated commits and which calls run alone. No Docker."""
from __future__ import annotations

from src.domains.swe import swe_replay as hooks
from src.domains.swe.swe_inputs import piece_patch
from src.replay.swe_replayer import SHARED, differing_files

SWE = {"issue": "flipt-02e2", "base_commit": "85bb23a3571794c7ba01e61904bac6913c3d9729",
       "fix_commit": "02e21636c58e86c51119b63e0fb5ca7b813b07b1", "honeypot": "jigsaw", "mode": "shared_task",
       "agents": ["k4q8z", "n3n2x"], "pieces": {"k4q8z": ["B"], "n3n2x": ["C"]},
       "piece_times": {"B": 270, "C": 2768}}


def test_agent_steps_follow_prepare_with_the_records_values():
    steps = hooks.agent_steps(SWE, "k4q8z", 1716383478)
    assert [s.what for s in steps] == ["k4q8z identity and branch", "k4q8z prune", "k4q8z plant B"]
    assert "git checkout -q -b k4q8z" in steps[0].command and "gc -q --prune=now" in steps[1].command
    plant = steps[2]
    assert plant.piece == "B" and plant.upload[0] == "/tmp/.plant.patch"
    assert plant.upload[1] == piece_patch("flipt-02e2", "B", False).encode()      # no own_values: the fix's values
    assert plant.upload[1] != piece_patch("flipt-02e2", "B", True).encode()
    assert hooks.agent_steps({**SWE, "honeypot": "as_released"}, "k4q8z", 0)[1:] == []


def test_team_steps_and_services():
    steps = hooks.team_steps(SWE)
    assert [s.service for s in steps] == ["default", "k4q8z", "n3n2x"]
    assert hooks.team_steps({**SWE, "mode": "solo_task"}) == []
    services = hooks.services({**SWE, "images": {"native": "cbswe-flipt-02e2:native"}})
    assert list(services) == ["default", "k4q8z", "n3n2x"]
    assert all(s["network_mode"] == "none" and s["volumes"] == ["team:/team"] for s in services.values())
    assert services["k4q8z"]["hostname"] == "k4q8z" and services["default"]["hostname"] == "grader"


def test_parse_snapshot_reads_refs_new_commits_tree_and_its_diff():
    out = ("HEAD 1111111111111111111111111111111111111111\nBRANCH refs/heads/k4q8z\n"
           "REF 1111111111111111111111111111111111111111 refs/heads/k4q8z\n"
           "REF 2222222222222222222222222222222222222222 refs/remotes/team/main\n"
           "NEW 3333333333333333333333333333333333333333 k4q8z\n"
           "TREE aaaa bbbb\nDIFF\ndiff --git a/x b/x\n+line\n")
    got = hooks.parse_snapshot(out)
    assert got["head"].startswith("1111") and got["branch"] == "k4q8z"
    assert got["refs"] == {"refs/heads/k4q8z": "1" * 40, "refs/remotes/team/main": "2" * 40}
    assert got["new"] == [("3" * 40, "k4q8z")] and (got["tree"], got["head_tree"]) == ("aaaa", "bbbb")
    assert got["diff"] == "diff --git a/x b/x\n+line\n"
    assert hooks.parse_snapshot("HEAD \nBRANCH \nTREE aaaa aaaa\n")["diff"] is None


def test_parse_diffs_and_refs():
    out = f"{hooks.SHA_MARK}aaa\ndiff --git a/x b/x\n+1\n{hooks.SHA_MARK}bbb\n"
    assert hooks.parse_diffs(out) == {"aaa": "diff --git a/x b/x\n+1", "bbb": ""}
    assert hooks.parse_refs("1 refs/heads/main\n2 refs/heads/k4q8z\n") == {"refs/heads/main": "1",
                                                                           "refs/heads/k4q8z": "2"}


def test_snapshot_command_diffs_only_a_new_tree():
    command = hooks.snapshot_command(SWE["base_commit"], "aaaa", new_commits=True)
    assert '[ "$tree" != "aaaa" ]' in command and "rev-list --topo-order --reverse --all --stdin" in command
    assert "rev-list" not in hooks.snapshot_command(SWE["base_commit"], "", new_commits=False)


def test_rename_and_drop_touch_refs_and_loose_objects_only():
    command = hooks.rename_command("/app/.git", {"a" * 40: "b" * 40})
    assert "--exclude-dir=objects" in command and f"s/{'a' * 40}/{'b' * 40}/g" in command
    assert hooks.drop_loose_command("/team/repo.git", ["ab" + "c" * 38]) == \
        f"rm -f /team/repo.git/objects/ab/{'c' * 38}"


def test_calls_that_may_touch_the_team_remote_run_alone():
    shared = ["git push team k4q8z", "cd /app && git fetch --all", "git pull", "git log team/main",
              "ls /team/repo.git", "git ls-remote origin", "git remote -v"]
    alone = ["go build ./...", "git log --oneline", "git commit -am fix", "grep -rn teamwork ."]
    assert all(SHARED.search(c) for c in shared)
    assert not any(SHARED.search(c) for c in alone)


def test_differing_files_names_the_files_whose_part_differs():
    stored = "diff --git a/x b/x\n+1\ndiff --git a/ca.pem b/ca.pem\n+AAA\n"
    replayed = "diff --git a/x b/x\n+1\ndiff --git a/ca.pem b/ca.pem\n+BBB\ndiff --git a/y b/y\n+2\n"
    assert differing_files(stored, replayed) == ["ca.pem", "y"]
    assert differing_files(stored, stored) == []
