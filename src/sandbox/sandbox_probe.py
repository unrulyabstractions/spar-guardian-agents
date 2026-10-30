"""Probing a sandbox: one command's record, a table of commands, a Python script whose last line is JSON, and the
setup solver every domain probes its agents' seats with before they start.

    results = await run_commands(PROBE_COMMANDS, {"w": workdir}, user=user, cwd=workdir)
    found = await run_json(SCRIPT, stdin={"paths": paths}, box=sandbox("a01"))

A probe never fails the sample: a command that cannot run records the error in place of a result. A script run by
run_json raises on failure, and its caller decides.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox

PROBE_STORE = "cheatbench_probe"     # sample store: {agent: {check: {rc, out, err}}}
KEEP_OUT = 3000
KEEP_ERR = 500
SCRIPT_TIMEOUT_S = 120


async def run_command(cmd: str, *, user: str | None = None, cwd: str | None = None, box=None, timeout: int = 60,
                      keep: int = KEEP_OUT) -> dict:
    """`bash -lc cmd` as `user` in the sample's sandbox or in `box`: {rc, out, err}, the output's last `keep`
    characters; rc None and the exception in err when the command could not run."""
    try:
        r = await (box or sandbox()).exec(["bash", "-lc", cmd], user=user, cwd=cwd, timeout=timeout)
        return {"rc": r.returncode, "out": (r.stdout or "")[-keep:], "err": (r.stderr or "")[-KEEP_ERR:]}
    except Exception as ex:  # noqa: BLE001 - the probe must never fail the sample
        return {"rc": None, "out": "", "err": f"{type(ex).__name__}: {ex}"[:KEEP_ERR]}


async def run_commands(commands: dict[str, str], fields: dict | None = None, *, user: str | None = None,
                       cwd: str | None = None, box=None) -> dict[str, dict]:
    """Each command of the table, its placeholders filled from `fields`, as run_command records it."""
    return {name: await run_command(cmd.format(**(fields or {})), user=user, cwd=cwd, box=box)
            for name, cmd in commands.items()}


async def run_json(script: str, *, args: list[str] | None = None, stdin=None, box=None, user: str = "root",
                   timeout: int = SCRIPT_TIMEOUT_S):
    """`python3 -c script args` as `user` (root by default) in the sample's sandbox or in `box`, `stdin` sent as
    JSON: the last line of its output, parsed as JSON. Raises RuntimeError with the script's message when it exits
    non-zero."""
    r = await (box or sandbox()).exec(["python3", "-c", script, *(args or [])],
                                      input=json.dumps(stdin) if stdin is not None else None, user=user,
                                      timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "").strip()[-800:] or f"exit {r.returncode}")
    return json.loads(r.stdout.strip().splitlines()[-1])


@solver
def environment_probe(prepare: Callable[[TaskState], Awaitable[None]],
                      probe: Callable[[TaskState], dict]) -> Solver:
    """Task setup: prepare(state) readies the sandbox (staging, the loopback guard), then probe(state) gives each
    agent's seat (src/sandbox/agent_tools.py Seat), command table and the table's fields, and the table runs as the
    seat's user in its directory and service. PROBE_STORE holds the records by agent."""
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        await prepare(state)
        state.store.set(PROBE_STORE, {
            agent: await run_commands(commands, fields, user=seat.user, cwd=seat.workdir, box=seat.box())
            for agent, (seat, commands, fields) in probe(state).items()})
        return state
    return solve
