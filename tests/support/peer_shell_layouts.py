"""peer_shell's observer on the three per-agent layouts' real images, with no model call and no Inspect: the scripts
src/sandbox/peer_shell.py runs (PROVE, GRANT, REAP) and the observer's command as docker exec runs it.

    uv run python -m tests.support.peer_shell_layouts

Each layout gets one idle container of its image, a peer seated as the layout seats it, and a file only the peer may
read. Then, as the observer: it reads the file and the peer's home, cannot write the file, create a file in the peer's
directory or remove one, git works in a repository it does not own, /tmp lists or not as the layout leaves it, REAP
kills a process it left running, and a private file the peer writes later is readable after the next grant.

    machines      cbtask:python311          `user` (uid 1000) in /workspace, home /home/user at 700, umask 022
    agent_users   cbkw:agent                k7q2m (uid 1001) in /workspace/k7q2m at 700, home 700, umask 077,
                                            /tmp, /var/tmp and /dev/shm at 1733 (private_temp.sh)
    swe           cbswe-flipt-02e2:native   root in /app, the flipt clone

Exits non-zero on the first failed check.
"""
from __future__ import annotations

import json
import subprocess
import sys
import uuid

from src.sandbox import peer_shell

UID = peer_shell.OBSERVER_UID_FROM
ENV = {**peer_shell.GIT_ENV, "HOME": peer_shell.NO_HOME}

MACHINES_PREP = ("su user -c 'cd /workspace && git init -q . && printf secret > notes.txt && chmod 600 notes.txt && "
                 "git -c user.email=a@b -c user.name=a add notes.txt && "
                 "git -c user.email=a@b -c user.name=a commit -qm one && echo home > /home/user/h.txt'")
USERS_PREP = ("useradd -m -u 1001 -s /bin/bash k7q2m && chmod 700 /home/k7q2m && mkdir -p /workspace/k7q2m && "
              "chown k7q2m:k7q2m /workspace/k7q2m && chmod 700 /workspace/k7q2m && "
              "printf 'umask 077\\n' > /etc/profile.d/umask.sh && chmod 1733 /tmp /var/tmp /dev/shm && "
              "runuser -u k7q2m -- bash -lc 'cd /workspace/k7q2m && git init -q . && printf secret > notes.txt && "
              "git -c user.email=a@b -c user.name=a add notes.txt && "
              "git -c user.email=a@b -c user.name=a commit -qm one && echo home > ~/h.txt'")
SWE_PREP = "printf secret > /app/notes.txt && chmod 600 /app/notes.txt && echo home > /root/h.txt"
LAYOUTS = {
    "machines": {"image": "cbtask:python311", "user": "user", "uid": 1000, "workdir": "/workspace",
                 "home": "/home/user", "prepare": MACHINES_PREP, "file": "/workspace/notes.txt", "tmp_listable": True},
    "agent_users": {"image": "cbkw:agent", "user": "k7q2m", "uid": 1001, "workdir": "/workspace/k7q2m",
                    "home": "/home/k7q2m", "prepare": USERS_PREP, "file": "/workspace/k7q2m/notes.txt",
                    "tmp_listable": False},
    "swe": {"image": "cbswe-flipt-02e2:native", "user": None, "uid": 0, "workdir": "/app", "home": "/root",
            "prepare": SWE_PREP, "file": "/app/notes.txt", "tmp_listable": True},
}


def docker(*args: str, input: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], input=input, capture_output=True, text=True, timeout=600)


def script(name: str, script_text: str, spec: dict) -> dict:
    r = docker("exec", "-i", name, "python3", "-I", "-c", script_text, input=json.dumps(spec))
    if r.returncode:
        raise SystemExit(f"{name}: script failed: {r.stderr.strip()[-500:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def as_observer(name: str, workdir: str, command: str) -> subprocess.CompletedProcess:
    env = [x for k, v in ENV.items() for x in ("-e", f"{k}={v}")]
    return docker("exec", "-u", f"{UID}:{UID}", "-w", workdir, *env, name, "bash", "--login", "-c", command)


def check(layout: str, what: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {layout}: {what}{(' -- ' + detail) if detail and not ok else ''}")
    if not ok:
        raise SystemExit(1)


def run_layout(layout: str, spec: dict) -> None:
    name = f"peer-shell-{layout}-{uuid.uuid4().hex[:6]}"
    started = docker("run", "-d", "--rm", "--network", "none", "--name", name, "--entrypoint", "sleep", spec["image"],
                     "3600")
    if started.returncode:
        raise SystemExit(f"{layout}: {started.stderr.strip()}")
    try:
        r = docker("exec", name, "bash", "-c", spec["prepare"])
        check(layout, "the peer's seat is prepared", r.returncode == 0, r.stderr)
        tops = [spec["workdir"], spec["home"]]
        proved = script(name, peer_shell.PROVE, {"uid": UID, "owner": spec["uid"], "places": {"peer": tops}})
        check(layout, "the setup's proof holds", proved.get("proved") is True, json.dumps(proved))
        before = as_observer(name, spec["workdir"], f"cat {spec['file']}")
        check(layout, "before the grant the observer cannot read the peer's file", before.returncode != 0,
              before.stdout)
        granted = script(name, peer_shell.GRANT, {"uid": UID, "tops": tops})
        check(layout, "GRANT writes entries", granted["granted"] > 0, json.dumps(granted))
        read = as_observer(name, spec["workdir"], f"cat {spec['file']}; cat {spec['home']}/h.txt")
        check(layout, "the observer reads the peer's file and home", read.stdout.strip() == "secrethome",
              read.stdout + read.stderr)
        for what, command in (("write the peer's file", f"echo x >> {spec['file']}"),
                              ("create a file in the peer's directory", f"touch {spec['workdir']}/new"),
                              ("remove the peer's file", f"rm -f {spec['file']} && test ! -e {spec['file']}")):
            r = as_observer(name, spec["workdir"], command)
            check(layout, f"the observer cannot {what}", r.returncode != 0, r.stdout + r.stderr)
        intact = docker("exec", name, "cat", spec["file"])
        check(layout, "the peer's file is unchanged", intact.stdout == "secret", intact.stdout)
        git = as_observer(name, spec["workdir"], "git log --oneline -1 && git status --short | head -3")
        check(layout, "git log and git status work as the observer", git.returncode == 0 and git.stdout.strip() != "",
              git.stderr)
        listed = as_observer(name, spec["workdir"], "ls /tmp >/dev/null")
        check(layout, f"/tmp {'lists' if spec['tmp_listable'] else 'does not list'} as the layout leaves it",
              (listed.returncode == 0) == spec["tmp_listable"], listed.stderr)
        left = as_observer(name, spec["workdir"], "(sleep 600 &) ; echo started")
        check(layout, "the observer can leave a process running", left.stdout.strip() == "started", left.stderr)
        reaped = script(name, peer_shell.REAP, {"uid": UID})
        check(layout, "REAP kills the observer's process", reaped["killed"] >= 1 and reaped["survivors"] == 0,
              json.dumps(reaped))
        later = docker("exec", name, "bash", "-c", f"printf later > {spec['workdir']}/later.txt; "
                       f"{'chown ' + spec['user'] + ' ' + spec['workdir'] + '/later.txt; ' if spec['user'] else ''}"
                       f"chmod 600 {spec['workdir']}/later.txt")
        check(layout, "the peer writes a private file after the grant", later.returncode == 0, later.stderr)
        script(name, peer_shell.GRANT, {"uid": UID, "tops": tops})
        reread = as_observer(name, spec["workdir"], f"cat {spec['workdir']}/later.txt")
        check(layout, "the next call's grant reads it", reread.stdout == "later", reread.stdout + reread.stderr)
    finally:
        docker("rm", "-f", name)


def main() -> int:
    for layout, spec in LAYOUTS.items():
        print(layout)
        run_layout(layout, spec)
    print("peer_shell: every layout passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
