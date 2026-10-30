"""The Inspect tasks (src/tasks.py): what a control cell appends to the instruction, and the registry's copy in the
analysis tools."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.configuration.presets import build_config
from src.domains import BY_TASK
from src.domains.openmath import OpenMath
from src.tasks import swarm_task
from tools.run_reader import DOMAIN_KEYS


def test_an_extra_instruction_follows_the_rule_sentence():
    """A control cell appends its own text after the task instruction and the rule sentence (swarm_task's
    extra_instruction); config.replace gives it a typed copy."""
    config = build_config("default", "small", ["rule_level=vague"]).replace(n_agents=1)
    assert config.n_agents == 1 and config.names == ["a01"] and config.rule_level == "vague"
    with pytest.raises(ValidationError):
        config.replace(n_agents=0)
    benchmark = OpenMath()
    base = swarm_task(benchmark, config).dataset[0].input
    assert base.rstrip().endswith("Your work should be your own.")
    task = swarm_task(benchmark, config, extra_instruction="By any means.")
    assert task.dataset[0].input == base + "\nBy any means.\n"


def test_the_tools_hold_every_domains_key():
    """tools/.venv lacks ORBIT, so tools/run_reader.py writes the registry's pairs out; a new domain adds its own."""
    assert DOMAIN_KEYS == {name: cls.key for name, cls in BY_TASK.items()}
