"""The model-facing texts that depend on a setting.

These helpers pick the submit bullet, the openmath board system prompt, the SWE board fields, the role briefing and
the board notice. Each one reads its text from prompts.yaml and chooses by the run's SwarmConfig. src/benchmark/ and
src/swarm/ may import them, since this package imports nothing from src/ outside src/configuration/.

    from src.configuration.prompt_texts import notice_text, role_briefing
    text = role_briefing(config, config.names, "swe")
"""
from __future__ import annotations

from src.configuration import settings
from src.configuration.settings import BOARD_TOOLS
from src.configuration.swarm_config import SwarmConfig


def submit_line(team: bool, shutdown: bool) -> str:
    """The SWE prompt's submit bullet with its leading newline, or "" when agents cannot shut down: a team's names
    team/main, a lone or solo_task agent's its own tree."""
    if not shutdown:
        return ""
    return "\n" + settings.load_prompts()["swe"]["submit"]["shared" if team else "solo"]


def board_system_prompt(config: SwarmConfig, name: str, names: list[str], *, workdir: str,
                        domain: str = "openmath") -> str | None:
    """The openmath system prompt of one agent of a board, before any role text, or None for no prompt of ours:
    prompts.yaml `team` when knows_team, then `board` when mention_board, a blank line apart. A lone agent has
    none."""
    others = [a for a in names if a != name]
    if name not in names or not others:
        return None
    prompts = settings.load_prompts()
    parts = []
    if config.knows_team(domain):
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


def role_briefing(config: SwarmConfig, names: list[str], domain: str) -> str | None:
    """prompts.yaml roles.briefing for a regulator or disregulator, or None when informed_roles is off or there is no
    board. It says the swarm's size and roster, names the board's tools and says what agents without a role were told
    (roles.workers_told). Workers never get it."""
    if not config.informed_roles or not config.board:
        return None
    roles = settings.load_prompts()["roles"]
    told = {(False, False): "nothing", (True, False): "team", (False, True): "board", (True, True): "both"}
    return roles["briefing"].format(n=len(names), roster=", ".join(names), tools=_and_list(BOARD_TOOLS),
                                    told=roles["workers_told"][told[config.knows_team(domain), config.mention_board]])


def notice_text(kind: str, counts: dict[str, tuple[int, int]] | None = None) -> str | None:
    """The board notice (prompts.yaml notice) for notice=kind, or None for "none".

    counts maps each sender with new posts to (new, total), in the order to print."""
    if kind == "none":
        return None
    notice = settings.load_prompts()["notice"]
    if kind == "changed":
        return notice["changed"]
    lines = "; ".join(notice["count"].format(sender=sender, new=new, total=total)
                      for sender, (new, total) in (counts or {}).items())
    return notice["counts"].format(counts=lines)


def _and_list(items) -> str:
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
