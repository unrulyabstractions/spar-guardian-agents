"""A sandbox environment that runs every exec on this machine, so the command jobs' scripts run for real in a unit test.

    box = LocalBox()
    monkeypatch.setattr(command_jobs, "JOBS_DIR", str(tmp_path / "jobs"))
    result = await box.exec(["python3", "-c", "print(1)"])

Each exec starts a session of its own, as docker makes every exec a session leader, and runs as the current user: a
`user` is recorded, never taken. The macOS host has no /proc, so a job is stopped through its process group alone
(command_jobs.STOP kills the group first). Every exec is recorded in `calls` as (argv, user, cwd).
"""
from __future__ import annotations

import asyncio
import os
from asyncio.subprocess import DEVNULL, PIPE

from inspect_ai.util import ExecResult


class LocalBox:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None, str | None]] = []

    async def exec(self, cmd, input=None, cwd=None, env=None, user=None, timeout=None, concurrency=True, **_):
        self.calls.append((list(cmd), user, cwd))
        proc = await asyncio.create_subprocess_exec(*cmd, stdin=PIPE if input is not None else DEVNULL, stdout=PIPE,
                                                    stderr=PIPE, cwd=cwd, env={**os.environ, **(env or {})},
                                                    start_new_session=True)
        data = input.encode() if isinstance(input, str) else input
        out, err = await asyncio.wait_for(proc.communicate(data), timeout)
        return ExecResult(success=proc.returncode == 0, returncode=proc.returncode,
                          stdout=out.decode(errors="replace"), stderr=err.decode(errors="replace"))
