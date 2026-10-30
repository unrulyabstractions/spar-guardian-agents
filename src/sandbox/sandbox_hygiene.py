"""Sandbox hygiene for the per-agent layouts: the processes an agent leaves behind, the loopback channel between
agent users, and what the sandbox holds when the run ends.

Three scripts run inside a sandbox as root, in Python 3, which every image has:

  REAP             kills every process of one Linux user, after each of the agent's tool commands (reap_after), so
                   no process of the agent outlives the command. A background process could otherwise write files or
                   hold a socket while the agent waits for its next step, after its episode ended, or while the probe
                   reads the disk.
  LOOPBACK_GUARD   refuses loopback traffic from the agent users with iptables owner rules (uids from 1001, every
                   per-agent user), except to the "ip:port" services the layout allows (openmath's article mirror,
                   creative writing's review endpoint), and proves
                   it: root listens on 127.0.0.1 and connects to itself (the listener is up), then each agent's
                   connection must be refused. A layout where an agent gets through is refused. The rule matches every
                   packet an agent's socket sends, replies included, so an agent cannot serve a peer either. Unix
                   sockets are not covered: an abstract socket reaches any user in the container, and the end-of-run
                   state records them.
  STATE            what the sandbox holds at the end: each agent's processes (then killed) and sockets, and the files
                   in the shared directories by owner.

The counts of REAP go to the sample store under REAP_STORE ({agent: {commands, killed, max_alive, survivors}}) and
the guard's report under GUARD_STORE; STATE's report goes to the domain's deliverables scorer under "sandbox"; the
export writes all three as run.json "sandbox" (src/export/_export_run_blocks.py sandbox_block). A lone agent keeps
CheatBench's layout: no guard and no reaps after its commands; its end state is still recorded.
"""
from __future__ import annotations

from inspect_ai.util import store

from src.sandbox.sandbox_probe import run_json

REAP_STORE = "sandbox_reaps"
GUARD_STORE = "sandbox_loopback"       # the guard's report, set by the domain's setup solver
GUARD_PORT = 47123
AGENT_UID_FROM = 1001           # the first per-agent user (src/sandbox/agent_users/agent_users.py FIRST_UID)
AGENT_UID_TO = 65533
STATE_DIRS = ["/tmp", "/var/tmp", "/dev/shm"]     # the temporary directories every layout's end state lists by owner

PROC_COMMON = r'''
import json, os, signal, sys, time

def status(pid):
    """(uid, state) of a process, or None."""
    uid = state = None
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("Uid:"):
                    uid = int(line.split()[1])
                elif line.startswith("State:"):
                    state = line.split()[1]
    except OSError:
        return None
    return (uid, state) if uid is not None else None

def cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()[:200]
    except OSError:
        return ""

def processes(uids):
    """{uid: [{pid, cmd}]} of the live (not zombie) processes of these uids."""
    out = {uid: [] for uid in uids}
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        st = status(pid)
        if st and st[0] in out and st[1] != "Z":
            out[st[0]].append({"pid": int(pid), "cmd": cmdline(pid)})
    return out

def kill_all(uids):
    """SIGKILL every process of these uids; the survivors after a short wait (zombies excluded)."""
    found = processes(uids)
    for procs in found.values():
        for p in procs:
            try:
                os.kill(p["pid"], signal.SIGKILL)
            except OSError:
                pass
    if any(found.values()):
        time.sleep(0.3)
    return found, processes(uids)
'''

REAP = PROC_COMMON + r'''
import pwd
uid = pwd.getpwnam(sys.argv[1]).pw_uid
found, left = kill_all([uid])
print(json.dumps({"killed": found[uid], "survivors": left[uid]}))
'''

LOOPBACK_GUARD = r'''
import json, socket, subprocess, sys, time
spec = json.load(sys.stdin)
rng = f"{spec['uid_from']}-{spec['uid_to']}"
port = spec["port"]

def rule(*args):
    return subprocess.run(list(args), capture_output=True, text=True)

for entry in spec.get("allow") or []:
    ip, _, service = entry.partition(":")
    r = rule("iptables", "-A", "OUTPUT", "-o", "lo", "-d", f"{ip}/32", "-p", "tcp", "--dport", service,
             "-m", "owner", "--uid-owner", rng, "-j", "ACCEPT")
    if r.returncode:
        sys.exit(f"iptables (allow {entry}) failed: {r.stderr.strip() or r.stdout.strip()}")
r = rule("iptables", "-A", "OUTPUT", "-o", "lo", "-m", "owner", "--uid-owner", rng, "-j", "REJECT")
if r.returncode:
    sys.exit(f"iptables failed: {r.stderr.strip() or r.stdout.strip()}")
v6 = rule("ip6tables", "-A", "OUTPUT", "-o", "lo", "-m", "owner", "--uid-owner", rng, "-j", "REJECT")

LISTENER = (
    "import socket\n"
    "s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
    f"s.bind(('127.0.0.1', {port})); s.listen(5); s.settimeout(30)\n"
    "for _ in range(2):\n"
    "    try:\n"
    "        c, _ = s.accept(); c.close(); print('accepted', flush=True)\n"
    "    except socket.timeout:\n"
    "        break\n")
CONNECT = f"import socket; s = socket.socket(); s.settimeout(5); s.connect(('127.0.0.1', {port})); print('connected')"
# The rule matches every packet an agent user's socket sends, replies included, so the listener runs as root (outside
# the rule): root connecting proves it is up, and each agent's connection must then be refused.
listener = subprocess.Popen(["python3", "-c", LISTENER], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
refusals = {}
try:
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            break
        except OSError:
            time.sleep(0.2)
    else:
        sys.exit("the loopback check's listener never came up")
    for agent in (spec["first"], spec["second"]):
        r = subprocess.run(["runuser", "-u", agent, "--", "python3", "-c", CONNECT], capture_output=True, text=True,
                           timeout=30)
        if r.returncode == 0:
            sys.exit(f"{agent} reached a loopback listener: loopback is open to the agent users")
        refusals[agent] = (r.stderr.strip().splitlines() or [""])[-1][:200]
finally:
    listener.kill()
    listener.wait()
print(json.dumps({"loopback": "refused", "uids": rng, "allow": spec.get("allow") or [], "ipv6": v6.returncode == 0,
                  "refusals": refusals}))
'''

STATE = PROC_COMMON + r'''
spec = json.load(sys.stdin)
owners = {int(uid): agent for agent, uid in spec["owners"].items()}

def owner_of(uid):
    return owners.get(uid, "root" if uid == 0 else f"uid:{uid}")

def socket_owners():
    """{socket inode: pid} from every process's descriptors."""
    out = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            for fd in os.listdir(f"/proc/{pid}/fd"):
                link = os.readlink(f"/proc/{pid}/fd/{fd}")
                if link.startswith("socket:["):
                    out[link[8:-1]] = int(pid)
        except OSError:
            continue
    return out

def hexaddr(text, v6):
    addr, _, port = text.partition(":")
    if v6:
        ip = ":".join(addr[i:i + 4].lower() for i in range(0, 32, 4))
    else:
        ip = ".".join(str(int(addr[i:i + 2], 16)) for i in (6, 4, 2, 0))
    return f"{ip}:{int(port, 16)}"

STATES = {"01": "established", "0A": "listen", "07": "closed"}
def inet(path, v6):
    out = []
    try:
        lines = open(path).read().splitlines()[1:]
    except OSError:
        return out
    for line in lines:
        parts = line.split()
        if len(parts) < 10:
            continue
        uid = int(parts[7])
        if uid in owners:
            out.append({"proto": os.path.basename(path), "local": hexaddr(parts[1], v6),
                        "remote": hexaddr(parts[2], v6), "state": STATES.get(parts[3], parts[3]),
                        "owner": owner_of(uid)})
    return out

def unix_sockets(inodes):
    out = []
    try:
        lines = open("/proc/net/unix").read().splitlines()[1:]
    except OSError:
        return out
    for line in lines:
        parts = line.split()
        if len(parts) < 7:
            continue
        pid = inodes.get(parts[6])
        st = status(pid) if pid else None
        if st and st[0] in owners:
            out.append({"path": parts[7] if len(parts) > 7 else "", "owner": owner_of(st[0]), "pid": pid})
    return out

def shared_files(dirs):
    out = {}
    for d in dirs:
        counts, agent_paths, total = {}, [], 0
        for dirpath, dirnames, filenames in os.walk(d):
            for name in filenames:
                path = os.path.join(dirpath, name)
                try:
                    uid = os.lstat(path).st_uid
                except OSError:
                    continue
                total += 1
                who = owner_of(uid)
                counts[who] = counts.get(who, 0) + 1
                if uid in owners and len(agent_paths) < 40:
                    agent_paths.append({"path": path, "owner": who})
        out[d] = {"files": total, "by_owner": counts, "agent_paths": agent_paths}
    return out

inodes = socket_owners()
sockets = {"inet": inet("/proc/net/tcp", False) + inet("/proc/net/tcp6", True)
                   + inet("/proc/net/udp", False) + inet("/proc/net/udp6", True),
           "unix": unix_sockets(inodes)}
alive = processes(list(owners))
_, survivors = kill_all(list(owners))
report = {"processes": {agent: {"alive_at_end": alive[uid], "survivors": survivors[uid]}
                        for uid, agent in owners.items()},
          "sockets": sockets, "shared_files": shared_files(spec.get("dirs") or [])}
print(json.dumps(report))
'''


async def reap(user: str, box=None) -> dict:
    """Kill every process of the Linux user `user` in the sandbox (or `box`); {killed: [{pid, cmd}], survivors:
    [...]}, or {"error": ...}: a failed reap never fails the tool call."""
    try:
        return await run_json(REAP, args=[user], box=box)
    except Exception as ex:  # noqa: BLE001
        return {"killed": [], "survivors": [], "error": f"{type(ex).__name__}: {ex}"[:300]}


def record_reap(agent: str, result: dict) -> None:
    """Add one reap to the sample store's REAP_STORE: the agent's commands, the processes killed over them, the most
    found alive after one command, and the survivors and errors seen (each at most once per agent)."""
    st = store()
    reaps = dict(st.get(REAP_STORE) or {})
    mine = dict(reaps.get(agent) or {"commands": 0, "killed": 0, "max_alive": 0, "survivors": [], "errors": []})
    killed = result.get("killed") or []
    mine["commands"] += 1
    mine["killed"] += len(killed)
    mine["max_alive"] = max(mine["max_alive"], len(killed))
    if result.get("survivors") and len(mine["survivors"]) < 10:
        mine["survivors"].append(result["survivors"][:5])
    if result.get("error") and len(mine["errors"]) < 5:
        mine["errors"].append(result["error"])
    reaps[agent] = mine
    st.set(REAP_STORE, reaps)


async def reap_after(agent: str, user: str, box=None) -> None:
    """After one of the agent's tool commands: kill what the command left running, and count it."""
    record_reap(agent, await reap(user, box=box))


async def guard_loopback(first: str, second: str, *, allow: list[str] | None = None, box=None) -> dict:
    """Install the loopback rules for the agent users and prove them on `first` and `second`; raise RuntimeError
    with the sandbox's message when the rules cannot be installed or an agent still reaches root's listener."""
    spec = {"uid_from": AGENT_UID_FROM, "uid_to": AGENT_UID_TO, "allow": list(allow or []), "first": first,
            "second": second, "port": GUARD_PORT}
    try:
        return await run_json(LOOPBACK_GUARD, stdin=spec, box=box)
    except Exception as ex:  # noqa: BLE001
        raise RuntimeError(f"loopback guard: {ex}") from None


async def sandbox_state(owners: dict[str, int], dirs: list[str], *, box=None) -> dict:
    """What the sandbox (or `box`) holds at the end of the run for these agents ({agent: uid}): their processes
    (then killed), their sockets, and the files of `dirs` by owner; an error is reported, never raised."""
    try:
        return await run_json(STATE, stdin={"owners": owners, "dirs": dirs}, box=box)
    except Exception as ex:  # noqa: BLE001
        return {"processes": {}, "sockets": {}, "shared_files": {}, "error": f"{type(ex).__name__}: {ex}"[:500]}
