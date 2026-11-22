"""In a machine, as root (`--user root`), after the remount: the proof that /proc hides what it should. stdin: {"gid",
"uids": [holder, viewer]}, two uids no agent has. Prints the report; proc_hiding.proof_failures reads it.

  profile         this exec's AppArmor label, which Docker gives every process of the container; kernel the release
  proc            the /proc mount's superblock options: hidepid and gid
  holder          a process of the first uid, alive while the viewer looks
  viewer          a process of the second uid: the pids it lists, of whom, whether it sees the holder, root's init
                  or this script, whether it can open the holder's cmdline and environ, whether its own /proc/self,
                  /proc/self/fd and /dev/fd (bash's <(...)) work, and how opening its own page map fails
  setgid          the viewer's uid running a setgid-gid copy of python3 in /tmp (what a peer_shell observer can
                  leave there): its egid, the uids it sees besides its own, and its NoNewPrivs
  root            whether this script, in the group, sees the holder, and how its own page map fails

The holder and the viewer each exec a fresh python3 after taking their uid, as an agent's command does (the command
jobs' launcher): a process that changed its uid without an exec is not dumpable, so its own /proc files belong to
root and its page map would fail with EACCES under any profile.
"""
import errno
import json
import os
import shutil
import subprocess
import sys

HOLD = "import sys; sys.stdin.read()"
VIEW = r'''
import errno, json, os, sys
holder, parent = sys.argv[1], sys.argv[2]

def failure(call):
    try:
        call()
        return "ok"
    except OSError as e:
        return errno.errorcode.get(e.errno, str(e.errno))

def read8(path):
    with open(path, "rb") as f:
        f.read(8)

def dev_fd():
    r, w = os.pipe()
    os.write(w, b"ok")
    os.close(w)
    with open(f"/dev/fd/{r}", "rb") as f:
        if f.read() != b"ok":
            raise OSError(errno.EIO, "the pipe read back wrong")

pids = [p for p in os.listdir("/proc") if p.isdigit()]
owners = {}
for p in pids:
    try:
        owners[p] = os.stat(f"/proc/{p}").st_uid
    except OSError:
        pass
print(json.dumps({"pids": len(pids), "other_owners": sorted({u for u in owners.values() if u != os.getuid()}),
                  "sees_holder": holder in pids, "sees_init": "1" in pids, "sees_parent": parent in pids,
                  "holder_cmdline": failure(lambda: read8(f"/proc/{holder}/cmdline")),
                  "holder_environ": failure(lambda: read8(f"/proc/{holder}/environ")),
                  "self": failure(lambda: read8("/proc/self/status")),
                  "self_fd": failure(lambda: os.listdir("/proc/self/fd")),
                  "dev_fd": failure(dev_fd),
                  "pagemap": failure(lambda: read8("/proc/self/pagemap")),
                  "task_pagemap": failure(lambda: read8(f"/proc/self/task/{os.getpid()}/pagemap"))}))
'''
SETGID_VIEW = r'''
import json, os
owners = set()
for p in filter(str.isdigit, os.listdir("/proc")):
    try:
        owners.add(os.stat(f"/proc/{p}").st_uid)
    except OSError:
        pass
nnp = next((line.split()[1] for line in open("/proc/self/status") if line.startswith("NoNewPrivs:")), None)
print(json.dumps({"egid": os.getegid(), "other_owners": sorted(owners - {os.getuid()}), "no_new_privs": nnp}))
'''


def failure(call):
    """'ok' when call() works, else the errno's name."""
    try:
        call()
        return "ok"
    except OSError as e:
        return errno.errorcode.get(e.errno, str(e.errno))


def read8(path):
    with open(path, "rb") as f:
        f.read(8)


def as_uid(uid):
    def become():
        os.setgroups([])
        os.setgid(uid)
        os.setuid(uid)
    return become


def mount_options():
    with open("/proc/self/mountinfo") as f:
        for line in f:
            fields = line.split()
            if len(fields) > 4 and fields[4] == "/proc" and " - " in line:
                return line.split(" - ", 1)[1].split()[2]
    return ""


spec = json.load(sys.stdin)
holder_uid, viewer_uid = spec["uids"]
report = {"profile": open("/proc/self/attr/current").read().strip().rsplit(" (", 1)[0], "proc": mount_options(),
          "groups": os.getgroups(), "kernel": os.uname().release}
holder = subprocess.Popen([sys.executable, "-I", "-c", HOLD], stdin=subprocess.PIPE, cwd="/",
                          preexec_fn=as_uid(holder_uid))
try:
    viewed = subprocess.run([sys.executable, "-I", "-c", VIEW, str(holder.pid), str(os.getpid())], cwd="/",
                            capture_output=True, text=True, timeout=60, preexec_fn=as_uid(viewer_uid))
    report["viewer"] = json.loads(viewed.stdout.strip().splitlines()[-1]) if viewed.returncode == 0 \
        else {"error": viewed.stderr.strip()[-300:]}
    planted = f"/tmp/.proc_proof_setgid_{os.getpid()}"
    try:
        shutil.copy2(os.path.realpath(sys.executable), planted)
        os.chown(planted, holder_uid, spec["gid"])
        os.chmod(planted, 0o2755)
        escalated = subprocess.run([planted, "-I", "-c", SETGID_VIEW], cwd="/", capture_output=True, text=True,
                                   timeout=60, preexec_fn=as_uid(viewer_uid))
        report["setgid"] = json.loads(escalated.stdout.strip().splitlines()[-1]) if escalated.returncode == 0 \
            else {"error": escalated.stderr.strip()[-300:]}
    finally:
        if os.path.exists(planted):
            os.unlink(planted)
    report["root"] = {"sees_holder": os.path.isdir(f"/proc/{holder.pid}"),
                      "holder_cmdline": failure(lambda: read8(f"/proc/{holder.pid}/cmdline")),
                      "pagemap": failure(lambda: read8("/proc/self/pagemap"))}
finally:
    holder.communicate(b"", timeout=30)
print(json.dumps(report))
