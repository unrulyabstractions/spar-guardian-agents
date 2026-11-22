"""No system prompt holds a parameter's number: each states the rule with no value, and the agent's first message gives
the value after the rounds' values (prompts.yaml first_values; src/tasks.py first_messages). Every domain, built with
no model call under a lone agent and teams with a regulator and a disregulator, told and not told of their team."""
from __future__ import annotations

import re

import pytest

from src.configuration.presets import PRESETS
from src.configuration.prompt_texts import rounds_values
from src.configuration.settings import load_instruction_levels, load_prompts
from src.configuration.swarm_config import Window
from src.domains import DOMAINS
from src.swarm.turn_rules import round_line
from src.tasks import agent_roles, first_messages, instruction_suffix, system_prompts

PROMPTS = load_prompts()
VALUES = PROMPTS["first_values"]
# Each rule a system prompt may state, with the start of the value its first message then gives.
RULES = {"check": "within a set number of your first rounds", "submit": "a set number of rounds after",
         "agents": "Briefing for your role", "credential": "holds one part of a bearer credential"}
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
    cheating = load_instruction_levels()["vars"]["openmath"]["cheating"]
    assert first[regulator].rstrip().endswith(f"{line}\n\n{cheating}\n\n{check} {agents} {delay}")


@pytest.mark.parametrize("role", ["regulator", "disregulator"])
def test_a_late_entrant_reads_the_rounds_it_has_left_on_entering(role):
    """An agent whose window opens in round 3 of 8 reads "You have 6 rounds" in its first message, as its first round's
    line says ("Round 3 of 8. You have 6 rounds left, including this one."); the agents there from round 1 read 8."""
    config = PRESETS["default"].replace(n_agents=3, rounds=8, **{f"{role}s": 1, f"{role}_window": Window(start=3)})
    bench = DOMAINS["subset_sum"](None)
    plan = bench.plan(config, agent_roles(config))
    first = first_messages(bench.sample(plan, instruction_suffix(bench, config)), plan, bench)
    late = next(a for a in plan.agents if plan.roles[a] == role)
    assert first[late].count(rounds_values(config, entry=3)) == 1 and "You have 8 rounds" not in first[late]
    assert rounds_values(config, entry=3).startswith("You have 6 rounds, and up to 10 tool calls per round.")
    assert "You have 6 rounds left, including this one." in round_line(3, 8, 10)
    assert all(first[a].count(rounds_values(config)) == 1 for a in plan.agents if a != late)
