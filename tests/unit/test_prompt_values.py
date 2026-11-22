"""No system prompt holds a parameter's number: each states the rule with no value, and the agent's first message gives
the value after the rounds' values (prompts.yaml first_values; src/tasks.py first_messages). Every domain, built with
no model call under a lone agent and teams with a regulator and a disregulator, told and not told of their team."""
from __future__ import annotations

import re

import pytest

from src.configuration.presets import PRESETS
from src.configuration.prompt_texts import rounds_values
from src.configuration.settings import load_prompts
from src.domains import DOMAINS
from src.tasks import agent_roles, first_messages, instruction_suffix, system_prompts

PROMPTS = load_prompts()
VALUES = PROMPTS["first_values"]
# Each rule a system prompt may state, with the start of the value its first message then gives.
RULES = {"check": "within a set number of your first rounds", "submit": "a set number of rounds after",
         "agents": "Briefing for your role", "hold": "holds the move for a set number of rounds",
         "credential": "holds one part of a bearer credential"}
TIME_RULE = PROMPTS["round"]["time"]                           # the round's time limit, its value round.minutes
TIME_VALUE = PROMPTS["round"]["minutes"].split("{")[0]
CONFIGS = {
    "lone": dict(n_agents=1),
    "informed": dict(n_agents=4, regulators=1, disregulators=1, informed_roles=True, submit_delay_rounds=5),
    "untold": dict(n_agents=3, mention_team=False, submit_delay_rounds=1),
}
# Numbers that are names, not parameters: agent IDs, CheatBench's agent-0NN branches, Python 3, an endpoint's /v1/.
NAMES = re.compile(r"Python 3|agent-0\d\d|/v1/")


def built(key: str, fields: dict):
    config = PRESETS["default"].replace(rounds=37, calls_per_round=13, **fields)
    bench = DOMAINS[key](None)
    plan = bench.plan(config, agent_roles(config))
    sample = bench.sample(plan, instruction_suffix(bench, config))
    first = first_messages(sample, plan, bench) or sample.metadata["cheatbench"].get("first_message") \
        or dict.fromkeys(plan.agents, sample.input)
    return plan, system_prompts(bench, plan), first


@pytest.mark.parametrize("key", sorted(DOMAINS))
@pytest.mark.parametrize("name", sorted(CONFIGS))
def test_no_system_prompt_holds_a_number_and_the_first_message_gives_each_value(key, name):
    plan, prompts, first = built(key, CONFIGS[name])
    line = rounds_values(plan.config)
    assert line == "You have 37 rounds, and up to 13 tool calls per round. A round lasts at most 10 minutes."
    for agent in plan.agents:
        prompt, message = prompts[agent], first[agent]
        left = NAMES.sub("", re.sub("|".join(plan.agents), "", prompt))
        assert not re.search(r"\d", left), (agent, [s for s in re.split(r"(?<=\.)\s+|\n", left) if re.search(r"\d", s)])
        assert message.count(line) == 1
        assert TIME_RULE in prompt and message.count(TIME_VALUE) == 1, agent
        for value, rule in RULES.items():
            start = VALUES[value].split("{")[0]
            assert message.count(start) == (rule in prompt), (agent, value)


def test_the_values_follow_the_rounds_values_in_their_paragraph():
    """A worker's values extend the paragraph of the rounds' values; a regulator that oversees reads them after the
    task it quotes, which stays its teammates' own words."""
    plan, _, first = built("openmath", CONFIGS["informed"])
    roles = plan.roles
    worker = next(a for a in plan.agents if roles[a] == "worker")
    regulator = next(a for a in plan.agents if roles[a] == "regulator")
    line = rounds_values(plan.config)
    delay = VALUES["submit"].format(rounds="5 rounds")
    assert first[worker].rstrip().endswith(f"{line} {delay}")
    check, agents = VALUES["check"].format(rounds="2 rounds"), VALUES["agents"].format(n=4)
    assert first[regulator].rstrip().endswith(f"{line}\n\n{check} {agents} {delay}")
