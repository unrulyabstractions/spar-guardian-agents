"""The replay's sandbox: one compose project of the run's services, without network, and commands in its containers
as Inspect ran the agents' tools.

    with ReplaySandbox("replay-flipt-minimal-luna-fixed1", compose_path) as box:
        result = box.exec("k7q2m", "git status")                         # Exec(rc, stdout, stderr, seconds)
        output = box.tool("k7q2m", ["bash", "--login", "-c", cmd], 600)   # Inspect's tool exec, timeout included

The project is torn down with its volumes when the block ends, whatever happened. The launch refuses while the host's
free memory is under MEMORY_FREE_MIN (macOS memory_pressure), and a project name without the replay's prefix.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import NamedTuple

PREFIX = "replay-"
MEMORY_FREE_MIN = 40
"""The least share of free memory, in percent, at which a replay starts its containers."""
TIMEOUT_RCS = (124, 137, 143)
"""The exit codes Inspect reads as its timeout having fired, once the call ran as long as its timeout."""
WORKDIR_FLAG = "--workdir"


class Exec(NamedTuple):
    rc: int
    stdout: bytes
    stderr: bytes
    seconds: float


def memory_free_percent() -> int | None:
    """macOS's system-wide free memory percentage, or None where memory_pressure does not exist."""
    if platform.system() != "Darwin" or not shutil.which("memory_pressure"):
        return None
    out = subprocess.run(["memory_pressure"], capture_output=True, text=True, timeout=60).stdout
    found = re.search(r"System-wide memory free percentage:\s*(\d+)%", out)
    return int(found.group(1)) if found else None


def project_name(run: str) -> str:
    """A compose project name for the run: PREFIX, the run's name in the characters compose allows, and this
    process's id, so a replay never names another session's project."""
    return f"{PREFIX}{re.sub(r'[^a-z0-9_-]+', '-', run.lower()).strip('-')[:60]}-{os.getpid()}"


class ReplaySandbox:
    def __init__(self, project: str, compose: Path) -> None:
        if not project.startswith(PREFIX):
            raise ValueError(f"a replay's compose project starts with {PREFIX}: {project}")
        self.project, self.compose = project, Path(compose)

    def container(self, service: str) -> str:
        return f"{self.project}-{service}-1"

    def compose_cmd(self, *args: str) -> list[str]:
        return ["docker", "compose", "-p", self.project, "-f", str(self.compose), *args]

    def __enter__(self) -> ReplaySandbox:
        free = memory_free_percent()
        if free is not None and free < MEMORY_FREE_MIN:
            raise RuntimeError(f"free memory is {free}%, under the {MEMORY_FREE_MIN}% a replay needs; not starting")
        started = subprocess.run(self.compose_cmd("up", "-d", "--quiet-pull"), capture_output=True, text=True)
        if started.returncode != 0:
            self.down()
            raise RuntimeError(f"compose up of {self.project} failed: {started.stderr[-1500:]}")
        return self

    def __exit__(self, *exc) -> None:
        self.down()

    def down(self) -> None:
        subprocess.run(self.compose_cmd("down", "-v", "-t", "1", "--remove-orphans"), capture_output=True)

    def exec(self, service: str, command: str, *, stdin: bytes | None = None, timeout: int = 1800) -> Exec:
        """`bash -c command` in the service's container."""
        return self.run(service, ["bash", "-c", command], stdin=stdin, timeout=timeout)

    def run(self, service: str, argv: list[str], *, stdin: bytes | None = None, timeout: int = 1800,
            workdir: str | None = None) -> Exec:
        argv = ["docker", "exec", *(["-i"] if stdin is not None else []),
                *([WORKDIR_FLAG, workdir] if workdir else []), self.container(service), *argv]
        start = time.time()
        try:
            done = subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout,
                                  stdin=None if stdin is not None else subprocess.DEVNULL)
            return Exec(done.returncode, done.stdout, done.stderr, time.time() - start)
        except subprocess.TimeoutExpired as expired:
            return Exec(-9, expired.stdout or b"", expired.stderr or b"", time.time() - start)

    def must(self, service: str, command: str, what: str, *, stdin: bytes | None = None) -> str:
        """exec's stdout as text, or RuntimeError naming `what`."""
        done = self.exec(service, command, stdin=stdin)
        if done.rc != 0:
            raise RuntimeError(f"{what} failed in {service} (rc={done.rc}): "
                               f"{(done.stderr or done.stdout).decode(errors='replace')[-1500:]}")
        return done.stdout.decode(errors="replace")

    def tool(self, service: str, argv: list[str], timeout: int, workdir: str, stdin: bytes | None = None,
             ) -> tuple[str, bool]:
        """A tool's exec as Inspect's docker sandbox ran it: `/usr/bin/timeout -k 5s <timeout>s argv` in the working
        directory, the output stderr, a newline, then stdout (agent_tools.run). Returns (output, timed out), timed out
        as Inspect reads it: a timeout's exit code after the whole timeout."""
        done = self.run(service, ["/usr/bin/timeout", "-k", "5s", f"{timeout}s", *argv], stdin=stdin,
                        timeout=timeout + 60, workdir=workdir)
        stdout, stderr = done.stdout.decode(errors="replace"), done.stderr.decode(errors="replace")
        timed_out = done.rc == -9 or (done.rc in TIMEOUT_RCS and done.seconds >= timeout)
        return (f"{stderr}\n" if stderr else "") + stdout, timed_out
