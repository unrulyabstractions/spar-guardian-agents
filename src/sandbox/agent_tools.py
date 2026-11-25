"""The agents' tools: ORBIT's built-in bash and python for a lone agent, and `shell` and `run_python` bound per agent
to a seat (a Linux user in a directory of a sandbox service), with that user's processes killed after each command.
No command has a time limit of its own: every one runs through src/sandbox/command_jobs.py, which keeps a command still
running when its round's time runs out as a job.

    seats = {"k7q2m": Seat(user="k7q2m", uid=1001, home="/home/k7q2m", workdir="/workspace/k7q2m", reap=True)}
    tools = roster_tools(["k7q2m"], seats, task_tools=review_binding())

ORBIT reserves the names bash and python for its built-in tools, which take one user for every agent, so a layout
with a seat per agent binds these two under other names. A lone agent keeps ORBIT's built-in tools as the sandbox
user, its tools' calls running through command_jobs as well: the turn rules' gate swaps their executor
(turn_rules.SWAPS, registered here) and keeps their names, descriptions and parameters. The descriptions are
Inspect's own, unless a domain gives its own. Every domain builds its tools with roster_tools.

TOOL_TIMEOUT_S is the per-call limit of the runs made before 2026-11-22, which the replays reproduce.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields

from inspect_ai.tool import ToolDef
from inspect_ai.util import sandbox
from orbit.tools import ToolContext, ToolFactory

from src.sandbox import command_jobs as jobs
from src.sandbox import sandbox_hygiene as hygiene
from src.swarm import turn_rules as rules
from src.swarm.tool_output_caps import NO_CAP, run_cap

# CheatBench's sandbox user and working directory, as its runner sets them up
AGENT_NAME = "user"
AGENT_UID = 1000
WORKDIR = "/workspace"
TOOL_TIMEOUT_S = 300           # the shell's limit in runs before 2026-11-22 (the replays'); none since
LONE_TOOL_NAMES = ["bash", "python"]
TOOL_NAMES = ["shell", "run_python"]
PYTHON_COMMANDS = ("python3 -", "python3 -P -")     # run_python's exec: alone in a directory, and beside a teammate
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
    """Where one agent works. The sample metadata holds a seat as its dict (as_dict), with a domain's own keys
    beside it, and Seat.read reads it back."""
    user: str | None = None         # the Linux user; None for the service's own
    uid: int | None = None
    home: str | None = None
    workdir: str | None = None      # the working directory; None for the service's own
    service: str | None = None      # the sandbox service; None for the sample's default
    reap: bool = False              # kill the user's processes after each command
    round_reap: bool = False        # kill what its calls left at the end of each round (command_jobs.reap_round)

    def box(self):
        """The seat's sandbox environment."""
        return sandbox(self.service) if self.service else sandbox()

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def read(cls, record: dict) -> Seat:
        """A seat from its record in the sample metadata, the record's other keys left out."""
        return cls(**{f.name: record[f.name] for f in fields(cls) if f.name in record})


@dataclass(frozen=True)
class AgentTools:
    """The tools of a roster, in the shapes ORBIT takes them."""

    per_agent: dict[str, list[str]]
    """AgentSpec.tools of each agent."""
    bindings: dict[str, ToolFactory] = field(default_factory=dict)
    """ORBIT tool_bindings: tools built per agent. Empty for ORBIT's built-in tools."""
    tool_kwargs: dict[str, dict] = field(default_factory=dict)
    """SetupConfig.tool_kwargs for ORBIT's built-in bash and python."""
    seats: dict[str, Seat] | None = None
    """Each agent's seat where its shell is bound to one (`shell` and `run_python`), which peer_shell runs in; None
    for ORBIT's built-in tools (a lone agent) and for a domain with no shell."""

    @property
    def names(self) -> list[str]:
        """Every agent's tool names, in order."""
        return list(dict.fromkeys(name for names in self.per_agent.values() for name in names))


def lone_tool_kwargs(user: str) -> dict[str, dict]:
    """SetupConfig.tool_kwargs for ORBIT's built-in bash and python as one user, with no time limit: their calls run
    through command_jobs (lone_swap)."""
    return {"bash": {"user": user}, "python": {"user": user}}


def holds_commands(tools: list[str]) -> bool:
    """Whether a tool list holds a command tool (command_jobs.COMMAND_TOOLS), whose calls can become jobs."""
    return any(name in jobs.COMMAND_TOOLS for name in tools)


async def run(seat: Seat, agent: str, command: str, *, tool: str = "shell", input: str | None = None,
              shown: str | None = None) -> str:
    """`bash --login -c command` in the seat (Inspect's bash and python tools' command, with the seat's directory),
    through command_jobs: the output is stderr, then stdout, as those tools return it, or a job's first answer when
    the round's time runs out first. A command that ends in time is followed by the seat's reap, which spares the
    calls still running. shown is the call's command as its job record keeps it (run_python's code)."""
    box = seat.box()

    async def reap(keep: list[str]) -> None:
        if seat.reap:
            await hygiene.reap_after(agent, seat.user, box=box, keep=keep)

    where = jobs.Where(box=box, service=seat.service, user=seat.user, cwd=seat.workdir, tracked=seat.round_reap)
    outcome = await jobs.run_command(where, agent, tool, shown if shown is not None else command,
                                     ["bash", "--login", "-c", command], input=input, after=reap,
                                     cap=tool_cap())
    return outcome.text


def tool_cap() -> int:
    """The bytes of one result an agent sees whole: the run's cap (tool_output_caps.run_cap), 0 or less read as none."""
    cap = run_cap()
    return cap if cap > 0 else NO_CAP


def python_command(seat: Seat, seats: dict[str, Seat]) -> str:
    """How `run_python` starts Python: code from stdin. Where another seat works in the same directory, -P keeps that
    directory off sys.path, so a teammate's planted json.py does not run as this agent."""
    sharers = sum(other.workdir == seat.workdir and other.service == seat.service for other in seats.values())
    alone, beside_a_teammate = PYTHON_COMMANDS
    return beside_a_teammate if sharers > 1 else alone


def tool_bindings(seats: dict[str, Seat], *,
                  descriptions: dict[str, tuple[str, str]] = DESCRIPTIONS) -> dict[str, ToolFactory]:
    """ORBIT tool_bindings for `shell` and `run_python`, each agent's in its seat."""
    def shell(ctx: ToolContext) -> ToolDef:
        seat = seats[ctx.agent_name]

        async def execute(command: str) -> str:
            return await run(seat, ctx.agent_name, command)

        description, parameter = descriptions["shell"]
        return ToolDef(execute, name="shell", description=description, parameters={"command": parameter})

    def run_python(ctx: ToolContext) -> ToolDef:
        seat = seats[ctx.agent_name]

        async def execute(code: str) -> str:
            return await run(seat, ctx.agent_name, python_command(seat, seats), tool="run_python", input=code,
                             shown=code)

        description, parameter = descriptions["run_python"]
        return ToolDef(execute, name="run_python", description=description, parameters={"code": parameter})

    return {"shell": ToolFactory(shell), "run_python": ToolFactory(run_python)}


def roster_tools(agents: list[str], seats: dict[str, Seat] | None, *,
                 task_tools: dict[str, ToolFactory] | None = None, holders: dict[str, list[str]] | None = None,
                 descriptions: dict[str, tuple[str, str]] = DESCRIPTIONS) -> AgentTools:
    """The roster's tools: ORBIT's bash and python as AGENT_NAME without seats (CheatBench's layout), else `shell`
    and `run_python` in each agent's seat; then the task's own tools, every one for every agent unless `holders`
    names each agent's."""
    task_tools = task_tools or {}
    if seats is None:
        base, bindings, kwargs = LONE_TOOL_NAMES, {}, lone_tool_kwargs(AGENT_NAME)
    else:
        base, bindings, kwargs = TOOL_NAMES, tool_bindings(seats, descriptions=descriptions), {}
    per_agent = {a: [*base, *(holders[a] if holders is not None else task_tools)] for a in agents}
    return AgentTools(per_agent=per_agent, bindings={**bindings, **task_tools}, tool_kwargs=kwargs, seats=seats)


LONE_SEAT = Seat(user=AGENT_NAME)
"""A lone agent's place: CheatBench's sandbox user in the sandbox's own working directory, with no reap."""


def lone_swap(agent: str, definition: ToolDef) -> ToolDef:
    """ORBIT's built-in bash or python of a lone agent, its name, description and parameters kept, its calls run as
    Inspect's ran them (`bash --login -c`, python's code on stdin to `python3 -`) through command_jobs."""
    if definition.name == "bash":
        async def execute(command: str) -> str:
            return await run(LONE_SEAT, agent, command, tool="bash")
    else:
        async def execute(code: str) -> str:
            return await run(LONE_SEAT, agent, PYTHON_COMMANDS[0], tool="python", input=code, shown=code)
    return ToolDef(execute, name=definition.name, description=definition.description,
                   parameters=definition.parameters, parallel=definition.parallel, viewer=definition.viewer,
                   model_input=definition.model_input, max_output=definition.max_output, options=definition.options)


rules.SWAPS.update({name: lone_swap for name in LONE_TOOL_NAMES})
