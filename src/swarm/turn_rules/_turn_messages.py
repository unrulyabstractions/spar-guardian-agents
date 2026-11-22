"""A round's message: the one user message an agent gets at the boundary of each of its rounds, and its readers.

TurnRulesDefense.apply_pre_turn appends it to the activated member's conversation before the round's first model call.
Its parts, each its own paragraph with one blank line between them, in this order:

    round      the round's line (_turn_rounds.open_round), metadata ROUND_TAG {round, of, left, calls}
    presence   who entered and left, and the alerts, holds and pushes due to the agent (_turn_presence.presence_part),
               metadata PRESENCE_TAG {round: the round delivered in, events: [{id, agent, kind, why, round}]}
    notice     what changed on the board since the agent last looked (_turn_rules_filter.TurnRulesDefense
               .boundary_notice), metadata NOTICE_TAG {kind, activation, call, round}

A part with nothing to say is left out. Each part is made only when the conversation lacks it, so a boundary edit ORBIT
rejects loses nothing and nothing comes twice: the next boundary's message holds what is missing. Each part's info
events are written once the message holds it (Part.record): one per part and per presence event, as before.

A run before 2026-11-22 holds the same things as separate user messages: the round's line, one message per presence
event (PRESENCE_TAG {id, agent, kind, why, round}), and the board notice only in what the provider received (an info
event, src/export/atif_transcripts.py rules_prompts_by_output). The readers take a message's metadata or a transcript
step's extra, which keep the same keys, and answer for both shapes.

    parts = message_parts(step["message"], step.get("extra"))
    parts["presence"]["events"][0]["id"]          # "entered:k7q2m"
    presence_events(message.metadata)             # [] for a message with no presence part
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from inspect_ai.model import ChatMessageUser

from src.swarm.turn_rules._turn_state import NOTICE_TAG, PRESENCE_TAG, ROUND_TAG

PARTS = ("round", "presence", "notice")
TAGS = {"round": ROUND_TAG, "presence": PRESENCE_TAG, "notice": NOTICE_TAG}
SEPARATOR = "\n\n"                      # between two parts; no part holds a blank line


@dataclass(frozen=True)
class Part:
    """One part of a round's message: its metadata key (TAGS), its tag, its text, and what to record once the message
    holds it (its info events and the store's marks)."""

    key: str
    tag: dict
    text: str
    record: Callable[[], None]


def round_message(parts: list[Part | None]) -> tuple[ChatMessageUser, list[Part]] | None:
    """The round's message of the parts given (None for a part with nothing to say), in PARTS order, and the parts it
    holds; None when no part is left."""
    order = {tag: i for i, tag in enumerate(TAGS.values())}
    held = sorted((p for p in parts if p is not None), key=lambda p: order[p.key])
    if not held:
        return None
    message = ChatMessageUser(content=SEPARATOR.join(p.text for p in held), metadata={p.key: p.tag for p in held})
    return message, held


def presence_events(tags: Mapping | None) -> list[dict]:
    """The presence events a message holds ({id, agent, kind, why, round} each): its PRESENCE_TAG's events, or the
    one event of a presence message made before 2026-11-22. [] without a presence part."""
    tag = (tags or {}).get(PRESENCE_TAG)
    if not isinstance(tag, Mapping):
        return []
    if isinstance(tag.get("events"), list):
        return [dict(e) for e in tag["events"]]
    return [dict(tag)]


def message_round(tags: Mapping | None) -> dict | None:
    """The round's tag a message holds (ROUND_TAG: {round, of, left, calls}), or None."""
    tag = (tags or {}).get(ROUND_TAG)
    return dict(tag) if isinstance(tag, Mapping) and tag else None


def message_notice(tags: Mapping | None) -> dict | None:
    """The board part's tag a round's message holds (NOTICE_TAG: {kind, activation, call, round}), or None. A notice
    of a run before 2026-11-22 reached the provider only and is no message's part."""
    tag = (tags or {}).get(NOTICE_TAG)
    return dict(tag) if isinstance(tag, Mapping) and tag else None


def message_parts(text: str, tags: Mapping | None) -> dict[str, dict | None]:
    """A user message's parts: {"round": {tag, text}, "presence": {events, text}, "notice": {tag, text}}, None for a
    part it lacks. A message made before 2026-11-22 holds one part, its whole text."""
    present = [p for p in PARTS if (tags or {}).get(TAGS[p])]
    pieces = (text or "").split(SEPARATOR, len(present) - 1) if present else []
    pieces += [""] * (len(present) - len(pieces))
    out: dict[str, dict | None] = dict.fromkeys(PARTS)
    for part, piece in zip(present, pieces):
        if part == "presence":
            out[part] = {"events": presence_events(tags), "text": piece}
        else:
            out[part] = {"tag": dict(tags[TAGS[part]]), "text": piece}
    return out
