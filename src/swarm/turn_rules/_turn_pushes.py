"""Push notices: every influencer hears each change of the team's shared result as a durable user message.

A benchmark with a shared deliverable on a team remote (Benchmark.team_head: SWE's team/main, openmath's and protein's
main of the team remote) gives the turn rules a reader of its head, which the harness runs itself, as root, never
through anything an agent can rewrite (src/sandbox/team_head.py). The rules read it before every agent's round
(apply_pre_turn) and after every agent's round (after_activation). The first read sets the base; every later read
that finds another head records a push in the store (Pushes: {head, by, round, time}) and an info event tagged PUSH_TAG.
Pushes between two reads count as one. Each influencer then hears every push recorded since its entry, once, before its
next round (_turn_presence.due), in one line: who pushed when the reflog names an agent, the new head, and how to read
it (prompts.yaml presence.push, push_unknown, and the domain's push_read). Workers hear nothing: a submit is announced
to every agent already.

    await look()                      # read the head; a change is recorded
    lines = push_events("k7q2m", 2)   # the recipient's due push lines from its entry round on
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import datetime, timezone

from inspect_ai.log import transcript
from inspect_ai.util import StoreModel, store_as
from pydantic import Field

from src.configuration.settings import load_prompts
from src.swarm.turn_rules._turn_state import TurnState, current_round

PUSH_TAG = "swarm_push"            # info event of a push the harness saw, or of a failed read
SHORT = 12                         # the head's characters in a notice

# The sample's reader of the team's head (Benchmark.team_head), set by rules_setup; None for a domain with none.
TEAM_HEAD: ContextVar[Callable[[], Awaitable[dict | None]] | None] = ContextVar("swarm_team_head", default=None)


class Pushes(StoreModel):
    """The pushes the harness saw: the head of the last read, whether a read set the base, how the domain says to
    read the head (push_read), each push {head, by, round, time} in order, and the reads that failed."""

    started: bool = False
    head: str | None = None
    read: str = ""
    pushes: list[dict] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


async def look() -> None:
    """Read the team's head once; the first read sets the base, a later one that differs records a push."""
    reader = TEAM_HEAD.get()
    if reader is None:
        return
    try:
        found = await reader()
    except Exception as error:  # noqa: BLE001 - a failed read sees nothing; the run goes on
        record = store_as(Pushes)
        if len(record.errors) < 5:
            record.errors = [*record.errors, f"{type(error).__name__}: {error}"[:300]]
        return
    record = store_as(Pushes)
    head = (found or {}).get("head")
    if not record.started:
        record.started, record.head = True, head
        return
    if head is None or head == record.head:
        return
    push = {"head": head, "by": (found or {}).get("by"), "round": current_round() or 0,
            "time": datetime.now(timezone.utc).isoformat()}
    record.head, record.pushes = head, [*record.pushes, push]
    transcript().info({PUSH_TAG: push})


def push_events(recipient: str, own: int) -> list[dict]:
    """The push lines due to an influencer from its entry round on, as _turn_presence.due's events; [] for a worker
    (TurnState.workers) and for a run without a reader."""
    st = store_as(TurnState)
    if TEAM_HEAD.get() is None or recipient in st.workers:
        return []
    record = store_as(Pushes)
    texts = load_prompts()["presence"]
    out = []
    for i, push in enumerate(record.pushes):
        if push["round"] < own:
            continue
        short = push["head"][:SHORT]
        text = (texts["push"].format(agent=push["by"], head=short, read=record.read) if push["by"]
                else texts["push_unknown"].format(head=short, read=record.read))
        out.append({"id": f"push:{i}", "agent": push["by"], "kind": "push", "why": None, "round": push["round"],
                    "order": (push["round"], 1, -2, i), "text": text.strip()})
    return out
