"""How much of one tool result an agent sees, and how a longer one is cut: one rule for every agent and every tool.

    install()                                   # done on import: Inspect's cut becomes middle_cut
    bindings = capped_bindings(bindings, config)            # src/tasks.py team_tools, over every binding
    budget = cap_of(config)                                 # what a tool may return whole, in UTF-8 bytes

The cap is SwarmConfig.tool_output_bytes (swarm_config.TOOL_OUTPUT_BYTES, 64 KiB), the same for workers and
influencers. The task's own GenerateConfig carries it (src/tasks.py swarm_task), so every entry point gets it, `inspect
eval src/tasks.py@...` included; run.py sets it on the eval too, and capped_bindings sets it on every tool bound through
ORBIT as ToolDef.max_output. ORBIT's built-in bash and python, which only a lone agent gets, take the task's.

The cut. Inspect cuts a result past its cap in inspect_ai/model/_call_tools.py truncate_tool_output, which keeps the
start and adds a note; the sandbox bridge's host tools import the same function by name. install() replaces both names
with middle_cut for every tool of the process, ORBIT's built-in bash and python included: a result past the cap keeps
its start and its end, half the cap each, with one line between them, "[cut: N of M characters omitted from the
middle]". The ToolEvent still records (raw bytes, cap) as `truncated`. Runs recorded before 2026-11-20 were cut by
Inspect at the start; their config holds no tool_output_cut, and the replay reproduces each run's own cut
(src/replay/replay_run.py tool_output).

The peer tools (src/swarm/_regulator_oversight.py) and peer_shell (src/sandbox/peer_shell.py) answer in JSON, which a
cut would leave invalid, so they read cap_of and fit their answer under it themselves.
"""
from __future__ import annotations

import copy

import inspect_ai.agent._bridge.sandbox.host_tool as host_tool  # imports the truncation by name
import inspect_ai.model._call_tools as call_tools  # Inspect's one truncation point: no public hook
from inspect_ai.model._generate_config import active_generate_config  # the run's config: no public accessor
from inspect_ai.tool import ToolDef
from orbit.tools import ToolContext, ToolFactory

from src.configuration.swarm_config import SwarmConfig

DEFAULT_CAP = 16 * 1024     # Inspect's cap when nothing sets one
NO_CAP = 2 ** 62            # what a cap of 0 or less means to the tools that pack their answer: no limit
CUT_LINE = "[cut: {omitted} of {total} characters omitted from the middle]"
INSPECT_CUT = getattr(call_tools.truncate_tool_output, "inspect_original", call_tools.truncate_tool_output)
"""Inspect's own cut (start kept, with its note), which the replay applies to runs recorded before the middle cut."""


def run_cap() -> int:
    """The run's cap: the active GenerateConfig's max_tool_output, else Inspect's 16 KiB. 0 or less is no cap, as in
    Inspect (inspect_ai/_util/text.py truncate_string_to_bytes)."""
    cap = active_generate_config().max_tool_output
    return DEFAULT_CAP if cap is None else cap


def cap_of(config: SwarmConfig) -> int:
    """The bytes of one result every agent sees whole; NO_CAP when the run sets none (0 or less)."""
    cap = config.tool_output_bytes if config.tool_output_bytes is not None else run_cap()
    return cap if cap > 0 else NO_CAP


def middle_cut(text: str, cap: int) -> str | None:
    """The text within `cap` UTF-8 bytes, its start and its end with CUT_LINE between them; None when it fits or the
    cap is 0 or less (no cap, as in Inspect)."""
    raw = text.encode()
    if cap <= 0 or len(raw) <= cap:
        return None
    line = CUT_LINE.format(omitted=len(text), total=len(text))
    half = max(0, (cap - len(line.encode()) - 2) // 2)
    head = raw[:half].decode(errors="ignore")
    tail = raw[len(raw) - half:].decode(errors="ignore") if half else ""
    omitted = len(text) - len(head) - len(tail)
    return f"{head}\n{CUT_LINE.format(omitted=omitted, total=len(text))}\n{tail}"


def truncate_tool_output(tool_name: str, output: str, max_output: int | None):
    """Inspect's truncate_tool_output with middle_cut in place of its start-only cut: the same limit (the tool's own,
    else the run's, else 16 KiB; 0 or less is no cap, which Inspect's react() gives its submit tool) and the same
    TruncatedToolOutput, or None when the output fits."""
    limit = max_output if max_output is not None else run_cap()
    cut = middle_cut(output, limit)
    return None if cut is None else call_tools.TruncatedToolOutput(cut, len(output.encode()), limit)


truncate_tool_output.inspect_original = INSPECT_CUT


def install() -> None:
    """Make middle_cut the cut of every tool result in this process (idempotent): Inspect's truncation point, and the
    sandbox bridge's host tools, which import the function by name."""
    call_tools.truncate_tool_output = truncate_tool_output
    host_tool.truncate_tool_output = truncate_tool_output


def capped(value, cap: int | None):
    """The tool as built, with max_output = cap when it sets none; a copy, so a shared instance stays as it was."""
    if cap is None:
        return value
    definition = copy.copy(value) if isinstance(value, ToolDef) else ToolDef(value)
    if definition.max_output is None:
        definition.max_output = cap
    return definition


def capped_bindings(bindings: dict, config: SwarmConfig) -> dict[str, ToolFactory]:
    """Every binding as a factory whose tool carries the run's cap (tool_output_bytes)."""
    def wrap(binding) -> ToolFactory:
        def create(ctx: ToolContext):
            value = binding.create(ctx) if isinstance(binding, ToolFactory) else binding
            return capped(value, config.tool_output_bytes)
        return ToolFactory(create)

    return {name: wrap(binding) for name, binding in bindings.items()}


install()
