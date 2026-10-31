"""Keep a writing.md as it stands at a submit, run as root.

    python3 -c "$(cat writing_snapshot.py)" <writing.md> <folder>

The file's first 8 MiB, as writing_files.py reads a deliverable, go to <folder>/<sha256> under the file's owner, in a
folder only root reads. The last line printed is the sha256 as JSON. A path that is no regular file exits non-zero: an
agent can leave anything under this name, and a FIFO would hang the read.
"""
import hashlib
import json
import os
import sys

path, folder = sys.argv[1:3]
if not os.path.isfile(path):
    raise SystemExit(f"{path} is no file")
with open(path, "rb") as f:
    data = f.read(8 << 20)
digest = hashlib.sha256(data).hexdigest()
os.makedirs(folder, mode=0o700, exist_ok=True)
copy = os.path.join(folder, digest)
with open(copy, "wb") as f:
    f.write(data)
info = os.stat(path)
os.chown(copy, info.st_uid, info.st_gid)
print(json.dumps(digest))
