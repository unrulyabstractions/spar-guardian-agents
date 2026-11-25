"""At most N calls of one tool: the run's tool limits (SwarmConfig.tool_limits), one wrapper over every binding.

    config = run_limits(benchmark, config)        # src/tasks.py swarm_task: the task's defaults under the study's
    bindings = limited_bindings(capped_bindings(bindings, config), config, names)   # src/tasks.py team_tools

A limit names a tool by its binding name, a number of calls, who shares the count (per: agent, each its own; team,
one for all) and the rounds the count holds (rounds: None, the whole run; N, the last N rounds, the round under way
included, so a call in round r counts through round r + N - 1; 1 is each round). Rounds are the round gate's own
(turn_rules current_round), so a head start or a late entry counts as the gate does.

A task may give its own tools default limits and bounds (Benchmark.tool_limits, tool_limit_bounds). run_limits lays
the study's limits over the task's defaults tool by tool: a study's limit on a tool replaces every default on it, and
tool:none frees it. A bound is the loosest limit the task accepts on the tool. The run's limits on that tool must
together allow no more calls than the bound in any stretch of the bound's rounds (within), so 1:agent:10rounds and
1:agent:run sit within 1:agent:round, and 2:agent:2rounds does not (two calls in one round). A team's count sits within
an agent's bound of the same numbers; an agent's count never sits within a team's bound. A run past a bound, or one
that frees a bounded tool, is refused when the task is built, with the bound in the message.

limited_bindings refuses a limit on a tool no agent of the run holds through a binding, then wraps each limited
binding. The tool's description
gains one sentence per limit (prompts.yaml tool_limits description). Each call asks every limit on its tool first. A
call past any of them is refused before the tool runs (Refused, prompts.yaml tool_limits refused) and counts toward
none. Every other call counts toward each, whatever the tool then returns. With no limit, the bindings come back as
the very objects given.

The round's gate (turn_rules counted) wraps every tool outside this wrapper. So a refused call has already used one of
the agent's tool calls and counted toward its round, and its timeline entry keeps the refusal, as a refused board_post
does. The counts and the refused calls live in the Inspect store (ToolCalls), which the eval log keeps beside the
turn state.
"""
from __future__ import annotations

from inspect_ai.tool import ToolDef
from inspect_ai.util import StoreModel, store_as
from orbit.tools import ToolContext, ToolFactory
from pydantic import Field

from src.configuration.settings import load_prompts
from src.configuration.swarm_config import SwarmConfig, ToolLimit
from src.swarm.turn_rules._turn_state import Refused, current_round

TEAM = "team"                   # the holder of a team's count in ToolCalls.counts


class ToolCalls(StoreModel):
    """The calls the run's tool limits let through and refused, saved in the eval log. counts maps each limit's text
    (check_fix:2:agent:run) to each holder, an agent or TEAM, and the round of each call it let through, in order.
    refused holds each refused call: {tool, agent, round, limit}."""

    counts: dict[str, dict[str, list[int]]] = Field(default_factory=dict)
    refused: list[dict] = Field(default_factory=list)


def text_key(limit: ToolLimit, told: bool) -> str:
    """The key of the limit's texts in prompts.yaml tool_limits: per_when, when run, round or rounds (N of 2 or
    more), with _untold for an agent's count over rounds when the agent is not told of its team."""
    when = "rounds" if (limit.rounds or 0) > 1 else limit.when
    key = f"{limit.per}_{when}"
    return key if told or key not in ("agent_round", "agent_rounds") else f"{key}_untold"


def limit_text(kind: str, limit: ToolLimit, told: bool, left: int = 1) -> str:
    """The limit's sentence (kind "description") or refusal (kind "refused"); left is the rounds until a call opens
    again, which a refusal of a limit over N rounds states as the times the teammates act (prompts.yaml again)."""
    texts = load_prompts()["tool_limits"]
    count = texts["count"]["one" if limit.calls == 1 else "many"].format(calls=limit.calls)
    again = texts["again"]["one" if left == 1 else "many"].format(left=left)
    return texts[kind][text_key(limit, told)].format(count=count, turns=limit.rounds, again=again)


def described(description: str, limits: list[ToolLimit], told: bool) -> str:
    """The tool's description with each limit's sentence after it."""
    return " ".join([description.rstrip(), *(limit_text("description", limit, told) for limit in limits)])


def holder(limit: ToolLimit, agent: str) -> str:
    return agent if limit.per == "agent" else TEAM


def now() -> int:
    """The round under way, as the round gate reads it; 0 outside a planned activation."""
    return current_round() or 0


def kept(limit: ToolLimit, agent: str) -> list[int]:
    """The rounds of the calls the agent's count still holds: every call for the whole run, else those of the last
    `rounds` rounds, oldest first."""
    rounds = store_as(ToolCalls).counts.get(limit.text, {}).get(holder(limit, agent), [])
    return rounds if limit.rounds is None else [r for r in rounds if r > now() - limit.rounds]


def left(limit: ToolLimit, agent: str) -> int:
    """The rounds until a spent limit over N rounds lets a call through again: once its oldest calls leave the count,
    enough that fewer than `calls` remain. 1 for any other limit."""
    rounds = kept(limit, agent)
    if limit.rounds is None or len(rounds) < limit.calls:
        return 1
    return rounds[len(rounds) - limit.calls] + limit.rounds - now()


def admit(tool: str, agent: str, limits: list[ToolLimit], told: bool) -> None:
    """Let one call through every limit on the tool, counting it toward each, or refuse it (Refused) with the first
    spent limit's text and record the refusal. Nothing awaits between the check and the count, so agents in one
    concurrent round never pass a shared count."""
    record = store_as(ToolCalls)
    spent = next((limit for limit in limits if len(kept(limit, agent)) >= limit.calls), None)
    if spent is not None:
        record.refused = [*record.refused, {"tool": tool, "agent": agent, "round": now(), "limit": spent.text}]
        raise Refused(limit_text("refused", spent, told, left(spent, agent)))
    counts = dict(record.counts)
    for limit in limits:
        mine = counts.get(limit.text, {})
        counts[limit.text] = {**mine, holder(limit, agent): [*mine.get(holder(limit, agent), []), now()]}
    record.counts = counts


def limited(value, tool: str, agent: str, limits: list[ToolLimit], told: bool) -> ToolDef:
    """The tool as built, as a ToolDef whose description states its limits and whose calls pass admit first."""
    definition = value if isinstance(value, ToolDef) else ToolDef(value)

    async def execute(**kwargs):
        admit(tool, agent, limits, told)
        return await definition.tool(**kwargs)

    return ToolDef(execute, name=definition.name, description=described(definition.description, limits, told),
                   parameters=definition.parameters, parallel=definition.parallel, viewer=definition.viewer,
                   model_input=definition.model_input, max_output=definition.max_output, options=definition.options)


def limitable(held: dict[str, list[str]], bindings: dict) -> list[str]:
    """The tools a limit may name: those an agent of the run holds through a binding."""
    return sorted({name for names in held.values() for name in names} & set(bindings))


def check(config: SwarmConfig, held: dict[str, list[str]], bindings: dict) -> None:
    """Refuse a limit on a tool no agent of the run holds through a binding, with the tools it could have named."""
    names = limitable(held, bindings)
    unknown = sorted({limit.tool for limit in config.tool_limits} - set(names))
    if unknown:
        raise ValueError(f"tool_limits: no agent of this run holds {unknown}; a limit may name one of {names}")


# ------------------------------------------------------------------------------------------------ the task's limits

def most_calls(limit: ToolLimit, rounds: int | None) -> float:
    """The most calls the limit lets one count make in `rounds` rounds in a row (None: the whole run)."""
    if limit.rounds is None:
        return limit.calls
    return float("inf") if rounds is None else limit.calls * -(-rounds // limit.rounds)


def within(limits: list[ToolLimit], bound: ToolLimit) -> bool:
    """Whether a tool's limits together allow no more than the bound: one of them, counted as the bound counts or
    for the whole team under an agent's bound, makes at most the bound's calls in any stretch of its rounds."""
    return any((limit.per == bound.per or limit.per == "team") and most_calls(limit, bound.rounds) <= bound.calls
               for limit in limits if limit.calls is not None)


def run_limits(benchmark, config: SwarmConfig) -> SwarmConfig:
    """The config with the run's limits: the benchmark's defaults (Benchmark.tool_limits) on every tool the study's
    tool_limits does not name, then the study's. Refused (ValueError) when the limits on a tool the benchmark bounds
    (tool_limit_bounds) allow more than the bound, or free the tool. The very config given when the benchmark gives
    no default and no bound."""
    defaults = [ToolLimit.model_validate(text) for text in benchmark.tool_limits]
    bounds = [ToolLimit.model_validate(text) for text in benchmark.tool_limit_bounds]
    if not defaults and not bounds:
        return config
    named = {limit.tool for limit in config.tool_limits}
    limits = (*(limit for limit in defaults if limit.tool not in named), *config.tool_limits)
    for bound in bounds:
        mine = [limit for limit in limits if limit.tool == bound.tool]
        if not within(mine, bound):
            given = [limit.text for limit in mine] or ["no limit"]
            raise ValueError(f"tool_limits: {benchmark.key} allows {bound.tool} at most {bound.text}; this run gives "
                             f"{given}")
    return config if limits == config.tool_limits else config.replace(tool_limits=limits)


def limited_bindings(bindings: dict, config: SwarmConfig, held: dict[str, list[str]]) -> dict:
    """Every binding, each limited one wrapped (limited) for the agent it is built for. held gives each agent's tool
    names (AgentSpec.tools). The very dict given when the run sets no limit."""
    if not config.tool_limits:
        return bindings
    check(config, held, bindings)
    by_tool: dict[str, list[ToolLimit]] = {}
    for limit in config.tool_limits:
        if limit.calls is not None:                 # tool:none frees the tool
            by_tool.setdefault(limit.tool, []).append(limit)

    def wrap(name: str, binding) -> ToolFactory:
        def create(ctx: ToolContext) -> ToolDef:
            value = binding.create(ctx) if isinstance(binding, ToolFactory) else binding
            return limited(value, name, ctx.agent_name, by_tool[name], config.knows_team)
        return ToolFactory(create)

    return {name: wrap(name, binding) if name in by_tool else binding for name, binding in bindings.items()}

