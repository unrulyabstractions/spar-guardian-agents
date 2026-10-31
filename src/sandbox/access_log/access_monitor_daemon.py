"""Run in the sidecar as root: mark the honeypot's files with fanotify and log one row per open. Standard library only.

The sidecar shares the host's pid namespace and holds SYS_ADMIN and SYS_PTRACE (access_monitor.py with_monitor), so it
reaches each machine's files through /proc/<init>/root. The plan comes on stdin:

    {"log": path, "max_rows": n, "nonces": {control: nonce},
     "machines": {service: {"container": id, "pid": init's host pid}},
     "objects": [{"machine", "path", "object", "kind", "level"}]}

Each object gets a FAN_OPEN_PERM mark on its inode. A file's directory gets one too, with FAN_EVENT_ON_CHILD, so a file
replaced by `sed -i` is seen before the poll re-marks it, and a neighbour's open reads level "sibling". The kernel holds
each open until the row is written and the monitor has answered, so a process that lives for a moment is still named,
and once a command returns its rows are in the log. The monitor only records: it allows every open. If it dies, the
kernel lets every open through with no record, which the end control shows.

The rows, each a JSON object on one line with seq, t and row:
  ready                       every object was tried; pid, objects, marked
  arm_failed                  an object that was not there to mark at the start
  open                        one open: where (machine, object, kind, level, path, dev, ino) and who (pid, uid, exe,
                              cwd, cmd, argv as shell words, chain, root, and control for a read with a nonce in its
                              argv), or gone for an opener that died first
  object_armed, object_gone   the poll found the object at a new inode or back at its own, or no regular file there
  restart, machine_down       the poll found the machine's container under a new init, or nowhere
  row_failed                  an open that could not be described, let through all the same
  stopped                     after SIGTERM: rows seen, dropped

Agent-made text reaches the log only through json.dumps.
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import select
import shlex
import signal
import stat
import struct
import sys
import time

# linux/fanotify.h
FAN_CLOEXEC = 0x1
FAN_CLASS_CONTENT = 0x4
FAN_UNLIMITED_QUEUE = 0x10
FAN_UNLIMITED_MARKS = 0x20
FAN_MARK_ADD = 0x1
FAN_OPEN_PERM = 0x10000
FAN_EVENT_ON_CHILD = 0x08000000
FAN_ALLOW = 0x1
AT_FDCWD = -100
EVENT = struct.Struct("=IBBHQii")       # struct fanotify_event_metadata
RESPONSE = struct.Struct("=iI")         # struct fanotify_response
# One read takes a descriptor per event, and the kernel denies the open it has no descriptor for. These 170 events stay
# far under Docker's soft limit of 1024.
EVENT_BUFFER = 4096

POLL_S = 0.2
CHAIN_DEPTH = 8                         # the ancestors a row names
CMD_CHARS = 300
CHAIN_CHARS = 120
ROOT_CHARS = 200
DIGEST_CHARS = 16
WALKS = 8                               # the walks of an opener whose ancestors keep leaving


# --------------------------------------------------------------------------
# the log
# --------------------------------------------------------------------------
class RowLog:
    """The monitor's rows on a line-buffered text stream. Past max_rows an ordinary open is counted and dropped; a
    control's open and every other row always pass."""

    def __init__(self, stream, max_rows: int) -> None:
        self.stream, self.max_rows = stream, max_rows
        self.seen = self.dropped = 0

    def write(self, row: str, **fields) -> None:
        self.seen += 1
        if row == "open" and "control" not in fields and self.seen > self.max_rows:
            self.dropped += 1
            return
        self.stream.write(json.dumps({"seq": self.seen, "t": round(time.time(), 6), "row": row, **fields}) + "\n")


# --------------------------------------------------------------------------
# what /proc says of a process
# --------------------------------------------------------------------------
def read_bytes(path: str) -> bytes | None:
    """A /proc file, or None for a process that is gone."""
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def link(path: str) -> str | None:
    try:
        return os.readlink(path)
    except OSError:
        return None


def status(pid: int | str) -> dict | None:
    """The process's effective uid, its parent and its pid in its own namespace, or None for one that is gone. An agent
    picks its process's name, so the bytes are decoded with care."""
    raw = read_bytes(f"/proc/{pid}/status")
    if raw is None:
        return None
    fields = dict(line.split(":", 1) for line in raw.decode("utf-8", "replace").splitlines() if ":" in line)
    return {"uid": int(fields["Uid"].split()[1]), "ppid": int(fields["PPid"]),
            "ns_pid": int(fields["NSpid"].split()[-1])}


def argv(pid: int) -> list[bytes]:
    raw = read_bytes(f"/proc/{pid}/cmdline")
    return raw.removesuffix(b"\0").split(b"\0") if raw else []


def text(args: list[bytes], keep: int) -> str:
    return b" ".join(args).decode("utf-8", "replace")[:keep]


def shell_words(args: list[bytes], keep: int) -> str:
    """argv as shell words. A word of argv can hold spaces, so a process that joined them could write an option it was
    never given: `exec -a 'grep -c' grep . key` counts nothing."""
    return " ".join(shlex.quote(arg.decode("utf-8", "replace")) for arg in args)[:keep]


def exec_digest(args: list[bytes]) -> str:
    """The hash of the command an exec started: its argv after Inspect's `timeout -k 5s Ns`. Hashing a tool call's argv
    the same way ties a read to its call."""
    command = args[4:] if args[:2] == [b"timeout", b"-k"] else args
    return hashlib.sha256(b"\0".join(command)).hexdigest()[:DIGEST_CHARS]


def ancestry(pid: int, opener: dict) -> tuple[list[str], dict] | None:
    """The opener and its ancestors, nearest first and at most CHAIN_DEPTH, and the root of its exec: the last process
    in its pid namespace. That is what `docker exec` started, or the machine's init for a detached process. None when an
    ancestor left during the walk: the kernel holds the opener alone, and a process halfway up its tree is no root."""
    namespace = link(f"/proc/{pid}/ns/pid")
    if namespace is None:
        return None
    chain: list[str] = []
    root, root_status = pid, opener
    while True:
        if len(chain) < CHAIN_DEPTH:
            chain.append(text(argv(root), CHAIN_CHARS))
        parent = root_status["ppid"]
        space = link(f"/proc/{parent}/ns/pid")      # AppArmor refuses it for a process outside the sidecar's profile
        parent_status = status(parent)
        if parent and parent_status is None:
            return None
        if not parent or space != namespace:
            break
        root, root_status = parent, parent_status
    args = argv(root)
    return chain, {"pid": root, "ns_pid": root_status["ns_pid"], "cmd": text(args, ROOT_CHARS),
                   "sha256": exec_digest(args), "detached": root_status["ns_pid"] == 1}


def find_init(container: str) -> int | None:
    """The host pid of a container's init, from the container id in the cgroup of its processes."""
    for name in os.listdir("/proc"):
        if name.isdigit() and container.encode() in (read_bytes(f"/proc/{name}/cgroup") or b""):
            found = status(name)
            if found and found["ns_pid"] == 1:
                return int(name)
    return None


# --------------------------------------------------------------------------
# the monitor
# --------------------------------------------------------------------------
class Monitor:
    """One fanotify group over the plan's objects."""

    def __init__(self, plan: dict, log: RowLog, libc, group: int) -> None:
        self.log, self.libc, self.group = log, libc, group
        self.nonces = {name: nonce.encode() for name, nonce in plan["nonces"].items()}
        self.machines = {name: {**machine, "down": False} for name, machine in plan["machines"].items()}
        self.objects = [{**obj, "inode": None, "gone": False} for obj in plan["objects"]]
        self.by_inode: dict[tuple[int, int], dict] = {}
        self.by_path = {(obj["machine"], obj["path"]): obj for obj in self.objects}

    # ---------------------------------------------------------------- marks
    def mark(self, path: str, mask: int) -> None:
        if self.libc.fanotify_mark(self.group, FAN_MARK_ADD, mask, AT_FDCWD, path.encode()) < 0:
            raise OSError(ctypes.get_errno(), f"fanotify_mark {path}")

    def arm(self, obj: dict) -> str:
        """Mark the inode now at the object's path, and a file's directory: "armed", "same" for the inode already
        marked, or "gone" when no regular file stands there. A symlink counts as gone: it reads something else."""
        full = f"/proc/{self.machines[obj['machine']]['pid']}/root{obj['path']}"
        try:
            found = os.stat(full, follow_symlinks=False)
            if not stat.S_ISREG(found.st_mode):
                return "gone"
            inode = (found.st_dev, found.st_ino)
            if inode == obj["inode"]:
                return "same"
            self.mark(full, FAN_OPEN_PERM)
            if obj["kind"] == "file":
                self.mark(os.path.dirname(full), FAN_OPEN_PERM | FAN_EVENT_ON_CHILD)
        except OSError:
            return "gone"
        obj["inode"] = inode
        self.by_inode[inode] = obj
        return "armed"

    def start(self) -> None:
        """Mark every object. A `ready` row follows, marked or not: the start control tells which objects are live."""
        for obj in self.objects:
            if self.arm(obj) != "armed":
                obj["gone"] = True
                self.log.write("arm_failed", machine=obj["machine"], object=obj["object"], path=obj["path"])
        self.log.write("ready", pid=os.getpid(), objects=len(self.objects),
                       marked=sum(not obj["gone"] for obj in self.objects))

    # ---------------------------------------------------------------- the poll
    def follow(self, name: str, machine: dict) -> None:
        """Find a machine again after a restart, which gives its objects new inodes to mark, or note that it is down."""
        if machine["container"].encode() in (read_bytes(f"/proc/{machine['pid']}/cgroup") or b""):
            return
        pid = find_init(machine["container"])
        if pid is None:
            if not machine["down"]:
                machine["down"] = True
                self.log.write("machine_down", machine=name)
            return
        self.log.write("restart", machine=name, old_pid=machine["pid"], new_pid=pid)
        machine.update(pid=pid, down=False)
        for obj in self.objects:
            if obj["machine"] == name:
                obj["inode"] = None

    def poll(self) -> None:
        """Follow each machine and each object. Every change leaves a row, and those rows bound the gap in the log."""
        for name, machine in self.machines.items():
            self.follow(name, machine)
        for obj in self.objects:
            if self.machines[obj["machine"]]["down"]:
                continue
            state = self.arm(obj)
            gone = state == "gone"
            if state == "armed" or gone != obj["gone"]:
                self.log.write("object_gone" if gone else "object_armed", machine=obj["machine"], object=obj["object"],
                               path=obj["path"])
            obj["gone"] = gone

    # ---------------------------------------------------------------- one open
    def machine_of(self, pid: int) -> str | None:
        """The service an opener runs in, or None for a process outside them, such as the daemon behind `docker cp`."""
        cgroup = read_bytes(f"/proc/{pid}/cgroup") or b""
        return next((name for name, machine in self.machines.items() if machine["container"].encode() in cgroup), None)

    def opener(self, pid: int) -> dict:
        """Who opened: read from /proc while the kernel holds the process. A parent /proc no longer knows has handed its
        children on, so a walk that met one starts again."""
        for _ in range(WALKS):
            found = status(pid)
            walked = found and ancestry(pid, found)
            if walked:
                chain, root = walked
                args = argv(pid)
                control = next((name for name, nonce in self.nonces.items() if nonce in args), None)
                return {"uid": found["uid"], "exe": link(f"/proc/{pid}/exe"), "cwd": link(f"/proc/{pid}/cwd"),
                        "cmd": shell_words(args, CMD_CHARS), "chain": chain, "root": root,
                        **({"control": control} if control else {})}
        return {"gone": True}

    def where(self, pid: int, fd: int) -> dict:
        """What was opened. The inode names a marked object. A new file at an object's path has no mark yet, so its
        machine and path name it. Anything else is a neighbour in a marked directory."""
        found = os.fstat(fd)
        path = link(f"/proc/self/fd/{fd}")
        machine = self.machine_of(pid)
        obj = self.by_inode.get((found.st_dev, found.st_ino)) or self.by_path.get((machine, path))
        place = {"machine": machine, "object": None, "kind": "file", "level": "sibling"} if obj is None else \
            {key: obj[key] for key in ("machine", "object", "kind", "level")}
        return {**place, "path": path, "dev": found.st_dev, "ino": found.st_ino}

    def answer(self, pid: int, fd: int) -> None:
        """Log one open, then let it go on, whatever happened."""
        try:
            self.log.write("open", pid=pid, **self.where(pid, fd), **self.opener(pid))
        except Exception as ex:  # noqa: BLE001 - one odd process must not void the run's log
            self.log.write("row_failed", pid=pid, error=repr(ex)[:300])
        finally:
            os.write(self.group, RESPONSE.pack(fd, FAN_ALLOW))
            os.close(fd)

    def run(self, stop: list) -> None:
        """Answer opens and poll until `stop` holds something."""
        last = time.monotonic()
        while not stop:
            ready, _, _ = select.select([self.group], [], [], POLL_S)
            if ready:
                data = os.read(self.group, EVENT_BUFFER)
                offset = 0
                while offset < len(data):
                    length, _, _, _, _, fd, pid = EVENT.unpack_from(data, offset)
                    offset += length
                    self.answer(pid, fd)
            if time.monotonic() - last >= POLL_S:
                last = time.monotonic()
                self.poll()


def fanotify_group():
    """(libc, the group's descriptor): opens are read through it and answered through it."""
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    libc.fanotify_init.argtypes = [ctypes.c_uint, ctypes.c_uint]
    libc.fanotify_mark.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_uint64, ctypes.c_int, ctypes.c_char_p]
    group = libc.fanotify_init(FAN_CLOEXEC | FAN_CLASS_CONTENT | FAN_UNLIMITED_QUEUE | FAN_UNLIMITED_MARKS,
                               os.O_RDONLY | os.O_CLOEXEC | os.O_LARGEFILE)
    if group < 0:
        sys.exit(f"fanotify_init: {errno.errorcode[ctypes.get_errno()]}")
    return libc, group


def main() -> None:
    plan = json.load(sys.stdin)
    stop: list = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(True))
    libc, group = fanotify_group()
    with open(plan["log"], "a", buffering=1) as stream:
        log = RowLog(stream, plan["max_rows"])
        monitor = Monitor(plan, log, libc, group)
        monitor.start()
        monitor.run(stop)
        log.write("stopped", rows=log.seen, dropped=log.dropped)


if __name__ == "__main__":
    main()
