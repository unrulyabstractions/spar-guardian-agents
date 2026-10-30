"""The team remote's lock (src/sandbox/team_remote_lock.sh) on a real bare repository: git keeps working, and neither a
commondir nor a .git planted in it can send git to a repository an agent controls. A test runs as one user, so the
sticky bit's refusal itself is the smoke suites' to show."""
from __future__ import annotations

import grp
import os
import stat
import subprocess
from pathlib import Path

from src.domains.openmath import openmath_sandbox
from src.domains.openmath.openmath_settings import TEAM_GROUP
from src.domains.openmath.openmath_settings import TEAM_REMOTE as OPENMATH_REMOTE
from src.domains.protein import protein_scoring
from src.domains.protein.protein_settings import TEAM_REMOTE as PROTEIN_REMOTE
from src.sandbox.agent_tools import AGENT_NAME
from src.sandbox.staging import team_remote_lock


def git(*args: str, cwd: Path | None = None) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": "a@b", "GIT_COMMITTER_NAME": "a",
           "GIT_COMMITTER_EMAIL": "a@b"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout


def locked_remote(tmp_path: Path) -> Path:
    remote = tmp_path / "repo.git"
    git("init", "-q", "--bare", "--shared=group", "--initial-branch=main", str(remote))
    lines = team_remote_lock(str(remote), grp.getgrgid(os.getgid()).gr_name)
    subprocess.run(["bash", "-c", "set -euo pipefail\n" + lines], check=True, capture_output=True, text=True)
    return remote


def plant_repository(where: Path, objects: Path, proof: Path) -> None:
    """A git directory that shares the remote's objects, with a hook that leaves `proof` behind."""
    (where / "hooks").mkdir(parents=True, exist_ok=True)
    (where / "refs" / "heads").mkdir(parents=True)
    (where / "objects" / "info").mkdir(parents=True)
    (where / "objects" / "info" / "alternates").write_text(f"{objects}\n")
    (where / "HEAD").write_text("ref: refs/heads/main\n")
    (where / "config").write_text("[core]\n\trepositoryformatversion = 0\n\tbare = true\n")
    hook = where / "hooks" / "pre-receive"
    hook.write_text(f"#!/bin/sh\ntouch {proof}\n")
    hook.chmod(0o755)


def test_the_lock_leaves_the_group_a_working_remote(tmp_path):
    remote = locked_remote(tmp_path)
    assert stat.S_IMODE(remote.stat().st_mode) & stat.S_ISVTX, "the sticky bit"
    assert (remote / "commondir").read_text() == ".\n" and stat.S_IMODE((remote / "commondir").stat().st_mode) == 0o644
    assert list((remote / ".git").iterdir()) == [] and not (remote / ".git").stat().st_mode & stat.S_IWGRP
    assert not (remote / "config").stat().st_mode & stat.S_IWGRP
    assert not (remote / "hooks").stat().st_mode & stat.S_IWGRP
    assert (remote / "refs").stat().st_mode & stat.S_IWGRP and (remote / "objects").stat().st_mode & stat.S_IWGRP
    work = tmp_path / "work"
    git("clone", "-q", str(remote), str(work))
    (work / "f").write_text("x\n")
    git("add", "f", cwd=work)
    git("commit", "-qm", "one", cwd=work)
    git("branch", "-M", "main", cwd=work)
    git("push", "-q", "origin", "main", "main:side", cwd=work)
    git("push", "-q", "origin", "--delete", "side", cwd=work)
    other = tmp_path / "other"
    git("clone", "-q", str(remote), str(other))
    assert (other / "f").read_text() == "x\n"
    git("fetch", "-q", cwd=other)
    git(f"--git-dir={remote}", "gc", "-q")
    assert git(f"--git-dir={remote}", "log", "--format=%s", "main").strip() == "one"


def test_git_enters_a_repository_planted_in_a_remote_without_the_lock(tmp_path):
    """The routes the lock closes, shown open: a .git directory beside the remote's files takes a teammate's push."""
    remote = tmp_path / "repo.git"
    git("init", "-q", "--bare", "--initial-branch=main", str(remote))
    proof = tmp_path / "ran"
    plant_repository(remote / ".git", remote / "objects", proof)
    work = tmp_path / "work"
    git("init", "-q", "--initial-branch=main", str(work))
    (work / "f").write_text("x\n")
    git("add", "f", cwd=work)
    git("commit", "-qm", "one", cwd=work)
    git("push", "-q", str(remote), "main", cwd=work)
    assert proof.exists(), "an unlocked remote ran the planted hook"


def test_the_empty_git_directory_keeps_the_push_in_the_remote(tmp_path):
    remote = locked_remote(tmp_path)
    work = tmp_path / "work"
    git("init", "-q", "--initial-branch=main", str(work))
    (work / "f").write_text("x\n")
    git("add", "f", cwd=work)
    git("commit", "-qm", "one", cwd=work)
    git("push", "-q", str(remote), "main", cwd=work)
    assert git(f"--git-dir={remote}", "rev-parse", "--verify", "-q", "refs/heads/main").strip()
    assert git(f"--git-dir={remote}", "rev-parse", "--git-common-dir").strip() == str(remote)


def test_both_domains_lock_their_remote():
    locked = ["printf '.\\n' > {r}/commondir", "mkdir -m 755 {r}/.git", "chmod +t {r}"]
    assert all(line.format(r=PROTEIN_REMOTE) in protein_scoring.TEAM_REMOTE_INIT for line in locked)
    seats = {a: {"uid": 1001 + i, "workdir": f"/workspace/{a}", "pieces": None} for i, a in enumerate(("a01", "a02"))}
    script = openmath_sandbox.setup_script({"layout": "split", "agents": seats, "remote_branches": ["main"],
                                            "archive": {"name": "x.zip", "commit": "c", "branch": "b"}, "n_pieces": 2})
    assert all(line.format(r=OPENMATH_REMOTE) in script for line in locked) and f"chgrp -R {TEAM_GROUP} " in script
    assert script.index("git init") < script.index("chmod +t"), script
    assert "{lock}" not in script and AGENT_NAME in protein_scoring.TEAM_REMOTE_INIT
