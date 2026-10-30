"""The sweep of a box: every file the capture has to account for, as JSON lines. It runs on the box.

    ssh -F logs/boxes/<id>/ssh_config box python3 - sweep /root/work /root /tmp < scripts/box/box_sweep.py
    ssh -F logs/boxes/<id>/ssh_config box python3 - baseline /root /tmp < scripts/box/box_sweep.py

box_capture.py sends this file to the box's own python3 on ssh's standard input, so it uses the standard library
alone and needs nothing installed. `sweep <repo> <home> <tmp>` walks every regular file under the repository's clone,
the home folder and /tmp, without following links, and prints one line per file with its size and sha256, except
what is provably reproducible, which it counts by reason (counts and bytes):
  - a tracked file whose bytes are its blob at the HEAD of its work tree (the repository, each submodule); the work
    trees are printed with their HEAD and the commit the repository records, for the check to hold them to;
  - the git object stores of those work trees, the virtualenv, build/, the editable install's metadata, package and
    font caches, tiktoken's downloads, uv's Pythons, binaries and receipt, docker's client config, the files
    box_setup.sh sent, bytecode and tool caches, systemd's private /tmp folders;
  - the keys file box_run.sh writes (and its temporary copy), the box's id file and vast.ai's authorized_keys.
The outputs under runs/, logs/ and out/ are never excluded. It also prints symbolic links with their targets (a link
whose target exists outside the swept roots on a line of its own, since what it points at is not swept), special
files, every path it could not read, the processes that still produce (before and after the walk: a runner, a screen,
an example or `python -m src` by their command line, and any process holding a file under the roots open for
writing), what Docker holds (containers running or stopped, volumes; or why it cannot tell), the box's id from
<home>/box_id, and last a `done` line, so a cut stream fails the check. `baseline <home> <tmp>` is the same sweep
before the repository exists: the host image's own files, which box_setup.sh records at first contact.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys

OUTPUT_DIRS = ("runs", "logs", "out")
CACHE_NAMES = {"__pycache__": "bytecode caches, compiled from the tracked source",
               ".pytest_cache": "pytest's cache", ".ruff_cache": "ruff's cache"}
PRODUCERS = ("examples.", "scripts.screen", "scripts.box.box_runner", "src")
GIT_STORES = "git's stores of the work trees, clones at the commits the work trees report"
KEYS = "the keys box_run.sh wrote from the Mac's environment, and its temporary copy (never copied)"
BOX_ID = "box_id"
"""The file under home that names the instance, written by box_setup.sh at first contact."""


def excluded_trees(repo: str | None, home: str, tmp: str) -> dict[str, str]:
    """{absolute folder: reason} for the trees that are reproducible elsewhere."""
    trees = {
        f"{home}/.cache/uv": "uv's package cache", f"{home}/.cache/pip": "pip's package cache",
        f"{home}/.cache/huggingface": "Hugging Face downloads", f"{home}/.local/share/uv": "uv's Python installs",
        f"{home}/.local/bin": "uv's installer binaries", f"{home}/.config/uv": "uv's installer receipt",
        f"{home}/.cache/matplotlib": "matplotlib's font cache", f"{home}/.docker": "docker's client config",
        f"{home}/ship": "the files box_setup.sh sent from the Mac (the lucy bundle, the package list)",
    }
    trees[f"{tmp}/data-gym-cache"] = "tiktoken's encoding downloads"
    if repo:
        trees |= {f"{repo}/.venv": "the virtualenv box_setup.sh builds from the Mac's package list",
                  f"{repo}/build": "the task's data and image inputs, rebuilt from CheatBench by box_setup.sh"}
    return trees


def excluded_files(home: str) -> dict[str, str]:
    return {f"{home}/.ssh/authorized_keys": "vast.ai's copy of the account's public key",
            f"{home}/{BOX_ID}": "the box's instance id, written by box_setup.sh"}


def git(tree: str, *args: str) -> bytes:
    return subprocess.run(["git", "-C", tree, *args], check=True, capture_output=True).stdout


def tree_blobs(tree: str) -> dict[str, tuple[str, str]]:
    """{path: (mode, blob sha)} of every file at the work tree's HEAD."""
    blobs = {}
    for entry in git(tree, "ls-tree", "-r", "-z", "--full-tree", "HEAD").split(b"\0"):
        if entry:
            meta, path = entry.decode().split("\t", 1)
            mode, kind, sha = meta.split()
            blobs[path] = (mode, sha)
    return blobs


def work_trees(repo: str) -> dict[str, dict]:
    """{work tree: {head, recorded, blobs}}: the repository and each submodule checked out in it (its .git exists),
    with the commit the repository's HEAD records for it."""
    trees = {repo: {"head": git(repo, "rev-parse", "HEAD").decode().strip(), "recorded": None,
                    "blobs": tree_blobs(repo)}}
    for path, (mode, sha) in list(trees[repo]["blobs"].items()):
        sub = f"{repo}/{path}"
        if mode == "160000" and os.path.lexists(f"{sub}/.git"):
            trees[sub] = {"head": git(sub, "rev-parse", "HEAD").decode().strip(), "recorded": sha,
                          "blobs": tree_blobs(sub)}
    return trees


def blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


def file_blob_sha(path: str, size: int) -> str:
    digest = hashlib.sha1(b"blob %d\0" % size)
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def under(path: str, roots) -> bool:
    return any(path == root or path.startswith(root + "/") for root in roots)


def command_line(proc: str, pid: str) -> list[str] | None:
    try:
        with open(f"{proc}/{pid}/cmdline", "rb") as handle:
            return [a.decode(errors="replace") for a in handle.read().split(b"\0") if a]
    except OSError:
        return None


def writing_to(proc: str, pid: str) -> list[str]:
    """The paths the process holds open for writing (O_WRONLY or O_RDWR, from /proc/<pid>/fdinfo)."""
    found = []
    try:
        fds = os.listdir(f"{proc}/{pid}/fd")
    except OSError:
        return found
    for fd in fds:
        try:
            target = os.readlink(f"{proc}/{pid}/fd/{fd}")
            with open(f"{proc}/{pid}/fdinfo/{fd}") as handle:
                flags = next(line for line in handle if line.startswith("flags:"))
        except (OSError, StopIteration):
            continue
        if int(flags.split()[1], 8) & 0o3 and target.startswith("/"):
            found.append(target)
    return found


def producing(proc: str = "/proc", roots: tuple[str, ...] = (), skip=lambda path: False) -> list[dict]:
    """The processes that still write the outputs: a runner, a screen, an example or `python -m src` by their command
    line, and any other process holding a file under the roots open for writing (skip names the paths that do not
    count, such as host services' private /tmp folders)."""
    found = []
    if not os.path.isdir(proc):
        return found
    for pid in filter(str.isdigit, os.listdir(proc)):
        if int(pid) == os.getpid():
            continue
        args = command_line(proc, pid)
        if args is None:
            continue
        module = args[args.index("-m") + 1] if "-m" in args[:-1] else ""
        if module.startswith(PRODUCERS) and (module != "scripts.box.box_runner" or "run" in args):
            found.append({"producing": int(pid), "command": " ".join(args)[:300]})
            continue
        held = [path for path in writing_to(proc, pid) if under(path, roots) and not skip(path)]
        if held:
            found.append({"producing": int(pid), "command": " ".join(args)[:300], "writing": held[:5]})
    return found


def docker_state() -> dict:
    """What Docker holds: the running containers, every container with its status, every volume; or why it cannot
    tell, which the check counts as a failure."""
    try:
        def lines(*args: str) -> list[str]:
            out = subprocess.run(["docker", *args], check=True, capture_output=True, text=True, timeout=120).stdout
            return [line for line in out.splitlines() if line.strip()]
        return {"docker": {"running": lines("ps", "-q"),
                           "containers": lines("ps", "-a", "--format", "{{.Names}} {{.Status}}"),
                           "volumes": lines("volume", "ls", "-q")}}
    except (OSError, subprocess.SubprocessError) as error:
        return {"docker": None, "error": f"{type(error).__name__}: {error}"[:300]}


class Sweep:
    """One walk over the roots; lines() yields the JSON lines."""

    def __init__(self, roots: list[str], repo: str | None, home: str, tmp: str, proc: str = "/proc",
                 docker=docker_state):
        self.roots, self.repo, self.home, self.tmp, self.proc = [r.rstrip("/") for r in roots], repo, home, tmp, proc
        self.real_roots = [os.path.realpath(r) for r in self.roots]
        self.docker = docker
        self.trees_excluded = excluded_trees(repo, home, tmp)
        self.files_excluded = excluded_files(home)
        self.counts: dict[str, list[int]] = {}
        self.trees = work_trees(repo) if repo and os.path.isdir(f"{repo}/.git") else {}

    def count(self, reason: str, files: int, size: int) -> None:
        total = self.counts.setdefault(reason, [0, 0])
        total[0] += files
        total[1] += size

    def count_tree(self, folder: str, reason: str) -> None:
        for dirpath, _, filenames in os.walk(folder):
            for name in filenames:
                try:
                    self.count(reason, 1, os.lstat(os.path.join(dirpath, name)).st_size)
                except OSError:
                    self.count(reason, 1, 0)

    def in_outputs(self, path: str) -> bool:
        return bool(self.repo) and any(path.startswith(f"{self.repo}/{d}/") or path == f"{self.repo}/{d}"
                                       for d in OUTPUT_DIRS)

    def private_tmp(self, path: str) -> bool:
        return path.startswith(f"{self.tmp}/systemd-private-")

    def folder_reason(self, path: str, name: str) -> str | None:
        if self.in_outputs(path):
            return None
        if path in self.trees_excluded:
            return self.trees_excluded[path]
        if name == ".git" and os.path.dirname(path) in self.trees:
            return GIT_STORES
        if name in CACHE_NAMES:
            return CACHE_NAMES[name]
        if name.endswith(".egg-info"):
            return "the editable install's metadata, written by box_setup.sh"
        if os.path.dirname(path) == self.tmp and name.startswith("systemd-private-"):
            return "systemd's private /tmp folders of host services"
        return None

    def tracked(self, path: str, info: os.stat_result) -> bool:
        """Whether a file is a tracked file of its work tree with the bytes of its blob at HEAD."""
        if self.in_outputs(path):
            return False
        tree = max((t for t in self.trees if path.startswith(t + "/")), key=len, default=None)
        blob = tree and self.trees[tree]["blobs"].get(os.path.relpath(path, tree))
        if not blob:
            return False
        if stat.S_ISLNK(info.st_mode):
            return blob[0] == "120000" and blob_sha(os.readlink(path).encode()) == blob[1]
        return blob[0] in ("100644", "100755") and file_blob_sha(path, info.st_size) == blob[1]

    def link(self, path: str) -> dict:
        """A link's line: its target, and on a line of its own when the target exists outside the swept roots."""
        target, real = os.readlink(path), os.path.realpath(path)
        if os.path.exists(real) and not under(real, self.real_roots):
            return {"outside_link": path, "target": target, "resolves": real}
        return {"link": path, "target": target}

    def entry(self, path: str) -> dict | None:
        """The line for one path that is no folder, or None when it is counted as excluded."""
        info = os.lstat(path)
        if path in self.files_excluded:
            self.count(self.files_excluded[path], 1, info.st_size)
            return None
        if path.startswith(f"{self.home}/.box_env"):
            self.count(KEYS, 1, info.st_size)
            return None
        if os.path.basename(path) == ".git" and os.path.dirname(path) in self.trees:
            self.count(GIT_STORES, 1, info.st_size)
            return None
        if (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)) and self.tracked(path, info):
            self.count("tracked files unchanged from their work tree's HEAD", 1, info.st_size)
            return None
        if stat.S_ISLNK(info.st_mode):
            return self.link(path)
        if stat.S_ISREG(info.st_mode):
            return {"file": path, "size": info.st_size, "sha256": sha256(path)}
        return {"special": path, "mode": stat.filemode(info.st_mode)}

    def walk(self, root: str):
        errors = []
        for dirpath, dirnames, filenames in os.walk(root, onerror=errors.append):
            keep = []
            for name in sorted(dirnames):
                path = os.path.join(dirpath, name)
                if path in self.roots and path != root:
                    continue
                reason = self.folder_reason(path, name)
                if reason:
                    self.count_tree(path, reason)
                elif os.path.islink(path):
                    filenames.append(name)
                else:
                    keep.append(name)
            dirnames[:] = keep
            for name in sorted(filenames):
                path = os.path.join(dirpath, name)
                try:
                    line = self.entry(path)
                except OSError as error:
                    line = {"unreadable": path, "error": str(error)}
                if line:
                    yield line
        for error in errors:
            yield {"unreadable": getattr(error, "filename", None) or root, "error": str(error)}

    def not_counted(self, path: str) -> bool:
        """An open file that is no sign of production: host services' private /tmp, an excluded tree."""
        return self.private_tmp(path) or under(path, list(self.trees_excluded))

    def box_id(self) -> dict:
        try:
            with open(f"{self.home}/{BOX_ID}") as handle:
                return {"box_id": handle.read().strip()}
        except OSError:
            return {"box_id": None}

    def lines(self):
        for tree, facts in self.trees.items():
            yield {"tree": tree, "head": facts["head"], "recorded": facts["recorded"]}
        yield from producing(self.proc, tuple(self.roots), self.not_counted)
        for root in self.roots:
            if os.path.isdir(root):
                yield from self.walk(root)
            else:
                yield {"unreadable": root, "error": "no such folder"}
        for reason, (files, size) in self.counts.items():
            yield {"excluded": reason, "files": files, "bytes": size}
        yield from producing(self.proc, tuple(self.roots), self.not_counted)
        yield self.docker()
        yield self.box_id()
        yield {"done": True, "roots": self.roots}


def main(argv: list[str]) -> int:
    if argv[:1] == ["sweep"] and len(argv) == 4:
        repo, home, tmp = argv[1:]
        sweep = Sweep([repo, home, tmp], repo, home, tmp)
    elif argv[:1] == ["baseline"] and len(argv) == 3:
        home, tmp = argv[1:]
        sweep = Sweep([home, tmp], None, home, tmp)
    else:
        print(__doc__, file=sys.stderr)
        return 2
    for line in sweep.lines():
        sys.stdout.write(json.dumps(line) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
