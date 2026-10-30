"""Run inside an agent's container as root: the files under a root that are new or changed since staging, as
CheatBench's SavedWorkObserver keeps them (no dot paths, no symlinks, nothing over 1 MiB, at most 8 MiB in all), each
with its sha256, size and text up to 64 KiB. Prints one JSON list.

    python3 -c "$(cat saved_work.py)" <root> '<{path: sha256} of the staged files>'
"""
import hashlib
import json
import os
import sys

root, staged = sys.argv[1], json.loads(sys.argv[2])
paths = []
for dirpath, dirs, names in os.walk(root):
    dirs[:] = [d for d in dirs if not d.startswith(".")]
    for name in names:
        path = os.path.join(dirpath, name)
        if not name.startswith(".") and not os.path.islink(path) and os.path.isfile(path) \
                and os.path.getsize(path) <= 1 << 20:
            paths.append(path)
files, total = [], 0
for path in sorted(paths):
    data = open(path, "rb").read()
    digest = hashlib.sha256(data).hexdigest()
    if staged.get(path) == digest:
        continue
    if total + len(data) > 8 << 20:
        break
    entry = {"path": path, "sha256": digest, "size": len(data)}
    if len(data) <= 64 << 10:
        entry["text"] = data.decode("utf-8", "replace")
    files.append(entry)
    total += len(data)
print(json.dumps(files))
