"""The system prompt texts of a board that depend on a setting.

These helpers pick the submit bullet, the openmath board system prompt and the SWE board fields. Each one reads its
text from prompts.yaml and chooses by the run's SwarmConfig.

    from src.configuration.prompt_texts import board_system_prompt
    prompt = board_system_prompt(config, "a01", config.names)
"""
from __future__ import annotations

from src.configuration import settings
from src.configuration.swarm_config import SwarmConfig


def submit_line(team: bool, shutdown: bool) -> str:
    """The SWE prompt's submit bullet with its leading newline, or "" when agents cannot shut down: a team's names
    team/main, a lone or solo_task agent's its own tree."""
    if not shutdown:
        return ""
    return "\n" + settings.load_prompts()["swe"]["submit"]["shared" if team else "solo"]


def board_system_prompt(config: SwarmConfig, name: str, names: list[str]) -> str | None:
    """The openmath system prompt of one agent of a board, before any role text, or None for no prompt of ours:
    prompts.yaml `team` when knows_team, then `board` when mention_board, a blank line apart. A lone agent has
    none."""
    others = [a for a in names if a != name]
    if name not in names or not others:
        return None
    prompts = settings.load_prompts()
    parts = []
    if config.knows_team:
        parts.append(prompts["team"].format(name=name, others=" and ".join(others), n=len(names)))
    if config.mention_board:
        parts.append(prompts["board"])
    return "\n\n".join(parts) or None


def swe_board_fields(config: SwarmConfig, *, team_submit: bool = False) -> dict:
    """The placeholders of prompts.yaml swe.system_shared that depend on the board settings.

    board_paragraph: the "Team chat" paragraph with its two leading newlines, and board_tool_line: the tool bullet
    for the board with its leading newline, both "" without mention_board, so the tool list stays the only hint of
    the board. team_submit_line: the team_submit bullet, or "" when the agent has no such tool. turns: the last
    line, on rounds."""
    swe = settings.load_prompts()["swe"]
    return {"board_paragraph": swe["board_paragraph"] if config.mention_board else "",
            "board_tool_line": swe["board_tool_line"] if config.mention_board else "",
            "team_submit_line": swe["team_submit_line"] if team_submit else "", "turns": swe["turns"]}
