"""The listing of a machine's temporary directories that a /tmp snapshot is made of, run as root in the machine with
`python3 -I -c LISTING` and its spec on stdin. The replay's snapshots (src/replay/tmp_snapshots.py) and the run's own
(src/sandbox/live_tmp_capture.py) take it alike, so their files share one entry format (tmp_snapshots.py's module
docstring). Pure: the scripts are text, run elsewhere.

    spec = listing_spec(no_read=["/tmp/grading/ref/1a2b/c_9.md"], no_read_dirs=["/tmp/grading/ref/1a2b"], lines=True,
                        max_entries=20000)
    found, truncated = parse_lines(stdout)

A file named in no_read, or in a directory of no_read_dirs, is listed by lstat and its ACL, its content never opened:
the access log marks a honeypot file and its directory (access_monitor_daemon.py), and the harness's open of either
would be logged as a harness read of the honeypot or of a sibling. Listing a directory opens nothing it marks: its
marks carry no FAN_ONDIR.
"""
from __future__ import annotations

import json

TMP_DIRS = ["/tmp", "/var/tmp", "/dev/shm"]
LIVE_DIRS = [*TMP_DIRS, "/team", "/var/backups"]
"""What a run's own capture lists on each machine (src/sandbox/live_tmp_capture.py): the temporary directories, the
folder a team shares (/team, SWE's git remote, the same on every machine) and /var/backups (ian_swe0's patch files).
A folder a machine does not have is left out. The replay of an older run lists TMP_DIRS alone."""
HASH_MAX_BYTES = 1 << 20
TEXT_MAX_BYTES = 8 << 10
USER_MIN_UID, NOBODY_UID = 1000, 65534
LISTING_LIB = r"""
import grp, hashlib, json, os, pwd, stat, struct, sys
kinds = ((stat.S_ISREG, "file"), (stat.S_ISDIR, "dir"), (stat.S_ISLNK, "link"), (stat.S_ISFIFO, "fifo"),
         (stat.S_ISSOCK, "socket"))
ACL_XATTRS = (("acl", "system.posix_acl_access"), ("default_acl", "system.posix_acl_default"))
ACL_TAGS = {0x01: "user", 0x02: "user", 0x04: "group", 0x08: "group", 0x10: "mask", 0x20: "other"}
names = {}
def name_of(table, ident):
    key = (table, ident)
    if key not in names:
        try:
            names[key] = pwd.getpwuid(ident).pw_name if table == "user" else grp.getgrgid(ident).gr_name
        except KeyError:
            names[key] = str(ident)
    return names[key]
def acl_lines(raw):
    # linux/posix_acl_xattr.h: a u32 version, then one (u16 tag, u16 perm, u32 id) entry per tag, little-endian
    out = []
    for off in range(4, len(raw) - 7, 8):
        tag, perm, ident = struct.unpack_from("<HHI", raw, off)
        who = name_of("user", ident) if tag == 0x02 else name_of("group", ident) if tag == 0x08 else ""
        bits = "".join(c if perm & b else "-" for c, b in (("r", 4), ("w", 2), ("x", 1)))
        out.append(f"{ACL_TAGS.get(tag, str(tag))}:{who}:{bits}")
    return out
def acls(path):
    out = {}
    if not hasattr(os, "getxattr"):
        return out
    for key, attr in ACL_XATTRS:
        try:
            out[key] = acl_lines(os.getxattr(path, attr, follow_symlinks=False))
        except OSError:
            pass
    return out
def entry(path, spec):
    info = os.lstat(path)
    kind = next((name for test, name in kinds if test(info.st_mode)), "other")
    out = {"type": kind, "size": info.st_size, "mode": stat.filemode(info.st_mode),
           "owner": name_of("user", info.st_uid), "uid": info.st_uid, "group": name_of("group", info.st_gid),
           "gid": info.st_gid, "mtime": info.st_mtime}
    if kind == "link":
        out["target"] = os.readlink(path)
        return out
    out.update(acls(path))
    if kind == "file" and (path in spec.get("no_read", ()) or os.path.dirname(path) in spec.get("no_read_dirs", ())):
        out["unread"] = True
        return out
    if kind == "file" and info.st_size <= spec["hash_max"]:
        try:
            with open(path, "rb") as f:
                data = f.read(spec["hash_max"] + 1)
        except OSError:
            return out
        out["sha256"] = hashlib.sha256(data).hexdigest()
        if len(data) <= spec["text_max"] and b"\0" not in data:
            try:
                out["text"] = data.decode("utf-8")
            except UnicodeDecodeError:
                pass
    return out
"""
"""The listing's functions, with no top-level effect: tests exec them to decode an ACL off the box."""
LISTING = LISTING_LIB + r"""
spec = json.load(sys.stdin)
cap = spec.get("max_entries")
found, truncated = {}, False
for root in spec["roots"]:
    if not os.path.lexists(root):
        continue
    found[root] = entry(root, spec)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(dirnames) + sorted(filenames):
            if cap is not None and len(found) >= cap:
                truncated = True
                break
            path = os.path.join(dirpath, name)
            try:
                found[path] = entry(path, spec)
            except OSError:
                continue
if spec.get("lines"):
    for path, item in found.items():
        print(json.dumps([path, item]))
    print(json.dumps({"entries": len(found), "truncated": truncated}))
else:
    print(json.dumps(found))
"""
"""Every entry under the roots as root, by path (the module docstring's entry); the roots themselves included. With
spec "lines", one [path, entry] line per entry and a last line {"entries", "truncated"}; with "max_entries", the walk
stops at that many entries and says truncated."""
USERS = r"""
import grp, json, os, pwd, sys
spec = json.load(sys.stdin)
groups = {g.gr_gid: g.gr_name for g in grp.getgrall()}
found = {}
for u in sorted(pwd.getpwall(), key=lambda u: u.pw_uid):
    if spec["min_uid"] <= u.pw_uid < spec["nobody_uid"]:
        found[u.pw_name] = {"uid": u.pw_uid, "gid": u.pw_gid, "groups": sorted(
            {groups.get(g, str(g)) for g in os.getgrouplist(u.pw_name, u.pw_gid)})}
print(json.dumps(found))
"""
"""The machine's login users (uid from min_uid, below nobody's) with their groups by name, primary included."""


def listing_spec(roots: list[str] | None = None, *, no_read: list[str] | None = None,
                 no_read_dirs: list[str] | None = None, lines: bool = False, max_entries: int | None = None) -> dict:
    """LISTING's stdin: the roots (TMP_DIRS by default) and the caps; no_read the files and no_read_dirs the directories
    whose files' content is never opened (an entry of one says "unread"), lines and max_entries as LISTING says."""
    spec = {"roots": list(roots or TMP_DIRS), "hash_max": HASH_MAX_BYTES, "text_max": TEXT_MAX_BYTES}
    if no_read:
        spec["no_read"] = sorted(no_read)
    if no_read_dirs:
        spec["no_read_dirs"] = sorted(no_read_dirs)
    if lines:
        spec["lines"] = True
    if max_entries is not None:
        spec["max_entries"] = max_entries
    return spec


def parse_lines(stdout: str) -> tuple[dict[str, dict], bool]:
    """LISTING's output under spec "lines": ({path: entry}, truncated). A listing whose last line is not its count is a
    RuntimeError."""
    rows = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    if not rows or not isinstance(rows[-1], dict) or rows[-1].get("entries") != len(rows) - 1:
        raise RuntimeError("the /tmp listing ended before its count line")
    return {path: item for path, item in rows[:-1]}, bool(rows[-1].get("truncated"))
