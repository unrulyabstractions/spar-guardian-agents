"""The system prompt texts that depend on a setting.

These helpers build the openmath board system prompt, the SWE board fields, what submit says and what it says is
missing, the bullets of the tools that end an agent's work and the sentence on rounds. Each one reads its text from
prompts.yaml and chooses by the run's SwarmConfig and by whether the agent's system prompt tells it of its team
(`told`, Benchmark.tells_team): an agent not told never reads of a teammate.

    prompt = board_system_prompt(config, "k7q2m", config.names)
    text = submit_text("swe", config.submit_kind, config.submit_delay_rounds, {}, told=True)
    lines = ending_lines(config, "worker", tool_line("submit", text), told=True)
"""
from __future__ import annotations

from src.configuration import settings
from src.configuration.swarm_config import SwarmConfig


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


def opening(config: SwarmConfig, name: str, names: list[str]) -> str:
    """The first paragraph of a system prompt we write whole: board_system_prompt, after prompts.yaml `identity` when
    the agent is not told of its team (the team sentence names it otherwise)."""
    identity = None if config.knows_team else settings.load_prompts()["identity"].format(name=name)
    return "\n\n".join(filter(None, [identity, board_system_prompt(config, name, names)]))


def swe_board_fields(config: SwarmConfig) -> dict:
    """The placeholders of prompts.yaml swe.system_shared that depend on the board settings.

    board_paragraph: the "Team chat" paragraph with its two leading newlines, and board_tool_line: the tool bullet
    for the board with its leading newline, both "" without mention_board, so the tool list stays the only hint of
    the board. turns: the last line, on rounds (prompts.yaml rounds.told)."""
    prompts = settings.load_prompts()
    swe = prompts["swe"]
    return {"board_paragraph": swe["board_paragraph"] if config.mention_board else "",
            "board_tool_line": swe["board_tool_line"] if config.mention_board else "",
            "turns": prompts["rounds"]["told"]}


def _pick(entry: str | dict, kind: str) -> str:
    """A domain's text, or of a pair the shared_task team's (kind team) or an agent's own (solo, lone)."""
    return entry if isinstance(entry, str) else entry["team" if kind == "team" else "own"]


def rounds_text(delay: int) -> str:
    """A number of rounds in words: "1 round", "2 rounds"."""
    return f"{delay} round{'' if delay == 1 else 's'}"


def submit_text(domain: str, kind: str, delay: int, values: dict[str, str], told: bool) -> str:
    """What submit does for an agent of this kind (SwarmConfig.submit_kind) and what counts in the domain (its
    Benchmark.key), from prompts.yaml submit; values fill the domain's placeholders. A team's text says when the run
    ends: when the current round ends for delay 0, else delay rounds after it. An agent not told of its team gets
    the `untold` texts."""
    texts = settings.load_prompts()["submit"]
    ends = texts["ends"]["now"] if delay == 0 else \
        texts["ends"]["later" if told else "later_untold"].format(rounds=rounds_text(delay))
    text = texts[kind] if told or kind == "lone" else texts["untold"][kind]
    return text.format(ends=ends, counts=_pick(texts["counts"][domain], kind).format(**values),
                       ready=texts["ready"].get(domain, "").format(**values))


def missing_text(domain: str, kind: str, values: dict[str, str]) -> str:
    """What is missing while the domain has nothing to grade, for an agent of this kind (prompts.yaml
    submit.missing)."""
    return _pick(settings.load_prompts()["submit"]["missing"][domain], kind).format(**values)


def tool_line(tool: str, text: str) -> str:
    """A tool's bullet in a system prompt: "- <tool>: <text>" (prompts.yaml tool_line)."""
    return settings.load_prompts()["tool_line"].format(tool=tool, text=text)


def described(text: str) -> str:
    """A tool's description from its bullet's text: the same text with a capital first letter."""
    return text[:1].upper() + text[1:]


def stop_text(told: bool) -> str:
    """What stop_working does (prompts.yaml stop_working), for an agent told of its team or not."""
    return settings.load_prompts()["stop_working"]["text" if told else "untold"]


def ending_lines(config: SwarmConfig, role: str, submit_line: str, told: bool) -> list[str]:
    """The bullets of the tools that end an agent's work, as an agent of this hidden role holds them: submit_line
    (Benchmark.submit_line) when it holds submit, then stop_working's when it holds that."""
    lines = [submit_line] if config.holds_submit(role) else []
    if config.holds_stop_working(role):
        lines.append(tool_line("stop_working", stop_text(told)))
    return lines


def rounds_line(config: SwarmConfig, told: bool) -> str | None:
    """What a round is (prompts.yaml rounds), for the agents of a shared_task team whose submit ends the run some
    rounds on, since its texts and the board's notice count rounds; None otherwise."""
    if not (config.gives_submit and config.submit_kind == "team"):
        return None
    return settings.load_prompts()["rounds"]["told" if told else "untold"]
