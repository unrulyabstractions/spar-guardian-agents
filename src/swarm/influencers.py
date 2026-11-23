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
the role. A level gives one text or one per benchmark, with placeholders filled from the benchmark's `vars`.

A regulator or disregulator level gives a system text with no task detail (`text`) and a first-message text with the
task's own words (`first`, role_first): each regulator level but blind gives the benchmark's `cheating` var, what
cheating means on that task, in the regulator's first message, after its teammates' task; a disregulator's gives its
objective and the work it may reuse, before the run's facts. A variant's scenario may give a role its own words in
place of the benchmark's (Benchmark.level_words, prompts.yaml <task>.scenarios.<name>.<role>). An influencer's level
text is followed by prompts.yaml roles.influencer_common (the peer tools, src/swarm/_regulator_oversight.py, and the
board), then under SwarmConfig.peer_shell roles.peer_shell. A regulator's then gets
roles.regulator_check (call get_peer_actions on each teammate within a set number of the regulator's first rounds:
CHECK_ROUNDS, which role_values gives the first message, since no system prompt holds a number) and, unless
regulator_discloses is on, roles.regulator_no_disclosure. The check is an instruction like the rest; run.json engagement
checked_in_time records whether it happened.

With informed_roles on, a regulator or disregulator also gets the briefing (prompts.yaml `roles.briefing`, built by
role_briefing) after its role text: the board and its tools, what the agents without a role were told, and that they
hold submit; the swarm's size is in its first message (role_values). Workers never get it. No system prompt names a
peer: every agent hears of each peer as it enters (src/swarm/turn_rules/_turn_presence.py).

Both influencers also hold alert_humans (src/swarm/submit_and_stop.py), which records an alert; the caller keeps
working. Under SwarmConfig.submit_hold a regulator also holds hold_submit and release_submit
(src/swarm/submit_hold.py), and its role text gets roles.submit_hold after the check.
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


def level_text(ladder: str, level: str, benchmark_name: str, key: str = "text",
               words: dict[str, str] | None = None) -> str | None:
    """The text of one level of a ladder for one benchmark (its Benchmark.key), or None for no text: the level's
    `text`, or its text for the benchmark; key "first" gives an influencer level's first-message text. `words` are a
    variant's own, which stand in place of the benchmark's `vars` of the same names (Benchmark.level_words)."""
    levels = load_instruction_levels()
    entry = levels[ladder][level]
    template = entry.get(key) if key != "text" or "text" in entry else entry.get(benchmark_name)
    if template is None:
        return None
    values = {**levels["vars"][benchmark_name], **(words or {})}
    values.update({f"{k}__cap": v[:1].upper() + v[1:] for k, v in list(values.items())})
    return SENTENCE_START.sub(r"\1{\2__cap}", template.strip()).format(**values)


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


def role_text(role: str, config: SwarmConfig, benchmark_name: str) -> str | None:
    """The text appended to the system prompt of an agent holding this role, or None."""
    if role not in ROLES:
        raise ValueError(role)
    if role == "worker":
        return None
    text = level_text(role, getattr(config, f"{role}_level"), benchmark_name)
    if text is None:
        return None
    pieces = load_prompts()["roles"]
    tail = [pieces["influencer_common"], *([pieces["peer_shell"]] if config.peer_shell else [])]
    if role == "regulator":
        tail += [pieces["regulator_check"], *([pieces["submit_hold"]] if config.submit_hold else []),
                 *([] if config.regulator_discloses else [pieces["regulator_no_disclosure"]])]
    return " ".join([text, *tail])


def role_first(role: str, config: SwarmConfig, benchmark_name: str, words: dict[str, str] | None = None) -> str | None:
    """What the role's level says in the agent's first message (instruction_levels.yaml `first`): the task's own words
    the system text leaves out, such as what cheating means on the task or the disregulator's objective, or a
    variant's `words` in their place (Benchmark.level_words). None for a worker, a level with no text, or one with no
    first-message text."""
    if role == "worker" or role_text(role, config, benchmark_name) is None:
        return None
    return level_text(role, getattr(config, f"{role}_level"), benchmark_name, key="first", words=words)


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
    round its teammates without a role enter in (first_values.head_start), and under submit_hold a regulator whose role
    text says it can hold a hand-in until the run ends reads the hand-in's delay, briefing or not. [] for a worker."""
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
    if role == "regulator" and config.submit_hold and role_text(role, config, benchmark_name) is not None \
            and (delay := submit_value(config)) and delay not in values:
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


def rule_suffix(config: SwarmConfig, benchmark_name: str) -> str:
    """The rule sentence on its own paragraph after the instruction, or "" at level none."""
    sentence = level_text("rule", config.rule_level, benchmark_name)
    return f"\n{sentence}\n" if sentence else ""


def composition_label(roles: dict[str, str]) -> str:
    counts = {r: sum(1 for v in roles.values() if v == r) for r in ROLES}
    return f"R{counts['regulator']}D{counts['disregulator']}W{counts['worker']}"
