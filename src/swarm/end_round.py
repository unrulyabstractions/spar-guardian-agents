"""end_round: the tool every agent holds, lone agents and influencers included, that ends its round now.

It takes no argument and never counts toward the calls of a round. The round ends after the reply that called it, and a
later call of that reply is refused (src/swarm/turn_rules/_turn_rounds.py). Its description and result come from
prompts.yaml round.end_round and round.round_ended.

    bindings = {**end_round_bindings(), ...}          # ORBIT tool_bindings; every AgentSpec.tools names END_ROUND
"""
from __future__ import annotations

from inspect_ai.tool import ToolDef
from orbit.tools import ToolFactory

from src.configuration.settings import load_prompts
from src.swarm.turn_rules import END_ROUND, close_round


def end_round(agent: str, told: bool = True) -> ToolDef:
    """The tool bound to `agent`: closes its round (turn_rules.close_round). An agent not told of its team (told
    false) reads a description that names no teammate."""
    texts = load_prompts()["round"]

    async def execute() -> str:
        close_round(agent)
        return texts["round_ended"]

    return ToolDef(execute, name=END_ROUND, description=texts["end_round" if told else "end_round_untold"])


def end_round_bindings(told: bool = True) -> dict[str, ToolFactory]:
    """ORBIT tool_bindings for wait, built per agent; told is SwarmConfig.knows_team."""
    return {END_ROUND: ToolFactory(lambda ctx: end_round(ctx.agent_name, told))}
