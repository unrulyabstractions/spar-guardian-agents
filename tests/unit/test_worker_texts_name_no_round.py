"""Agents are not told of rounds: no text a worker reads on SWE, protein design or creative writing says "round". The
system prompt, the first message, every tool's description and argument descriptions our bindings give it, and the
texts the turn rules show it while it works (prompts.yaml), built with no model call, for a team told and not told
of its team, for a lone agent, and for the regulators and disregulators of a team (since the peer tools' rows lost
their round)."""
from __future__ import annotations

import re

import pytest
from inspect_ai.tool import ToolDef
from orbit.tools import ToolContext, ToolFactory

from src.configuration.presets import PRESETS
from src.configuration.settings import load_prompts
from src.domains import DOMAINS
from src.tasks import agent_roles, first_messages, instruction_suffix, system_prompts, team_tools

ROUND = re.compile(r"(?<!bring your teammates )round", re.IGNORECASE)
"""Any "round" but the disregulator's "bring your teammates round" (the user's text, 2026-11-23), which means win them
over and says nothing of the run's rounds."""
KEYS = ("swe", "protein_design", "creative_writing")
CONFIGS = {
    "team": dict(n_agents=3),
    "untold": dict(n_agents=3, mention_team=False),
    "lone": dict(n_agents=1),
    # the influencers too, with the peer tools, peer_shell and the hold on a hand-in
    "influencers": dict(n_agents=4, regulators=1, disregulators=1, peer_shell=True),
    "hold": dict(n_agents=4, regulators=1, submit_hold=True),
}
# The texts a worker reads while it works, by prompts.yaml section: the turn rules' lines and refusals, the hand-in's
# results and notices, the board's budgets and notices, the jobs' answers, the presence lines.
RUNTIME = ("round", "submit", "stop_working", "submit_hold", "presence", "notice", "budget", "jobs", "continue")


def strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings(v)]
    return [s for v in value for s in strings(v)] if isinstance(value, list) else []


def tool_texts(tool) -> list[str]:
    definition = tool if isinstance(tool, ToolDef) else ToolDef(tool)
    return [definition.description or "",
            *(p.description or "" for p in definition.parameters.properties.values())]


def worker_texts(key: str, fields: dict) -> dict[str, list[str]]:
    config = PRESETS["default"].replace(**fields)
    bench = DOMAINS[key](None)
    plan = bench.plan(config, agent_roles(config))
    sample = bench.sample(plan, instruction_suffix(bench, config))
    first = first_messages(sample, plan, bench) or sample.metadata["cheatbench"].get("first_message") \
        or dict.fromkeys(plan.agents, sample.input)
    prompts = system_prompts(bench, plan)
    names, bindings = team_tools(bench, plan, bench.tools(plan))
    out = {}
    for agent in plan.agents:
        texts = [prompts[agent] or "", first[agent]]
        for name in names[agent]:
            binding = bindings.get(name)
            if isinstance(binding, ToolFactory):
                texts += tool_texts(binding.create(ToolContext(agent_name=agent, sample_id=1, epoch=0)))
        out[agent] = texts
    return out


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("name", sorted(CONFIGS))
def test_no_prompt_or_tool_description_a_worker_gets_says_round(key, name):
    for agent, texts in worker_texts(key, CONFIGS[name]).items():
        said = [line for text in texts for line in text.splitlines() if ROUND.search(line)]
        assert not said, (key, name, agent, said)


def test_no_text_the_turn_rules_show_a_worker_says_round():
    prompts = load_prompts()
    said = [s for section in RUNTIME for s in strings(prompts[section]) if ROUND.search(s)]
    assert not said, said
