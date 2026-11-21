"""How much of one tool result an agent sees: the run's cap for a worker, SwarmConfig.influencer_tool_output_bytes for
a regulator or a disregulator.

    bindings = capped_bindings(bindings, config, roles)     # src/tasks.py team_tools, over every binding
    budget = cap_of(config, role)                           # what a tool may return whole, in UTF-8 bytes

Inspect cuts a tool's result at its cap and says so (inspect_ai/model/_call_tools.py truncate_tool_output): the
tool's own ToolDef.max_output when it sets one, else the run's GenerateConfig.max_tool_output (run.py sets it from
SwarmConfig.tool_output_bytes), else 16 KiB. ORBIT builds each binding per agent and hands Inspect the ToolDef as built,
so capped_bindings gives every tool an influencer is bound its own max_output; a worker's tools keep none and take the
run's. ORBIT's built-in bash and python, which only a lone agent gets, take the run's. A tool that sets its own
max_output keeps it.

The peer tools (src/swarm/_regulator_oversight.py) and peer_shell (src/sandbox/peer_shell.py) answer in JSON, which a
cut would leave invalid, so they read cap_of and fit their answer under it themselves.
"""
from __future__ import annotations

import copy

from inspect_ai.model._generate_config import active_generate_config  # the run's config: no public accessor
from inspect_ai.tool import ToolDef
from orbit.tools import ToolContext, ToolFactory

from src.configuration.swarm_config import INFLUENCERS, SwarmConfig, role_of

DEFAULT_CAP = 16 * 1024     # Inspect's cap when the run sets none


def run_cap() -> int:
    """The run's cap: the active GenerateConfig's max_tool_output, else Inspect's 16 KiB."""
    return active_generate_config().max_tool_output or DEFAULT_CAP


def agent_cap(config: SwarmConfig, role: str) -> int | None:
    """The cap an agent's tools carry themselves: influencer_tool_output_bytes for an influencer, None (the run's) for
    a worker or when the field is None."""
    return config.influencer_tool_output_bytes if role in INFLUENCERS else None


def cap_of(config: SwarmConfig, role: str) -> int:
    """The bytes of one result an agent of this role sees whole."""
    return agent_cap(config, role) or run_cap()


def capped(value, cap: int | None):
    """The tool as built, with max_output = cap when it sets none; a copy, so a shared instance stays as it was."""
    if cap is None:
        return value
    definition = copy.copy(value) if isinstance(value, ToolDef) else ToolDef(value)
    if definition.max_output is None:
        definition.max_output = cap
    return definition


def capped_bindings(bindings: dict, config: SwarmConfig, roles: dict[str, str] | None) -> dict[str, ToolFactory]:
    """Every binding as a factory whose tool carries the agent's cap (agent_cap)."""
    def wrap(binding) -> ToolFactory:
        def create(ctx: ToolContext):
            value = binding.create(ctx) if isinstance(binding, ToolFactory) else binding
            return capped(value, agent_cap(config, role_of(roles, ctx.agent_name)))
        return ToolFactory(create)

    return {name: wrap(binding) for name, binding in bindings.items()}
