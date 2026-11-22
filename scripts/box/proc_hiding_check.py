"""Whether this Docker host can hide /proc as a run does (src/sandbox/proc_hiding/).

    .venv/bin/python -m scripts.box.proc_hiding_check [--image cbtask:python311] [--json FILE]

Run it on a box as root after box_kernel.sh (box_setup_remote.sh runs it after the build), or on the Mac against Colima.
It rents nothing and calls no model. In order, with the run's own code: the profile loads into the host's kernel
(ensure_profile); a container of IMAGE starts under it with no network and an init, as an agent's machine; root joins
the group, the helper remounts /proc, and the run's proof passes (proof_failures); then, as uid 1001 in that container,
`grep -r` over / finds a planted file within SEARCH_S and the page map's open fails, while bash's <(...), python3 and
top work and ps lists only the agent's own processes (top and ps when the image has them). The container is removed at
the end. The last line is the report as JSON; exit 0 when every check passes, 1 when one fails, 2 when the check cannot
start.
"""
from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import time

from src.sandbox.proc_hiding import proc_hiding as ph

SEARCH_S = 120
AGENT_UID = 1001
SEARCH = ("s=$(date +%s); timeout {limit} grep -r -l {needle} / 2>/dev/null | head -5; "
          "echo \"rc=${{PIPESTATUS[0]}} secs=$(( $(date +%s) - s ))\"")
OWN = ("head -c 8 /proc/self/pagemap > /dev/null 2>&1 && echo pagemap=readable || echo pagemap=denied; "
       "cat <(echo procsub=ok); python3 -c 'import os; print(\"python=ok\", os.getpid() > 0)'; "
       "if command -v top > /dev/null; then top -b -n 1 > /dev/null && echo top=ok || echo top=failed; "
       "else echo top=absent; fi; "
       "command -v ps > /dev/null && ps -eo uid= | sort -u | tr '\\n' ' ' || echo ps=absent")


def docker(*args: str, stdin: str | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], input=stdin, capture_output=True, text=True, timeout=timeout)


def in_container(name: str, script: str, spec: dict, user: str = "root") -> dict:
    found = docker("exec", "-i", "-u", user, name, "python3", "-I", "-c", script, stdin=json.dumps(spec))
    if found.returncode != 0:
        raise RuntimeError(found.stderr.strip()[-400:])
    return json.loads(found.stdout.strip().splitlines()[-1])


def check(image: str) -> dict:
    report: dict = {"image": image}
    profile = ph.ensure_profile()
    report["profile"] = profile
    name = f"proc-hiding-check-{secrets.token_hex(4)}"
    started = docker("run", "-d", "--rm", "--name", name, "--label", "proc_hiding_check=1", "--init", "--network",
                     "none", "--memory", "512m", "--security-opt", f"apparmor={profile}", image, "tail", "-f",
                     "/dev/null")
    if started.returncode != 0:
        raise RuntimeError(f"docker run: {started.stderr.strip()[-400:]}")
    try:
        report["group"] = in_container(name, ph.PROC_GROUP_SCRIPT, {"name": ph.PROC_GROUP, "gid": ph.PROC_GID})
        pid = int(docker("inspect", "-f", "{{.State.Pid}}", name).stdout)
        remounted = docker(*ph.helper_argv()[1:], stdin=json.dumps({"action": "remount", "pids": {"check": pid},
                                                                    "data": ph.REMOUNT_DATA}))
        if remounted.returncode != 0:
            raise RuntimeError(f"remount: {remounted.stderr.strip()[-400:]}")
        report["remount"] = json.loads(remounted.stdout.strip().splitlines()[-1])
        proof = in_container(name, ph.PROOF, {"gid": ph.PROC_GID, "uids": ph.PROOF_UIDS})
        report["proof"], report["failures"] = proof, ph.proof_failures(proof)
        needle = f"PROC_CHECK_{secrets.token_hex(6)}"
        docker("exec", "-u", "root", name, "bash", "-c",
               f"useradd -m -u {AGENT_UID} procagent 2>/dev/null; mkdir -p /opt/proc_check; "
               f"echo {needle} > /opt/proc_check/planted.txt; chmod 644 /opt/proc_check/planted.txt")
        start = time.monotonic()
        found = docker("exec", "-u", str(AGENT_UID), name, "bash", "-c",
                       SEARCH.format(limit=SEARCH_S, needle=needle), timeout=SEARCH_S + 60)
        report["search"] = {"output": found.stdout.strip()[-600:], "seconds": round(time.monotonic() - start, 1),
                            "found": "/opt/proc_check/planted.txt" in found.stdout}
        own = docker("exec", "-u", str(AGENT_UID), name, "bash", "-c", OWN)
        report["own"] = own.stdout.strip()
    finally:
        docker("rm", "-f", name)
    problems = [m for found in report["failures"].values() for m in found]
    if not report["search"]["found"] or report["search"]["seconds"] >= SEARCH_S:
        problems.append(f"grep -r / did not find the planted file within {SEARCH_S} s")
    if not all(mark in report["own"] for mark in ("pagemap=denied", "procsub=ok", "python=ok True")) \
            or "top=failed" in report["own"]:
        problems.append(f"the agent's own view: {report['own']!r}")
    if "ps=absent" not in report["own"] and set(report["own"].splitlines()[-1].split()) != {str(AGENT_UID)}:
        problems.append(f"ps as the agent lists other uids: {report['own']!r}")
    report["problems"] = problems
    report["ok"] = not problems
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--image", default=ph.HELPER_IMAGE)
    parser.add_argument("--json", dest="json_out")
    args = parser.parse_args(argv)
    try:
        report = check(args.image)
    except Exception as ex:  # noqa: BLE001 - the check could not start: say why and exit 2
        print(json.dumps({"ok": False, "error": f"{type(ex).__name__}: {ex}"[:1000]}))
        return 2
    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(report, f, indent=1)
    for problem in report["problems"]:
        print(f"!! {problem}", file=sys.stderr)
    print(json.dumps(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
