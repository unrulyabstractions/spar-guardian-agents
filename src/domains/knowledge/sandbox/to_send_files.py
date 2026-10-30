"""Run inside the sandbox as root: the regular files under <root>/to_send, listed without following a symlink
anywhere. to_send itself must be a real directory, symlinked subdirectories are not entered, and only regular files
are read (opened with O_NOFOLLOW) and hashed; every other entry is listed under `skipped` with its kind. Prints one
JSON object with the files, the skipped entries and the reference copy's sha256.

    python3 -c "$(cat to_send_files.py)" <root> <reference>
"""
import hashlib
import json
import os
import stat
import sys

root, reference = sys.argv[1], sys.argv[2]
ref = hashlib.sha256(open(reference, "rb").read()).hexdigest()
files, skipped = [], []
top = os.path.join(root, "to_send")
if not stat.S_ISDIR(os.lstat(top).st_mode):
    skipped.append({"path": top, "kind": "not a directory"})
else:
    for dirpath, dirs, names in os.walk(top, followlinks=False):
        for n in sorted(dirs):
            p = os.path.join(dirpath, n)
            if stat.S_ISLNK(os.lstat(p).st_mode):
                skipped.append({"path": p, "kind": "symlink"})
        for n in sorted(names):
            p = os.path.join(dirpath, n)
            mode = os.lstat(p).st_mode
            if not stat.S_ISREG(mode):
                skipped.append({"path": p, "kind": "symlink" if stat.S_ISLNK(mode) else "not a regular file"})
                continue
            fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                os.close(fd)
                skipped.append({"path": p, "kind": "not a regular file"})
                continue
            with os.fdopen(fd, "rb") as stream:
                data = stream.read()
            files.append({"path": p, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
print(json.dumps({"files": files, "skipped": skipped, "reference_sha256": ref}))
