"""The system prompt texts that depend on a setting.

These helpers build the board system prompt, the SWE board fields, what submit says and what it says is
missing, the bullets of the tools that end an agent's work, the facts block that ends every first message
(with_facts) and the values of the parameters a system prompt states with no number (first_value, submit_value). The
rounds' rules in the system prompt name the agent's board calls, so they live with the board (src/swarm/board.py
round_rules). Each one reads its text from prompts.yaml and chooses by the run's SwarmConfig and by whether the
agent's system prompt tells it of its team (`told`, Benchmark.tells_team): an agent not told never reads of a teammate.

    prompt = board_system_prompt(config, "k7q2m", config.names)
    text = submit_text(config.submit_kind, config.submit_delay_rounds, told=True)
    lines = ending_lines(config, "worker", tool_line("submit", text), told=True)
"""
from __future__ import annotations

from src.configuration import settings
from src.configuration.swarm_config import SwarmConfig


def board_system_prompt(config: SwarmConfig, name: str, names: list[str]) -> str | None:
    """The system prompt of one agent of a board, before any role text, or None for no prompt of ours: prompts.yaml
    `team` when knows_team. It names no peer: peers are announced as they enter (turn_rules presence). The board is no
    sentence of it: it is a bullet of the tool list (board_line). A lone agent has none."""
    if name not in names or len(names) < 2 or not config.knows_team:
        return None
    return f"{settings.load_prompts()['team'].format(name=name)} {together(config)}"


def together(config: SwarmConfig) -> str:
    """What an agent told of its team reads after its team sentence (prompts.yaml `together`): that peers may not hold
    the same files or access, to communicate and coordinate, to say on the board what it does and finds and read its
    peers first (only with the board's tools, mention_board), and to ask and give. One paragraph with the team
    sentence, in every task."""
    texts = settings.load_prompts()["together"]
    board = texts["board"] if config.mention_board else None
    return " ".join(filter(None, [texts["apart"], texts["peers"], board, texts["exchange"]]))


APART = "{apart}"
"""The token a scenario's story text holds where its `apart` sentence goes (prompts.yaml <task>.scenarios.<name>)."""


def fill_apart(text: str, told: dict | None, team: bool) -> str:
    """A scenario's story text with its token APART filled: a space and the scenario's `apart` sentence for a team
    told of itself (Benchmark.tells_team), nothing for a lone agent or an agent not told of its team. Every reader of
    a story text goes through here, so no agent reads the token. ValueError when a scenario with an `apart` sentence
    gives a text without the token or with it more than once, or when a text holds the token and no sentence fills
    it."""
    apart = (told or {}).get("apart")
    found = text.count(APART)
    if found != (1 if apart else 0):
        raise ValueError(f"the text holds {found} of the token {APART}, not {1 if apart else 0}: {text!r}")
    return text.replace(APART, f" {apart}" if team and apart else "")


def board_line(config: SwarmConfig, name: str, names: list[str]) -> str | None:
    """The board's tool bullet for one agent of a board when mention_board is on (prompts.yaml `board`), listed with
    the other tools' bullets; None for a lone agent or with mention_board off."""
    if name not in names or len(names) < 2 or not config.mention_board:
        return None
    return settings.load_prompts()["board"]


def opening(config: SwarmConfig, name: str, names: list[str]) -> str:
    """The first paragraph of a system prompt we write whole: board_system_prompt, after prompts.yaml `identity` when
    the agent is not told of its team (the team sentence names it otherwise)."""
    identity = None if config.knows_team else settings.load_prompts()["identity"].format(name=name)
    return "\n\n".join(filter(None, [identity, board_system_prompt(config, name, names)]))


def swe_board_fields(config: SwarmConfig) -> dict:
    """The placeholders of prompts.yaml swe.system_shared that depend on the board settings.

    board_tool_line: the tool bullet for the board with its leading newline, "" without mention_board."""
    swe = settings.load_prompts()["swe"]
    return {"board_tool_line": swe["board_tool_line"] if config.mention_board else ""}


def _pick(entry: str | dict, kind: str) -> str:
    """A domain's text, or of a pair the shared_task team's (kind team) or an agent's own (solo, lone)."""
    return entry if isinstance(entry, str) else entry["team" if kind == "team" else "own"]


def rounds_text(delay: int) -> str:
    """A number of rounds in words: "1 round", "2 rounds"."""
    return f"{delay} round{'' if delay == 1 else 's'}"


def submit_text(kind: str, delay: int, told: bool, unsubmit: bool = False) -> str:
    """What submit does for an agent of this kind (SwarmConfig.submit_kind), from prompts.yaml submit, with no task
    detail: what counts is counts_text, in the first message. A team's text says when the run ends: when the current
    round ends for delay 0, else a set number of rounds after it, with no number (the number is submit_value, in the
    first message). An agent not told of its team gets the `untold` texts. Under unsubmit (SwarmConfig.gives_unsubmit)
    a team's text is submit.takes_back's, which says a teammate can take the hand-in back."""
    texts = settings.load_prompts()["submit"]
    if unsubmit and kind == "team":
        return texts["takes_back"]["team" if told else "untold"]
    ends = texts["ends"]["now"] if delay == 0 else texts["ends"]["later" if told else "later_untold"]
    text = texts[kind] if told or kind == "lone" else texts["untold"][kind]
    return text.format(ends=ends)


def counts_text(domain: str, kind: str, values: dict[str, str]) -> str:
    """What counts at a submit in the domain (its Benchmark.key), for an agent of this kind, for the first message
    (prompts.yaml submit.counted); values fill the domain's placeholders."""
    texts = settings.load_prompts()["submit"]
    return texts["counted"].format(counts=_pick(texts["counts"][domain], kind).format(**values))


def overseen_counts_text(domain: str, kind: str, values: dict[str, str]) -> str:
    """What counts when a regulator's teammates hand in their work (prompts.yaml roles.regulator_counts), for a team of
    this kind (SwarmConfig.submit_kind): the team's counts text, or each teammate's own (submit.counts_teammate where
    the domain has one); values fill the domain's placeholders."""
    prompts = settings.load_prompts()
    submit = prompts["submit"]
    team = kind == "team"
    entry = submit["counts"][domain] if team or domain not in submit["counts_teammate"] \
        else submit["counts_teammate"][domain]
    return prompts["roles"]["regulator_counts"]["team" if team else "solo"].format(
        counts=_pick(entry, kind).format(**values))


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


def unsubmit_text(told: bool) -> str:
    """What unsubmit does (prompts.yaml unsubmit), for an agent told of its team or not: its bullet's text."""
    return settings.load_prompts()["unsubmit"]["text" if told else "untold"]


def stop_text(told: bool) -> str:
    """What stop_working does (prompts.yaml stop_working), for an agent told of its team or not."""
    return settings.load_prompts()["stop_working"]["text" if told else "untold"]


def ending_lines(config: SwarmConfig, role: str, submit_line: str, told: bool) -> list[str]:
    """The bullets of the tools that end an agent's work, as an agent of this role holds them: submit_line
    (Benchmark.submit_line) when it holds submit, then unsubmit's when it holds that (SwarmConfig.holds_unsubmit),
    then stop_working's when it holds that."""
    lines = [submit_line] if config.holds_submit(role) else []
    if config.holds_unsubmit(role):
        lines.append(tool_line("unsubmit", unsubmit_text(told)))
    if config.holds_stop_working(role):
        lines.append(tool_line("stop_working", stop_text(told)))
    return lines


def scenario_words(told: dict | None, **values: str) -> dict[str, str]:
    """A scenario's own words for the texts of instruction_levels.yaml (prompts.yaml <task>.scenarios.<name>.words),
    with the task's values filled in. They stand in place of the task's `vars` of the same names in the rule sentence
    and in an influencer's first message (influencers.level_text). {} without a scenario or without such words."""
    return {key: text.format(**values) for key, text in ((told or {}).get("words") or {}).items()}


def first_value(name: str, **values) -> str:
    """One parameter's value for the first message (prompts.yaml first_values), the number a system prompt leaves
    out."""
    return settings.load_prompts()["first_values"][name].format(**values)


def submit_value(config: SwarmConfig) -> str | None:
    """When a team's hand-in ends the run, for an agent whose system prompt says a set number of rounds after it: the
    number (first_values.submit). None when a submit hands in no team's work or ends the run with its round."""
    if config.submit_kind != "team" or config.submit_delay_rounds == 0:
        return None
    return first_value("submit", rounds=rounds_text(config.submit_delay_rounds))


def counted(n: int | float, noun: str) -> str:
    """A count in words: "1 round", "30 rounds", "1 tool call"; noun is the singular."""
    return f"{n:g} {noun}{'' if n == 1 else 's'}"


def facts_block(facts: list[str]) -> str:
    """The run's facts as the last paragraph of a first message: prompts.yaml `facts`, then one labelled line per fact
    ("- What counts when you submit: ...")."""
    return "\n".join([settings.load_prompts()["facts"], *(f"- {fact}" for fact in facts)])


def split_facts(message: str) -> tuple[str, list[str]]:
    """A first message without its facts block, and the block's facts (facts_block), [] when it has none."""
    heading = f"\n\n{settings.load_prompts()['facts']}\n"
    cut = message.rfind(heading)
    if cut < 0:
        return message, []
    lines = message[cut + len(heading):].strip().splitlines()
    return message[:cut], [line[2:] for line in lines if line.startswith("- ")]


def with_facts(message: str, facts: list[str] | None = None, note: str | None = None) -> str:
    """A first message in its shape: the task, then the note on a paragraph of its own, then one facts block, last,
    with the facts it held and these after them, none twice."""
    body, held = split_facts(message)
    merged = list(dict.fromkeys([*held, *(f for f in facts or [] if f)]))
    parts = [body.strip("\n"), note.strip() if note else None, facts_block(merged) if merged else None]
    return "\n\n".join(p for p in parts if p) + "\n"


def joined(names: list[str]) -> str:
    """Names in a sentence: "a", "a and b", "a, b and c"."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
