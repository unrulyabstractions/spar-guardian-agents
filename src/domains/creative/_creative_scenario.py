"""A variant's scenario: the sentences that replace CheatBench's interview framing in the instruction an agent reads.

The texts live in src/configuration/prompts.yaml creative_writing.scenarios. `replace` maps each sentence of
CheatBench's rendered instruction to the sentence that stands in its place, `apart` is a team's sentence of the story,
`principal` is whom the work is for, and `words` fills the texts of instruction_levels.yaml.

    told = scenario("commissioned_poem")
    text = scenario_instruction(cheatbench_instruction, "commissioned_poem", team=True)
"""
from __future__ import annotations

from src.configuration import settings
from src.configuration.prompt_texts import fill_apart


def scenario(name: str | None) -> dict | None:
    """A scenario's texts by its name, or None for no scenario."""
    return settings.load_prompts()["creative_writing"]["scenarios"][name] if name else None


def scenario_instruction(instruction: str, name: str | None, *, team: bool = False) -> str:
    """CheatBench's rendered instruction with a scenario's sentences in place of the interview's (its `replace`), and
    its {apart} token filled for a team told of itself (prompt_texts.fill_apart). ValueError when a sentence to replace
    is not there exactly once, so a scenario never leaves one in."""
    told = scenario(name)
    for old, new in (told or {}).get("replace", {}).items():
        if instruction.count(old) != 1:
            raise ValueError(f"the instruction holds {instruction.count(old)} of the sentence {old!r}, not one")
        instruction = instruction.replace(old, new)
    return fill_apart(instruction, told, team)
