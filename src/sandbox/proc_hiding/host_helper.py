"""The privileged helper's script: one throwaway container per call (proc_hiding.helper_argv), privileged, in the Docker
host's pid namespace, with no network. It reads its order as JSON on stdin and prints one line of JSON.

    {"action": "profile", "template": <agent_profile.apparmor>, "name": <profile name>}
        In the host's mount namespace (pid 1's), render the template as Docker renders docker-default
        (moby/profiles apparmor v0.2.1 generate: abi/3.0, tunables/global and abstractions/base where the host has
        them, the daemon's own profile as dockerd's peer) and load it with the host's `apparmor_parser -Kr`, as
        Docker loads docker-default. Prints {name, loaded, daemon_profile}.
    {"action": "remount", "pids": {service: host pid}, "data": "hidepid=2,gid=2999"}
        In each container's mount namespace, remount its /proc with the data, keeping the mount's own flags (nosuid,
        nodev, noexec, relatime). Prints {service: the /proc line of the container's init's mountinfo after}.

`python3 -c` runs it as __main__; the unit tests import it for render. Every module is imported before the first
setns: after it, imports would read the target's files. Each namespace is opened before any setns, since a
container's /proc does not show the host's pids.
"""
import ctypes
import json
import os
import subprocess
import sys

CLONE_NEWNS = 0x00020000
MS_RDONLY, MS_NOSUID, MS_NODEV, MS_NOEXEC, MS_REMOUNT = 1, 2, 4, 8, 32
MS_NOATIME, MS_NODIRATIME, MS_RELATIME, MS_STRICTATIME = 1024, 2048, 1 << 21, 1 << 24
FLAGS = {"ro": MS_RDONLY, "nosuid": MS_NOSUID, "nodev": MS_NODEV, "noexec": MS_NOEXEC, "noatime": MS_NOATIME,
         "nodiratime": MS_NODIRATIME, "relatime": MS_RELATIME, "strictatime": MS_STRICTATIME}
PROFILES = "/sys/kernel/security/apparmor/profiles"
APPARMOR_D = "/etc/apparmor.d"
libc = ctypes.CDLL(None, use_errno=True)


def enter(fd):
    if libc.setns(fd, CLONE_NEWNS) != 0:
        sys.exit(f"setns: {os.strerror(ctypes.get_errno())}")
    os.chdir("/")


def proc_line(mountinfo):
    """The /proc mount's line of a mountinfo: its per-mount options and its superblock options."""
    for line in mountinfo.splitlines():
        fields = line.split()
        if len(fields) > 4 and fields[4] == "/proc":
            mount_opts = fields[5]
            super_opts = line.split(" - ", 1)[1].split()[2] if " - " in line else ""
            return {"mount": mount_opts, "super": super_opts}
    return None


def daemon_profile():
    """dockerd's AppArmor label, as Docker reads its own: unconfined when it has none."""
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                argv0 = f.read().split(b"\0")[0]
            if os.path.basename(argv0) == b"dockerd":
                with open(f"/proc/{pid}/attr/current") as f:
                    label = f.read().strip()
                return label.rsplit(" (", 1)[0] or "unconfined"
        except OSError:
            continue
    return "unconfined"


def render(template, name, exists):
    """The template as moby/profiles' generate fills docker-default's."""
    imports = "#include <tunables/global>" if exists("tunables/global") else "@{PROC}=/proc/"
    inner = "  #include <abstractions/base>\n" if exists("abstractions/base") else ""
    values = {"abi": "abi <abi/3.0>," if exists("abi/3.0") else "", "imports": imports, "inner_imports": inner,
              "name": name, "daemon_profile": daemon_profile()}
    text = template
    for key, value in values.items():
        text = text.replace("${" + key + "}", value)
    return text, values["daemon_profile"]


def load_profile(order):
    enter(os.open("/proc/1/ns/mnt", os.O_RDONLY))
    text, daemon = render(order["template"], order["name"], lambda m: os.path.exists(os.path.join(APPARMOR_D, m)))
    parser = next((p for p in ("/sbin/apparmor_parser", "/usr/sbin/apparmor_parser") if os.path.exists(p)), None)
    if parser is None:
        sys.exit("the Docker host has no apparmor_parser")
    loaded = subprocess.run([parser, "-Kr"], input=text.encode(), capture_output=True)
    if loaded.returncode != 0:
        sys.exit(f"apparmor_parser: {(loaded.stderr or loaded.stdout).decode().strip()[-600:]}")
    with open(PROFILES) as f:
        names = [line.rsplit(" (", 1)[0] for line in f.read().splitlines()]
    if order["name"] not in names:
        sys.exit(f"{order['name']} is not among the host's loaded profiles")
    return {"name": order["name"], "loaded": True, "daemon_profile": daemon}


def remount(order):
    fds = {service: os.open(f"/proc/{pid}/ns/mnt", os.O_RDONLY) for service, pid in order["pids"].items()}
    out = {}
    for service, fd in fds.items():
        enter(fd)
        with open("/proc/1/mountinfo") as f:
            before = proc_line(f.read())
        if before is None:
            sys.exit(f"{service}: no /proc mount")
        flags = MS_REMOUNT
        for opt in before["mount"].split(","):
            flags |= FLAGS.get(opt, 0)
        if libc.mount(b"proc", b"/proc", b"proc", flags, order["data"].encode()) != 0:
            sys.exit(f"{service}: remount /proc: {os.strerror(ctypes.get_errno())}")
        with open("/proc/1/mountinfo") as f:
            out[service] = proc_line(f.read())
    return out


if __name__ == "__main__":
    order = json.load(sys.stdin)
    action = {"profile": load_profile, "remount": remount}[order["action"]]
    print(json.dumps(action(order)))
