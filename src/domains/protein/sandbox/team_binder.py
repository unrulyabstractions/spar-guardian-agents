"""Run inside an agent's machine as root: the files of commits of the team remote that are new or changed since
staging, as saved_work.py keeps a repository's (no dot paths, no symlinks, nothing over 1 MiB, at most 8 MiB per
commit), each with its path in the repository, its sha256, its size and its text up to 64 KiB. Prints one JSON object
{rev: {"commit", "files"}}; commit is None for a rev the remote lacks. With no rev given, every branch of the remote.

    python3 -c "$(cat team_binder.py)" <remote> <work dir> '<{path: [sha256 staged there in any machine]}>' [rev ...]
"""
import hashlib
import json
import subprocess
import sys

remote, work, staged, revs = sys.argv[1], sys.argv[2], json.loads(sys.argv[3]), sys.argv[4:]


def git(*args):
    return subprocess.run(["git", f"--git-dir={remote}", *args], capture_output=True)


def files_of(commit):
    files, total = [], 0
    listing = git("ls-tree", "-r", "-l", "-z", commit).stdout.decode("utf-8", "replace")
    for line in filter(None, listing.split("\0")):
        meta, path = line.split("\t", 1)
        mode, kind, oid, size = meta.split()
        if kind != "blob" or mode == "120000" or any(p.startswith(".") for p in path.split("/")) \
                or int(size) > 1 << 20:
            continue
        data = git("cat-file", "blob", oid).stdout
        digest = hashlib.sha256(data).hexdigest()
        if digest in staged.get(f"{work}/{path}", []):
            continue
        if total + len(data) > 8 << 20:
            break
        entry = {"path": path, "sha256": digest, "size": len(data)}
        if len(data) <= 64 << 10:
            entry["text"] = data.decode("utf-8", "replace")
        files.append(entry)
        total += len(data)
    return files


if not revs:
    revs = git("for-each-ref", "--format=%(refname:short)", "refs/heads").stdout.decode().split()
found = {}
for rev in revs:
    r = git("rev-parse", "--verify", "-q", f"{rev}^{{commit}}")
    commit = r.stdout.decode().strip() if r.returncode == 0 else None
    found[rev] = {"commit": commit, "files": files_of(commit) if commit else []}
print(json.dumps(found))
