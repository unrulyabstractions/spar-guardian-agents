"""Run inside the native image, whose /app is the base commit: which of the wanted lines (a JSON list on stdin) the
base tree already holds, whitespace collapsed as Rigel compares lines. Prints one JSON list."""
import json
import os
import re
import sys

want = set(json.load(sys.stdin))
hits = set()
for root, dirs, files in os.walk("/app"):
    dirs[:] = [d for d in dirs if d != ".git"]
    for f in files:
        try:
            with open(os.path.join(root, f), encoding="utf-8") as fh:
                for line in fh:
                    n = re.sub(r"\s+", " ", line).strip()
                    if n in want:
                        hits.add(n)
        except (UnicodeDecodeError, OSError):
            pass
print(json.dumps(sorted(hits)))
