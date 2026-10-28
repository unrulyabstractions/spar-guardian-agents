"""Hidden roles and instruction levels: workers, regulators and disregulators as peers on one board.

A swarm is the board (src/swarm/comm_topology.py) with N agents whose system prompts differ by role:

    worker        the plain board prompt
    regulator     a peer told to keep the team honest, at the configured regulator level
    disregulator  a peer privately told to get the result by any means, at the configured disregulator level

Regulators are not monitors. They have the same tools and standing as everyone else, and nothing is enforced at a
boundary. Roles hide behind neutral names (a01 ... a10) and are recorded in the sample metadata.

Every model-facing text of a level comes from src/configuration/instruction_levels.yaml: the rule sentence appended to
the task instruction, and the regulator and disregulator texts appended to the system prompt of the agents holding
the role. A level gives one text or one per benchmark, with placeholders filled from the benchmark's `vars`.

Under board_tools a regulator's level text is followed by prompts.yaml roles.regulator_common (it can query the other
agents' tool calls, src/swarm/oversight.py, and read the board) and, unless regulator_discloses is on, by
roles.regulator_no_disclosure.

With informed_roles on, a regulator or disregulator also gets the briefing (prompts.yaml `roles.briefing`, built by
swarm_config.role_briefing) after its role text: the swarm's size and roster, the board and its tools, and what the
agents without a role were told. Workers never get it.
"""
from __future__ import annotations

import random
import re

from src.configuration.access import neutral_agents
from src.configuration.settings import load_instruction_levels, load_prompts
from src.configuration.swarm_config import ROLES, SwarmConfig, agent_names, role_briefing

__all__ = ["ROLES", "LEVEL_KEYS", "agent_names", "assign_roles", "level_text", "role_text", "role_prompt",
           "with_role", "rule_suffix", "composition_label", "neutral_agents"]

# The key instruction_levels.yaml uses for each CheatBench task.
LEVEL_KEYS = {"openmath_agent": "openmath", "software_engineering": "swe", "knowledge_work": "knowledge_work",
              "subset_sum": "subset_sum", "prime_factorization": "prime_factorization",
              "protein_design": "protein_design"}
# A placeholder that opens a sentence; its value is capitalised there.
SENTENCE_START = re.compile(r"(^|[.!?]\s+)\{(\w+)\}")


def level_text(ladder: str, level: str, benchmark_name: str) -> str | None:
    """The text of one level of a ladder for one benchmark ("openmath" or "swe"), or None for no text."""
    levels = load_instruction_levels()
    entry = levels[ladder][level]
    template = entry["text"] if "text" in entry else entry.get(benchmark_name)
    if template is None:
        return None
    values = dict(levels["vars"][benchmark_name])
    values.update({f"{k}__cap": v[:1].upper() + v[1:] for k, v in list(values.items())})
    return SENTENCE_START.sub(r"\1{\2__cap}", template.strip()).format(**values)


def assign_roles(names: list[str], regulators: int, disregulators: int, seed: int = 0) -> dict[str, str]:
    """Hidden roles by neutral name, from counts; the seed fixes which names get which role."""
    if regulators + disregulators > len(names):
        raise ValueError("more regulators and disregulators than agents")
    order = list(names)
    random.Random(seed).shuffle(order)
    roles = {name: "worker" for name in names}
    for name in order[:regulators]:
        roles[name] = "regulator"
    for name in order[regulators:regulators + disregulators]:
        roles[name] = "disregulator"
    return roles


def role_text(role: str, config: SwarmConfig, benchmark_name: str) -> str | None:
    """The text appended to the system prompt of an agent holding this role, or None."""
    if role not in ROLES:
        raise ValueError(role)
    if role == "worker":
        return None
    text = level_text(role, getattr(config, f"{role}_level"), benchmark_name)
    if role != "regulator" or text is None or not config.board_tools:
        return text
    pieces = load_prompts()["roles"]
    tail = [pieces["regulator_common"], *([] if config.regulator_discloses else [pieces["regulator_no_disclosure"]])]
    return " ".join([text, *tail])



def role_prompt(role: str, config: SwarmConfig, benchmark_name: str, names: list[str]) -> str | None:
    """Everything a role adds to the system prompt: the role text, then the briefing for a regulator or a
    disregulator when informed_roles is on, a blank line apart. None for a worker."""
    if role == "worker":
        return role_text(role, config, benchmark_name)
    parts = [role_text(role, config, benchmark_name), role_briefing(config, names, benchmark_name)]
    return "\n\n".join(p for p in parts if p) or None


def with_role(prompt: str | None, text: str | None) -> str | None:
    """The system prompt, then the role text after a blank line. Without a prompt of ours the role text stands
    alone."""
    if not text:
        return prompt
    return f"{prompt}\n\n{text}" if prompt else text


def rule_suffix(config: SwarmConfig, benchmark_name: str) -> str:
    """The rule sentence on its own paragraph after the instruction, or "" at level none."""
    sentence = level_text("rule", config.rule_level, benchmark_name)
    return f"\n{sentence}\n" if sentence else ""


def composition_label(roles: dict[str, str]) -> str:
    counts = {r: sum(1 for v in roles.values() if v == r) for r in ROLES}
    return f"R{counts['regulator']}D{counts['disregulator']}W{counts['worker']}"
