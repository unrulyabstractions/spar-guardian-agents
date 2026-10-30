"""Probing a sandbox before and after the agents: one command's record, a table of commands, and the store key the
domains' setup solvers write their probe to.

    results = await run_commands(PROBE_COMMANDS, {"w": workdir}, user=user, cwd=workdir)

A probe never fails the sample: a command that cannot run records the error in its place of a result.
"""
from __future__ import annotations

from inspect_ai.util import sandbox

PROBE_STORE = "cheatbench_probe"     # sample store: {check: {rc, out, err}}, or {agent: {check: ...}} per agent
KEEP_OUT = 3000
KEEP_ERR = 500


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
