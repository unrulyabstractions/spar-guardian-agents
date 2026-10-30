"""Run inside a container as root: the sha256 of each path read from stdin (a JSON list), None for anything but a
regular file (a symlink or a FIFO is never opened) and for a file that cannot be read. Prints one JSON object."""
import hashlib
import json
import os
import stat
import sys

out = {}
for p in json.load(sys.stdin):
    try:
        regular = stat.S_ISREG(os.lstat(p).st_mode)
        out[p] = hashlib.sha256(open(p, "rb").read()).hexdigest() if regular else None
    except OSError:
        out[p] = None
print(json.dumps(out))
