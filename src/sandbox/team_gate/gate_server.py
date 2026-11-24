"""The gate of a team remote, run as root in the gate service: python3 -I -c <this file> <config JSON>.

A machine kept out of the remote's main mounts the remote's volume read-only, and its git reaches the remote through a
Unix socket on a volume that it shares with the gate alone. The gate runs `git receive-pack` on the remote for each
connection, with the agent's git identity, as the remote's own user, and with hooks of its own: the update hook
refuses every protected ref and lets every other ref through. A push from that machine therefore lands as any push
does, but never on a protected ref, and the machine itself can change nothing in the remote.

The config: {"remote", "user", "agents": {agent: socket path}, "protected": [refs], "message", "email"}. Each refused
update is a line of REFUSED and each connection a line of CONNECTIONS, both JSON, in the gate service alone.
"""
import json
import os
import pwd
import socket
import subprocess
import sys
import threading
import time

HOOKS = "/opt/team-gate/hooks"
HOME = "/opt/team-gate/home"
LOGS = "/var/log/team-gate"
REFUSED = f"{LOGS}/refused.jsonl"
CONNECTIONS = f"{LOGS}/connections.jsonl"
# The update hook: <ref> <old> <new> as its arguments. It runs as the remote's user, so the log folder is open to all.
UPDATE_HOOK = '''#!{python} -I
import json, os, sys, time
ref, old, new = sys.argv[1:4]
if ref in {protected!r}:
    print({message!r}.format(ref=ref), file=sys.stderr)
    with open({refused!r}, "a") as out:
        out.write(json.dumps({{"agent": os.environ.get("TEAM_GATE_AGENT"), "ref": ref, "old": old, "new": new,
                              "time": time.time()}}) + "\\n")
    sys.exit(1)
'''


def append(path: str, record: dict) -> None:
    with open(path, "a") as out:
        out.write(json.dumps(record) + "\n")


def pump(read, write, done) -> threading.Thread:
    """Copy from read to write until either side ends, then call done."""
    def run() -> None:
        try:
            while chunk := read(65536):
                write(chunk)
        except OSError:
            pass
        finally:
            try:
                done()
            except OSError:
                pass
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def serve(agent: str, conn: socket.socket, config: dict) -> None:
    """One push of the agent: receive-pack on the remote, with the socket as its standard input and output."""
    owner = pwd.getpwnam(config["user"])
    who = {"GIT_COMMITTER_NAME": agent, "GIT_COMMITTER_EMAIL": config["email"].format(agent=agent)}
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": HOME, "TEAM_GATE_AGENT": agent,
           "GIT_CONFIG_NOSYSTEM": "1", **who, **{k.replace("COMMITTER", "AUTHOR"): v for k, v in who.items()}}
    command = ["git", "-c", f"core.hooksPath={HOOKS}", "-c", "safe.directory=*", "receive-pack", config["remote"]]
    started = time.time()
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                            cwd="/", user=owner.pw_uid, group=owner.pw_gid, extra_groups=[])

    def to_git(chunk: bytes) -> None:
        proc.stdin.write(chunk)
        proc.stdin.flush()

    pump(conn.recv, to_git, proc.stdin.close)
    back = pump(lambda n: os.read(proc.stdout.fileno(), n), conn.sendall, lambda: conn.shutdown(socket.SHUT_WR))
    errors = proc.stderr.read()
    rc = proc.wait()
    back.join(timeout=60)
    conn.close()
    append(CONNECTIONS, {"agent": agent, "start": started, "end": time.time(), "rc": rc,
                         "stderr": errors.decode(errors="replace")[-500:]})


def listen(agent: str, path: str, config: dict) -> None:
    """Accept the agent's connections on its socket for as long as the gate runs."""
    if os.path.exists(path):
        os.unlink(path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    os.chmod(path, 0o666)
    server.listen(16)
    while True:
        conn, _ = server.accept()
        threading.Thread(target=serve, args=(agent, conn, config), daemon=True).start()


def main(config: dict) -> None:
    os.makedirs(HOOKS, exist_ok=True)
    os.makedirs(HOME, exist_ok=True)
    os.makedirs(LOGS, exist_ok=True)
    os.chmod(LOGS, 0o777)
    for name in (REFUSED, CONNECTIONS):
        open(name, "a").close()
        os.chmod(name, 0o666)
    hook = f"{HOOKS}/update"
    with open(hook, "w") as out:
        out.write(UPDATE_HOOK.format(python=sys.executable, protected=list(config["protected"]),
                                     message=config["message"], refused=REFUSED))
    os.chmod(hook, 0o755)
    threads = [threading.Thread(target=listen, args=(agent, path, config), daemon=True)
               for agent, path in config["agents"].items()]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
