"""The order of calls that touch the team remote: a call that read the remote before an overlapping earlier call of
another agent wrote it plays against the remote as it stood before that write. No Docker: a fake sandbox holds the
remote's refs."""
from __future__ import annotations

import subprocess

import pytest

from src.domains.swe import swe_replay as hooks
from src.replay.git_state import SHARED, changed_refs, read_before_write, reads_team_only, shows_before
from src.replay.recorded_calls import RecordedCall
from src.replay.replay_run import OutputCut, play

MAIN = "refs/heads/main"
BASE, PUSHED = "85bb23a3571794c7ba01e61904bac6913c3d9729", "000abd88e2b0e6d5c7f1a3b4c5d6e7f8091a2b3c"
WRITE = "cd /app && git commit -qm fix && git fetch -q team && git merge -q team/main; git push -q team HEAD:main"
READ = "cd /app && git commit -qm tls && git fetch -q team && git log --oneline -1 team/main"


def call(n: int, agent: str, command: str, result: str = "", t0: float = 0.0, t1: float = 1.0) -> RecordedCall:
    return RecordedCall(n=n, agent=agent, k=n, id=f"c{n}", tool="shell", arguments={"command": command},
                        result=result, error=None, t0=t0, t1=t1)


def test_a_read_of_the_team_remote_is_a_git_read_that_names_it_and_cannot_write_it():
    reads = ["git fetch -q team", "cd /app && git fetch team >/dev/null 2>&1; git log -3 team/main",
             "git pull --rebase team main", "git ls-remote team", "git fetch --all", "git remote update"]
    others = ["git fetch team && git push team HEAD:main", "git log --oneline -3 team/main",
              "git checkout -B reland team/main", "git fetch /tmp/t.git main", "git ls-remote /tmp/t.git",
              "git --git-dir=/team/repo.git show-ref; git fetch team", "cp x /team/y && git fetch team",
              "git fetch origin; echo team"]
    assert all(reads_team_only(c) for c in reads)
    assert not any(reads_team_only(c) for c in others)
    assert all(SHARED.search(c) for c in reads)


def test_changed_refs_names_each_ref_a_write_moved_made_or_removed():
    before = {MAIN: BASE, "refs/heads/a": "1" * 40, "refs/heads/gone": "2" * 40}
    after = {MAIN: PUSHED, "refs/heads/a": "1" * 40, "refs/heads/new": PUSHED}
    assert changed_refs(before, after) == {MAIN: (BASE, PUSHED), "refs/heads/gone": ("2" * 40, None),
                                           "refs/heads/new": (None, PUSHED)}
    assert changed_refs(before, before) == {}


def test_an_output_shows_the_remote_before_a_write_by_an_old_tip_and_no_new_one():
    changed = {MAIN: (BASE, PUSHED), "refs/heads/z4j2q": (None, PUSHED)}
    assert shows_before("85bb23a3 Merge pull request #3102\n", changed)
    assert not shows_before("000abd88 feat(cache)\n85bb23a3 Merge pull request #3102\n", changed)
    assert not shows_before("ok  go.flipt.io/flipt/internal/config\n", changed)
    assert not shows_before("85bb23 is too short to name a commit\n", changed)
    assert not shows_before("85bb23a3\n", {MAIN: (BASE, BASE[:8] + "f" * 32)})


def test_a_read_goes_before_a_write_only_with_the_records_evidence():
    writer = call(31, "z4j2q", WRITE, t0=729.2, t1=746.4)
    changed = {MAIN: (BASE, PUSHED)}
    reader = call(32, "x7p7q", READ, "85bb23a3 Merge pull request #3102\n", t0=730.1, t1=755.6)
    assert read_before_write(reader, READ, writer, changed)
    assert not read_before_write(reader._replace(agent="z4j2q"), READ, writer, changed)
    assert not read_before_write(reader._replace(t0=746.5), READ, writer, changed)
    assert not read_before_write(reader._replace(result="000abd88 feat\n"), READ, writer, changed)
    assert not read_before_write(reader, READ, writer, {})
    assert not read_before_write(reader, READ + " && git push team x7p7q", writer, changed)


class Remote:
    """The fake sandbox: the team remote's refs."""
    def __init__(self, refs: dict[str, str]) -> None:
        self.refs = dict(refs)


class FakeReplayer:
    """Plays a call on the fake remote: a push sets main, a fetch prints main's short id, "sneaky" writes a ref."""
    agents = ["z4j2q", "x7p7q", "q6j6x"]

    def in_sandbox(self, c):
        return True

    def shared(self, c):
        return bool(SHARED.search(self.command(c)))

    @staticmethod
    def command(c):
        return c.arguments["command"]

    def execute(self, box, c):
        command = self.command(c)
        if "sneaky" in command:
            box.refs["refs/heads/sneaky"] = PUSHED
        if "push" in command:
            box.refs[MAIN] = PUSHED
            return "", False
        if "fetch" in command:
            return box.refs[MAIN][:8] + " Merge pull request #3102\n", False
        return "ok\n", False

    def after(self, box, c, output):
        return output, {"agent": c.agent}, [], []

    def team_state(self, box):
        return {"refs": dict(box.refs), "ref_pieces": {}}

    def set_team_refs(self, box, refs):
        box.refs = dict(refs)


class OrderOnly(FakeReplayer):
    """A replayer without set_team_refs: its calls keep the recorded order."""
    set_team_refs = None


def played(replayer, calls, parallel=True):
    box = Remote({MAIN: BASE})
    return box, play(box, replayer, calls, OutputCut(16 * 1024, False), parallel, team0={"refs": {MAIN: BASE}})


@pytest.mark.parametrize("parallel", [True, False])
def test_a_read_that_saw_the_remote_before_an_overlapping_write_plays_against_it(parallel):
    writer = call(1, "z4j2q", WRITE, t0=0.0, t1=10.0)
    reader = call(2, "x7p7q", READ, "85bb23a3 Merge pull request #3102\n", t0=1.0, t1=12.0)
    later = call(3, "q6j6x", "git fetch -q team && git log --oneline -1 team/main", "000abd88 Merge pull request "
                 "#3102\n", t0=13.0, t1=14.0)
    box, done = played(FakeReplayer(), [writer, reader, later], parallel)
    assert done[2]["match"] == "exact" and done[2]["read_before"] == {"writer": 1, "refs": {MAIN: [BASE, PUSHED]}}
    assert done[2]["team"]["refs"] == {MAIN: PUSHED} and box.refs == {MAIN: PUSHED}
    assert done[3]["match"] == "exact" and "read_before" not in done[3]


def test_a_read_that_saw_the_write_or_started_after_it_keeps_the_recorded_order():
    writer = call(1, "z4j2q", WRITE, t0=0.0, t1=10.0)
    saw = call(2, "x7p7q", READ, "000abd88 Merge pull request #3102\n", t0=1.0, t1=12.0)
    after = call(2, "x7p7q", READ, "85bb23a3 Merge pull request #3102\n", t0=10.5, t1=12.0)
    _, done = played(FakeReplayer(), [writer, saw])
    assert done[2]["match"] == "exact" and "read_before" not in done[2]
    _, done = played(FakeReplayer(), [writer, after])
    assert done[2]["match"] == "differs" and "read_before" not in done[2]
    _, done = played(OrderOnly(), [writer, saw._replace(result="85bb23a3 Merge pull request #3102\n")])
    assert done[2]["match"] == "differs" and "read_before" not in done[2]


def test_a_read_that_writes_while_the_remote_stands_before_the_write_is_an_error():
    writer = call(1, "z4j2q", WRITE, t0=0.0, t1=10.0)
    reader = call(2, "x7p7q", READ + " # sneaky", "85bb23a3 Merge pull request #3102\n", t0=1.0, t1=12.0)
    box = Remote({MAIN: BASE})
    with pytest.raises(RuntimeError, match="n2 changed the team remote"):
        play(box, FakeReplayer(), [writer, reader], OutputCut(16 * 1024, False), True, team0={"refs": {MAIN: BASE}})
    assert box.refs == {MAIN: PUSHED}


def test_set_refs_command_turns_a_repositorys_refs_into_the_wanted_ones(tmp_path):
    def sh(command: str) -> str:
        return subprocess.run(["bash", "-c", command], check=True, capture_output=True, text=True).stdout

    work, repo = tmp_path / "work", tmp_path / "repo.git"
    sh(f"git init -q {work} && git init -q --bare {repo}")
    commit = f"git -C {work} -c user.name=t -c user.email=t@t commit -q --allow-empty -m"
    sh(f"{commit} one && {commit} two")
    first, second = sh(f"git -C {work} rev-parse HEAD~1 HEAD").split()
    sh(f"git -C {work} push -q {repo} HEAD~1:refs/heads/main HEAD~1:refs/heads/packed && "
       f"git --git-dir={repo} pack-refs --all && git -C {work} push -q {repo} HEAD:refs/heads/main HEAD:refs/heads/new")
    packed = (repo / "packed-refs").read_bytes()

    def refs() -> dict[str, str]:
        return hooks.parse_refs(sh(f"git --git-dir={repo} for-each-ref --format='%(objectname) %(refname)'"))

    after = refs()
    before = {MAIN: first, "refs/heads/packed": first}
    assert after == {MAIN: second, "refs/heads/new": second, "refs/heads/packed": first}
    sh(hooks.set_refs_command(str(repo), after, before))
    assert refs() == before
    sh(hooks.set_refs_command(str(repo), before, after))
    assert refs() == after and (repo / "packed-refs").read_bytes() == packed
    assert hooks.set_refs_command(str(repo), after, after) == ""
    with pytest.raises(ValueError, match="not a commit id"):
        hooks.set_refs_command(str(repo), {}, {MAIN: "$(touch x)"})
