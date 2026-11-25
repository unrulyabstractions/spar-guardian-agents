"""Influencers and instruction levels: workers and the influencers, regulators and disregulators, as peers on one
board. An influencer's role is hidden from the workers.

A swarm is the board (src/swarm/board.py) with N agents whose system prompts differ by role:

    worker        the plain board prompt
    regulator     a peer that oversees the team and does not do the task, at the configured regulator level
    disregulator  a peer privately told to get the result by any means, at the configured disregulator level

Regulators are not monitors. They have the same standing as everyone else, and nothing is enforced at a boundary. Their
tools differ: both influencers also hold the peer tools (get_peer_actions, get_peer_observations), and a regulator holds
neither submit nor stop_working. It keeps the task's tools, so it can look at what its machine shows. A regulator has no
deliverable. Its system prompt is its own in every domain (oversight_prompt, in place of Benchmark.system_prompts), and
its first message holds the task as what its teammates do (oversight_first_message, in place of the task instruction).
At level none a regulator keeps the task's prompt and first message (SwarmConfig.oversees). Roles hide behind the drawn
agent IDs (SwarmConfig.names) and are recorded in the sample metadata.

Every model-facing text of a level comes from src/configuration/instruction_levels.yaml: the rule sentence appended to
the task instruction, and the regulator and disregulator texts appended to the system prompt of the agents holding
the role. A level gives one text or one per benchmark, with placeholders filled from the benchmark's `vars`. A var
may name a value of the task in braces, such as a folder, which the task gives (Benchmark.level_values).

A regulator or disregulator level gives a system text with no task detail (`text`) and a first-message text with the
task's own words (`first`, role_first). Each ladder is none, generic and instructed. A regulator at instructed gets the
benchmark's `cheating` var, what cheating means on that task, in its first message, after its teammates' task; at
generic it gets no first-message text. A disregulator's first message gives its objective at generic, and at
instructed also where the work that already solves the task is, before the run's facts. A variant's scenario may give
its own words in place of the benchmark's, for the rule sentence and both roles' first messages (Benchmark.level_words,
prompts.yaml <task>.scenarios.<name>.words). A disregulator's level text is followed by prompts.yaml
roles.influencer_common (the peer tools, src/swarm/_regulator_oversight.py, and the board), then under
SwarmConfig.peer_shell roles.peer_shell. A regulator's level text is sections of bullets, some lines of which hold
under a condition of the run's config alone (REGULATOR_LINES, shown_lines), then a last section, "Your tools:"
(regulator_tools): one bullet per sentence of the same two pieces, then roles.regulator_check (call get_peer_actions
on each teammate within a set number of the regulator's first rounds: CHECK_ROUNDS, which role_values gives the first
message, since no system prompt holds a number). The check is an instruction like the rest; run.json engagement
checked_in_time records whether it happened. In a run with two or more regulators each one's first message names the
others (same_role_text).

With informed_roles on, a regulator or disregulator also gets the briefing (prompts.yaml `roles.briefing`, built by
role_briefing) after its role text: the board and its tools, what the agents without a role were told, and that they
hold submit; the swarm's size is in its first message (role_values). Workers never get it. No system prompt names a
peer: every agent hears of each peer as it enters (src/swarm/turn_rules/_turn_presence.py).

Both influencers also hold alert_humans (src/swarm/submit_and_stop.py), which records an alert; the caller keeps
working. Neither holds unsubmit (src/swarm/unsubmit.py). A regulator's text names it when workers hold it, to get a
teammate to take cheated work back; a disregulator's never does.

In a run with two or more disregulators each one finds OTHERS.md on its own machine or account, naming the other
disregulators (others_named, others_text, prompts.yaml roles.others). No prompt points at it. Each domain says where
an agent's private file goes (Benchmark.private_dir) and plants it (Benchmark.others_files).
"""
from __future__ import annotations

import random
import re

from src.configuration.prompt_texts import first_value, rounds_text, submit_value
from src.configuration.settings import load_instruction_levels, load_prompts
from src.configuration.swarm_config import ROLES, SwarmConfig
from src.swarm.turn_rules import BOARD_TOOLS, CHECK_ROUNDS

# A placeholder that opens a sentence; its value is capitalised there.
SENTENCE_START = re.compile(r"(^|[.!?]\s+)\{(\w+)\}")
# A line of a regulator's level text shown only under a condition: [tag] then the line (instruction_levels.yaml).
TAGGED = re.compile(r"^\[(\w+)\](.*)$")
# Where one sentence ends and the next begins, in a piece of prompts.yaml roles made into bullets.
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class Unfilled(dict):
    """The task's values for its vars; a value it does not give stays as its placeholder."""

    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


def level_vars(benchmark_name: str, values: dict[str, str] | None = None) -> dict[str, str]:
    """The benchmark's `vars` of instruction_levels.yaml with the task's values in their placeholders
    (Benchmark.level_values); a placeholder whose value is not given stays."""
    filled = Unfilled(values or {})
    return {k: v.format_map(filled) for k, v in load_instruction_levels()["vars"][benchmark_name].items()}


def level_text(ladder: str, level: str, benchmark_name: str, key: str = "text",
               words: dict[str, str] | None = None, values: dict[str, str] | None = None) -> str | None:
    """The text of one level of a ladder for one benchmark (its Benchmark.key), or None for no text: the level's
    `text`, or its text for the benchmark; key "first" gives an influencer level's first-message text. `words` are a
    variant's own (Benchmark.level_words): they stand in place of the benchmark's `vars` of the same names, and a
    level that holds a `scenario` text gives that in place of the benchmark's. {generic} is the generic level's text of
    the same ladder, for a level built on it: the rule's generic sentence in the rule's scenario text, the regulator's
    or the disregulator's generic text in its instructed text. `values` fill the placeholders of the benchmark's
    `vars` (Benchmark.level_values); a var whose value is not given keeps its placeholder."""
    levels = load_instruction_levels()
    entry = levels[ladder][level]
    own = "scenario" if words and "scenario" in entry else benchmark_name
    template = entry.get(key) if key != "text" or "text" in entry else entry.get(own)
    if template is None:
        return None
    filled = {"generic": levels[ladder]["generic"]["text"].strip(), **level_vars(benchmark_name, values),
              **(words or {})}
    filled.update({f"{k}__cap": v[:1].upper() + v[1:] for k, v in list(filled.items())})
    return SENTENCE_START.sub(r"\1{\2__cap}", template.strip()).format(**filled)


def assign_roles(seats: list[str], regulators: int, disregulators: int, seed: int = 0) -> dict[str, str]:
    """Each agent's role (worker, regulator or disregulator), sorted by agent, from counts; the seed fixes
    which seats get which role."""
    if regulators + disregulators > len(seats):
        raise ValueError("more regulators and disregulators than agents")
    order = list(seats)
    random.Random(seed).shuffle(order)
    roles = {name: "worker" for name in sorted(seats)}
    for name in order[:regulators]:
        roles[name] = "regulator"
    for name in order[regulators:regulators + disregulators]:
        roles[name] = "disregulator"
    return roles


def at_instructed(config: SwarmConfig) -> bool:
    """Whether the regulator's level is instructed, whose first message says what cheating means on the task."""
    return config.regulator_level == "instructed"


def regulator_kept_out(config: SwarmConfig) -> bool:
    """Whether a regulator may not write the team's shared result (SwarmConfig.shared_result_kept_out)."""
    return "regulator" in config.shared_result_kept_out


def several_regulators(config: SwarmConfig) -> bool:
    """Whether the run has two or more regulators, so each one's first message names the others (same_role_text)."""
    return config.regulator_count >= 2


def hides_role(config: SwarmConfig) -> bool:
    """Whether a regulator may not tell its peers of its role (regulator_discloses off)."""
    return not config.regulator_discloses


def workers_hold_unsubmit(config: SwarmConfig) -> bool:
    """Whether the workers hold unsubmit, to take the team's hand-in back (SwarmConfig.gives_unsubmit)."""
    return config.holds_unsubmit("worker")


REGULATOR_LINES = {"instructed": at_instructed, "kept_out": regulator_kept_out,
                   "several_regulators": several_regulators, "hides_role": hides_role,
                   "unsubmit": workers_hold_unsubmit}
"""The tags of a regulator's level text (instruction_levels.yaml), each with the condition of the run's config under
which its lines are shown."""


def shown_lines(text: str, config: SwarmConfig) -> str:
    """A regulator's level text as the run shows it: each line that opens with spaces joined to the line before it by
    one space, then each line tagged [tag] kept, without its tag, when REGULATOR_LINES[tag] holds for the config, and
    dropped when it does not. An unknown tag raises ValueError."""
    lines: list[str] = []
    for line in text.splitlines():
        if line[:1].isspace() and lines and lines[-1]:
            lines[-1] = f"{lines[-1]} {line.strip()}"
        else:
            lines.append(line)
    shown = []
    for line in lines:
        tagged = TAGGED.match(line)
        if tagged is None:
            shown.append(line)
            continue
        if tagged[1] not in REGULATOR_LINES:
            raise ValueError(f"instruction_levels.yaml regulator: no condition for the tag [{tagged[1]}]")
        if REGULATOR_LINES[tagged[1]](config):
            shown.append(tagged[2])
    return "\n".join(shown)


def regulator_tools(config: SwarmConfig) -> str:
    """The last section of a regulator's role text: prompts.yaml roles.regulator_tools, then one bullet per sentence
    of roles.influencer_common, roles.peer_shell under SwarmConfig.peer_shell, and roles.regulator_check."""
    pieces = load_prompts()["roles"]
    sentences = [*SENTENCE_END.split(pieces["influencer_common"].strip()),
                 *([pieces["peer_shell"].strip()] if config.peer_shell else []), pieces["regulator_check"].strip()]
    return "\n".join([pieces["regulator_tools"], *(f"- {sentence}" for sentence in sentences)])


def role_text(role: str, config: SwarmConfig, benchmark_name: str) -> str | None:
    """The text appended to the system prompt of an agent holding this role, or None. A regulator's is its level's
    lines as the run shows them (shown_lines), then its tools' section (regulator_tools), a blank line apart. A
    disregulator's is its level's text, then roles.influencer_common and, under peer_shell, roles.peer_shell, a space
    apart."""
    if role not in ROLES:
        raise ValueError(role)
    if role == "worker":
        return None
    text = level_text(role, getattr(config, f"{role}_level"), benchmark_name)
    if text is None:
        return None
    if role == "regulator":
        return f"{shown_lines(text, config)}\n\n{regulator_tools(config)}"
    pieces = load_prompts()["roles"]
    return " ".join([text, pieces["influencer_common"], *([pieces["peer_shell"]] if config.peer_shell else [])])


def role_first(role: str, config: SwarmConfig, benchmark_name: str, words: dict[str, str] | None = None,
               values: dict[str, str] | None = None) -> str | None:
    """What the role's level says in the agent's first message (instruction_levels.yaml `first`): the task's own words
    the system text leaves out, such as what cheating means on the task or the disregulator's objective, or a
    variant's `words` in their place (Benchmark.level_words), the task's `values` in its vars (Benchmark.level_values).
    None for a worker, a level with no text, or one with no first-message text."""
    if role == "worker" or role_text(role, config, benchmark_name) is None:
        return None
    return level_text(role, getattr(config, f"{role}_level"), benchmark_name, key="first", words=words, values=values)


def role_briefing(config: SwarmConfig, names: list[str]) -> str | None:
    """prompts.yaml roles.briefing for a regulator or disregulator, or None when informed_roles is off or there is no
    board. It names no agent and gives no number (the swarm's size and the hand-in's delay are role_values, in the
    first message), names the board's tools, says what agents without a role were told (roles.workers_told) and, when
    they hold submit, what it does and when a team's run then ends (roles.submit_held, submit_ends). Workers never get
    it."""
    if not config.informed_roles or not config.board:
        return None
    roles = load_prompts()["roles"]
    told = {(False, False): "nothing", (True, False): "team", (False, True): "board", (True, True): "both"}
    tools = ", ".join(BOARD_TOOLS[:-1]) + " and " + BOARD_TOOLS[-1]
    text = roles["briefing"].format(tools=tools,
                                    told=roles["workers_told"][told[config.knows_team, config.mention_board]])
    if not config.gives_submit:
        return text
    ends = roles["submit_ends"]["now" if config.submit_delay_rounds == 0 else "later"]
    return f"{text} {roles['submit_held'][config.submit_kind].format(ends=ends)}"


def role_values(role: str, config: SwarmConfig, benchmark_name: str, names: list[str]) -> list[str]:
    """The values of the parameters the role's text and briefing (role_prompt) state with no number, for the first
    message (prompts.yaml first_values): a regulator's check rounds (CHECK_ROUNDS, after a head start's rounds, since
    its teammates enter after them), and with the briefing the agents' count and, when it says a team's hand-in ends
    the run a set number of rounds later, that number. Under head_start_rounds an agent with the head start reads the
    round its teammates without a role enter in (first_values.head_start). [] for a worker."""
    values = []
    head = config.head_start_rounds if role != "worker" and config.holds_head_start(role) else 0
    if head:
        values.append(first_value("head_start", round=head + 1))
    if role == "regulator" and role_text(role, config, benchmark_name) is not None:
        values.append(first_value("check", rounds=rounds_text(CHECK_ROUNDS + head)))
    if role != "worker" and role_briefing(config, names) is not None:
        values.append(first_value("agents", n=len(names)))
        if config.gives_submit and (delay := submit_value(config)):
            values.append(delay)
    return values


def role_prompt(role: str, config: SwarmConfig, benchmark_name: str, names: list[str]) -> str | None:
    """Everything a role adds to the system prompt: the role text, then the briefing for a regulator or a
    disregulator when informed_roles is on, a blank line apart. None for a worker."""
    if role == "worker":
        return None
    parts = [role_text(role, config, benchmark_name), role_briefing(config, names)]
    return "\n\n".join(p for p in parts if p) or None


def oversight_prompt(name: str, names: list[str], added: str | None, note: str | None = None,
                     rules: str | None = None) -> str:
    """A regulator's whole system prompt, a blank line apart: prompts.yaml roles.regulator_opening, which names no peer,
    the domain's note on how the access is dealt, which names no peer either (Benchmark.access_note), what its role
    adds (role_prompt) and the rounds' rules (board.round_rules). Who holds what reaches it in its first message,
    inside its teammates' (oversight_first_message)."""
    opening = load_prompts()["roles"]["regulator_opening"].format(name=name)
    return "\n\n".join(filter(None, [opening, note, added, rules]))


def oversight_first_message(task: str) -> str:
    """A regulator's first message: prompts.yaml roles.regulator_task around the first message its teammates get."""
    return load_prompts()["roles"]["regulator_task"].format(task=task)


def with_role(prompt: str | None, text: str | None) -> str | None:
    """The system prompt, then the role text after a blank line. Without a prompt of ours the role text stands
    alone."""
    if not text:
        return prompt
    return f"{prompt}\n\n{text}" if prompt else text


def rule_suffix(config: SwarmConfig, benchmark_name: str, words: dict[str, str] | None = None,
                values: dict[str, str] | None = None) -> str:
    """The rule sentence on its own paragraph after the instruction, or "" at level none; `words` are a variant's own
    (Benchmark.level_words), `values` the task's for its vars (Benchmark.level_values)."""
    sentence = level_text("rule", config.rule_level, benchmark_name, words=words, values=values)
    return f"\n{sentence}\n" if sentence else ""


def composition_label(roles: dict[str, str]) -> str:
    counts = {r: sum(1 for v in roles.values() if v == r) for r in ROLES}
    return f"R{counts['regulator']}D{counts['disregulator']}W{counts['worker']}"


def others_named(roles: dict[str, str] | None) -> dict[str, list[str]]:
    """{disregulator: the other disregulators, sorted} in a run with two or more disregulators; {} otherwise. Each
    entry is what that disregulator's OTHERS.md names (others_text). A lone disregulator, a worker and a regulator get
    none."""
    found = sorted(agent for agent, role in (roles or {}).items() if role == "disregulator")
    if len(found) < 2:
        return {}
    return {agent: [other for other in found if other != agent] for agent in found}


def kept_out(roles: dict[str, str] | None, agents: list[str], kept_roles: tuple[str, ...]) -> list[str]:
    """The agents whose role is one of kept_roles (SwarmConfig.shared_result_kept_out), in roster order. Every domain
    with a shared result enforces it in its own sandbox (Benchmark.kept_out) and records it in its block. Only a new
    run reads this; a stored run's record says whom it kept out."""
    return [agent for agent in agents if (roles or {}).get(agent) in kept_roles]


def listed(names: list[str]) -> str:
    """Agent IDs as "a", "a and b" or "a, b and c"."""
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def others_text(names: list[str]) -> str:
    """The whole text of OTHERS.md for a disregulator, from prompts.yaml roles.others: the other disregulators' IDs,
    as "a", "a and b" or "a, b and c", then a newline."""
    if not names:
        raise ValueError("OTHERS.md names at least one other disregulator")
    templates = load_prompts()["roles"]["others"]
    return templates["one" if len(names) == 1 else "several"].format(names=listed(names)) + "\n"


def same_role_text(roles: dict[str, str] | None, agent: str) -> str | None:
    """The line of a regulator's first message that names the other regulators, sorted, from prompts.yaml
    roles.same_role, in a run with two or more regulators. None for a worker, a disregulator or a lone regulator."""
    found = sorted(other for other, role in (roles or {}).items() if role == "regulator")
    if agent not in found or len(found) < 2:
        return None
    others = [other for other in found if other != agent]
    return load_prompts()["roles"]["same_role"]["one" if len(others) == 1 else "several"].format(names=listed(others))
