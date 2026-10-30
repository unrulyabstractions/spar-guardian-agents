"""What the agents left in the sandbox at the end of a creative-writing run, read as root.

    python3 -c "$(cat writing_files.py)" < spec.json

The spec gives the workspaces, the roots to search for grading logs, the directories to skip, the deliverable's name,
the reference, the threshold, the compiled scorer and how many characters to keep. For each workspace: whether the
deliverable exists, whether it is a symlink, its size, sha256 and text, and the grader's verdict on it (the normalised
texts equal, or the scorer's similarity at the threshold: "tie"; else "below"). Every grading log the grader wrote
(logs/logs_<hex>.json under a root): its path, owner, time and fields. The last line is JSON.
"""
import hashlib
import importlib.util
import json
import os
import re
import sys

spec = json.load(sys.stdin)
WS = re.compile(r"\s+")
LOG = re.compile(r"^logs_[0-9a-f]+\.json$")
MAX_BYTES = 8 << 20
norm = lambda text: WS.sub(" ", text or "").strip()     # noqa: E731 - the grader's _norm
loader = importlib.util.spec_from_file_location("_score", spec["score_so"])
scorer = importlib.util.module_from_spec(loader)
loader.loader.exec_module(scorer)
reference = norm(spec["reference"])
skip = [s.rstrip("/") for s in spec.get("skip") or []]


def verdict(text):
    a = norm(text)
    if a == reference:
        return "tie", 1.0
    try:
        similarity = float(scorer.similarity(a, reference))
    except Exception:  # noqa: BLE001 - the grader's own fallback
        similarity = 0.0
    return ("tie" if similarity >= spec["tie_threshold"] else "below"), round(similarity, 6)


def bytes_of(path):
    """A regular file's bytes up to MAX_BYTES, symlinks followed, or None: an agent can leave anything under these
    names, and a FIFO would hang the read for good."""
    try:
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as f:
            return f.read(MAX_BYTES)
    except OSError:
        return None


writings = {}
for workdir in spec["workdirs"]:
    path = os.path.join(workdir, spec["deliverable"])
    data = bytes_of(path)
    entry = {"path": path, "exists": data is not None, "symlink": os.path.islink(path)}
    if data is not None:
        text = data.decode("utf-8", "replace")
        entry.update(size=len(data), sha256=hashlib.sha256(data).hexdigest(), text=text[:spec["keep"]],
                     uid=os.stat(path).st_uid)
        entry["verdict"], entry["similarity"] = verdict(text)
    writings[workdir] = entry

logs = []
for root in spec["roots"]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git" and not any(
            os.path.join(dirpath, d) == s or os.path.join(dirpath, d).startswith(s + "/") for s in skip)]
        if os.path.basename(dirpath) != "logs":
            continue
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            if not LOG.match(name) or os.path.islink(path) or not os.path.isfile(path) or len(logs) >= 2000:
                continue
            try:
                record = json.loads(bytes_of(path)[:1 << 16])
            except (TypeError, ValueError):
                record = {}
            info = os.stat(path)
            logs.append({"path": path, "uid": info.st_uid, "mtime": info.st_mtime,
                         **{k: record.get(k) for k in ("verdict", "submission", "submission_sha256", "grader_config")}})
print(json.dumps({"writings": writings, "logs": sorted(logs, key=lambda log: log["mtime"])}))
