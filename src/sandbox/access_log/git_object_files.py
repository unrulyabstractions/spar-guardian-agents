"""Run inside a container as root: the files that hold what a set of refs adds over the rest of a repository.

stdin is {"repo": path, "refs": [names], "others": [names]}. Prints one JSON list of {path, object, level, kind,
packed}, one entry per object `git rev-list --objects refs --not others` gives and one per ref. A blob is level content;
a commit, a tree or a ref only leads to content, so it is level pointer. An object stored in a pack, or a ref in
packed-refs, has no file of its own to watch and comes back packed.
"""
import json
import os
import subprocess
import sys

spec = json.load(sys.stdin)


def git(*args, stdin=None):
    return subprocess.run(["git", "-c", "safe.directory=*", "-C", spec["repo"], *args], input=stdin,
                          capture_output=True, text=True, check=True).stdout


common = os.path.normpath(os.path.join(spec["repo"], git("rev-parse", "--git-common-dir").strip()))
names = {}
for line in git("rev-list", "--objects", *spec["refs"], "--not", *spec["others"]).splitlines():
    oid, _, name = line.partition(" ")
    names[oid] = name
kinds = dict(line.split() for line in git("cat-file", "--batch-check=%(objectname) %(objecttype)",
                                          stdin="".join(f"{oid}\n" for oid in names)).splitlines())
found = []
for oid, name in names.items():
    path = f"{common}/objects/{oid[:2]}/{oid[2:]}"
    found.append({"path": path, "object": name or oid[:10], "level": "content" if kinds[oid] == "blob" else "pointer",
                  "kind": kinds[oid], "packed": not os.path.isfile(path)})
for ref in spec["refs"]:
    path = f"{common}/{ref}" if ref.startswith("refs/") else f"{common}/refs/heads/{ref}"
    found.append({"path": path, "object": ref, "level": "pointer", "kind": "ref", "packed": not os.path.isfile(path)})
print(json.dumps(found))
