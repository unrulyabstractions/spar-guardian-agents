"""The agents' `shell` and `run_python` tools, bound per agent: Inspect's bash and python run as a Linux user in a
directory of a sandbox service, with that user's processes killed after each command.

    bindings = tool_bindings(lambda agent: Seat(user=agent, cwd=f"/workspace/{agent}", reap=agent), timeout=300)

ORBIT reserves the names bash and python for its built-in tools, which take one user for every agent, so a layout
with a seat per agent binds these two under SWE's names. A lone agent keeps ORBIT's built-in tools as the sandbox
user (lone_tool_kwargs). The descriptions are Inspect's own, unless a domain gives its own.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from inspect_ai.tool import ToolDef
from inspect_ai.util import sandbox
from orbit.tools import ToolContext, ToolFactory

from src.benchmark import sandbox_hygiene as hygiene

LONE_TOOL_NAMES = ["bash", "python"]
TOOL_NAMES = ["shell", "run_python"]
BASH_DESCRIPTION = "Use this function to execute bash commands."
PYTHON_DESCRIPTION = """Use the python function to execute Python code.

The Python tool executes single-run Python scripts. Important notes:
1. Each execution is independent - no state is preserved between runs
2. You must explicitly use print() statements to see any output
3. Simply writing expressions (like in notebooks) will not display results
4. The script cannot accept interactive input during execution
5. Return statements alone won't produce visible output
6. All variables and imports are cleared between executions
7. Standard output (via print()) is the only way to see results"""
# {tool: (description, parameter description)}; Inspect's texts
DESCRIPTIONS = {"shell": (BASH_DESCRIPTION, "The bash command to execute."),
                "run_python": (PYTHON_DESCRIPTION, "The python code to execute.")}


@dataclass(frozen=True)
class Seat:
    """Where one agent's commands run."""
    user: str | None = None         # the Linux user; None for the service's own
    cwd: str | None = None          # the working directory; None for the service's own
    service: str | None = None      # the sandbox service; None for the sample's default
    reap: str | None = None         # the user whose processes are killed after each command; None for nobody


def lone_tool_kwargs(user: str, timeout: int) -> dict[str, dict]:
    """SetupConfig.tool_kwargs for ORBIT's built-in bash and python as one user."""
    return {"bash": {"user": user, "timeout": timeout}, "python": {"user": user, "timeout": timeout}}


async def run(seat: Seat, agent: str, command: str, *, timeout: int, input: str | None = None) -> str:
    """`bash --login -c command` in the seat (Inspect's bash and python tools' command, with the seat's directory);
    the output is stderr, then stdout, as those tools return it. The reap runs whatever happened."""
    box = sandbox(seat.service) if seat.service else sandbox()
    try:
        result = await box.exec(["bash", "--login", "-c", command], input=input, timeout=timeout, user=seat.user,
                                cwd=seat.cwd)
    finally:
        if seat.reap:
            await hygiene.reap_after(agent, seat.reap, box=box)
    return (f"{result.stderr}\n" if result.stderr else "") + f"{result.stdout}"


def tool_bindings(seat_of: Callable[[str], Seat], *, timeout: int,
                  descriptions: dict[str, tuple[str, str]] = DESCRIPTIONS) -> dict[str, ToolFactory]:
    """ORBIT tool_bindings for `shell` and `run_python`, each agent's in its seat (seat_of(agent))."""
    def shell(ctx: ToolContext) -> ToolDef:
        seat = seat_of(ctx.agent_name)

        async def execute(command: str) -> str:
            return await run(seat, ctx.agent_name, command, timeout=timeout)

        description, parameter = descriptions["shell"]
        return ToolDef(execute, name="shell", description=description, parameters={"command": parameter})

    def run_python(ctx: ToolContext) -> ToolDef:
        seat = seat_of(ctx.agent_name)

        async def execute(code: str) -> str:
            return await run(seat, ctx.agent_name, "python3 -", timeout=timeout, input=code)

        description, parameter = descriptions["run_python"]
        return ToolDef(execute, name="run_python", description=description, parameters={"code": parameter})

    return {"shell": ToolFactory(shell), "run_python": ToolFactory(run_python)}
