"""In a machine, as root, before /proc is remounted: the group whose members see every process (/proc's gid=), with
root as its member, so every exec of ours as root (`--user root`, which Docker gives the groups of /etc/group) still
sees the agents' processes. stdin: {"name", "gid"}. Refuses a gid or a name that another group already holds.
Prints {"group": the line}."""
import json
import os
import sys

spec = json.load(sys.stdin)
name, gid = spec["name"], int(spec["gid"])
path = "/etc/group"
with open(path) as f:
    lines = f.read().splitlines()
out, line = [], None
for entry in lines:
    fields = entry.split(":")
    if len(fields) < 4:
        out.append(entry)
        continue
    if fields[0] == name or fields[2] == str(gid):
        if fields[0] != name or fields[2] != str(gid):
            sys.exit(f"/etc/group already holds {entry!r}, which clashes with {name}:{gid}")
        members = [m for m in fields[3].split(",") if m]
        fields[3] = ",".join(members + ([] if "root" in members else ["root"]))
        entry = line = ":".join(fields)
    out.append(entry)
if line is None:
    line = f"{name}:x:{gid}:root"
    out.append(line)
st = os.stat(path)
tmp = f"{path}.proc_hiding"
with open(tmp, "w") as f:
    f.write("\n".join(out) + "\n")
os.chmod(tmp, st.st_mode & 0o7777)
os.chown(tmp, st.st_uid, st.st_gid)
os.replace(tmp, path)
print(json.dumps({"group": line}))
