"""Who is present: each agent's entry and exit, announced to its peers as user messages in their conversations.

No system prompt names a peer. Each agent of a board hears of every other agent as a durable user message (prompts.yaml
`presence`), delivered before its next step: "<agent> entered." once the peer's entry round has come (TurnState
entry_rounds: the first round of a plan step that holds it), and when the peer leaves the line of its exit, then
"<agent> exited.". The entries of the run's first round arrive before every agent's first step, after its task
message; an agent whose window starts later is announced in that round, not before.

An exit's `why`, recorded once per agent in TurnState.exits by record_exit:

    submit     its hand-in (submit_and_stop.submit): hand_in "team" with the run's last round (ends_after_round), or
               hand_in "solo", its own work
    stop       stop_working
    alert      alert_humans (an influencer)
    budget     no plan step left and none that pooling could still give it (turn_activation.out_of_steps), or the cap
    context    its last generation stopped at the context limit: "exited." alone
    delivered  stop_after_delivery ended it: "exited." alone

Delivery is a pull. TurnRulesDefense.apply_pre_turn calls deliver on the activated member's own editable state at
ORBIT's defense boundary, which commits what it appends into that member's conversation, so the lines are saved in the
transcript. deliver appends every due line the conversation lacks, found by the event id in the message's metadata
(PRESENCE_TAG), so a boundary edit that ORBIT rejects loses nothing: the next boundary delivers it again. Messages are
only appended at the end, so a provider's cached prefix holds. A peer whose step is under way when another agent exits
hears of it at its next step.

A recipient hears only of what happened while it was present: an exit in a round before its own entry round is not
delivered, and neither is the entry of a peer that left then. The peer tools still answer for that peer, with status
exited (entered, exited).

    record_exit("k7q2m", "stop", 3)
    messages = due("t4x9n", 4)      # [{"id": "exit:k7q2m", "text": "k7q2m stopped working ...", ...}]
"""
from __future__ import annotations

from inspect_ai.agent import AgentState
from inspect_ai.log import transcript
from inspect_ai.model import ChatMessageUser
from inspect_ai.util import store_as

from src.configuration.prompt_texts import rounds_text
from src.configuration.settings import load_prompts
from src.swarm.turn_rules._turn_state import PRESENCE_TAG, TurnState, current_round
from src.swarm.turn_rules._turn_steps import step_round

EXIT_WHY = ("submit", "stop", "alert", "budget", "context", "delivered")
# The TurnState.ended reasons and the exit each one is.
ENDED_WHY = {"submit": "submit", "stop_working": "stop", "alert": "alert", "cap": "budget", "context": "context",
             "delivered": "delivered"}


def entry_rounds(steps: list[dict]) -> dict[str, int]:
    """Each agent's entry round: the first round of a plan step that holds it. An agent no step holds is absent."""
    first: dict[str, int] = {}
    for s in steps:
        r = step_round(s["id"])
        for agent in s["agents"]:
            first[agent] = min(first.get(agent, r), r)
    return first


def enter_late(st: TurnState, steps: list[dict]) -> None:
    """Give an entry round to every agent a step of the amended plan holds for the first time (a pooled step for an
    agent the written plan held nowhere)."""
    late = {a: r for a, r in entry_rounds(steps).items() if a not in st.entry_rounds}
    if late:
        st.entry_rounds = {**st.entry_rounds, **late}


def entered(st: TurnState, agent: str, round_number: int | None) -> bool:
    """Whether the agent's entry round has come by this round (the round under way). Outside a planned step, whether it
    has an entry round at all."""
    entry = st.entry_rounds.get(agent)
    return entry is not None and (round_number is None or entry <= round_number)


def exited(st: TurnState, agent: str) -> bool:
    return agent in st.exits


def record_exit(agent: str, why: str, round_number: int | None, **extra) -> None:
    """Record the agent's exit, once: TurnState.exits[agent] = {why, round, seq, **extra}, and an info event."""
    if why not in EXIT_WHY:
        raise ValueError(why)
    st = store_as(TurnState)
    if agent in st.exits or len(st.agents) < 2:
        return
    event = {"why": why, "round": round_number or 0, "seq": len(st.exits), **extra}
    st.exits = {**st.exits, agent: event}
    transcript().info({PRESENCE_TAG: {"event": "exit", "agent": agent, **event}})


def exit_text(agent: str, event: dict, round_number: int) -> str:
    """The lines of an exit as one recipient in this round reads them: why it left, then "exited.". A team's hand-in
    counts the rounds left after the recipient's own."""
    texts = load_prompts()["presence"]
    why, lines = event["why"], []
    if why == "submit" and event.get("hand_in") == "team":
        left = (event.get("ends_after_round") or 0) - round_number
        lines.append(texts["submit_team"].format(agent=agent, rounds=rounds_text(left)) if left > 0
                     else texts["submit_team_now"].format(agent=agent))
    elif why == "submit":
        lines.append(texts["submit_solo"].format(agent=agent))
    elif why in ("stop", "budget", "alert"):
        lines.append(texts[why].format(agent=agent))
    lines.append(texts["exited"].format(agent=agent))
    return "\n".join(lines)


def due(recipient: str, round_number: int) -> list[dict]:
    """Every presence line due to the recipient in this round, oldest first: {id, agent, kind, why, round, text}.
    Entries come before exits within a round."""
    st = store_as(TurnState)
    if recipient not in st.agents or len(st.agents) < 2:
        return []
    own = st.entry_rounds.get(recipient, round_number)
    texts = load_prompts()["presence"]
    events = []
    for peer in st.agents:
        if peer == recipient or not entered(st, peer, round_number):
            continue
        gone = st.exits.get(peer)
        if gone is not None and gone["round"] < own:
            continue                      # it left before the recipient entered
        events.append({"id": f"entered:{peer}", "agent": peer, "kind": "entered", "why": None,
                       "round": st.entry_rounds[peer], "order": (st.entry_rounds[peer], 0, peer),
                       "text": texts["entered"].format(agent=peer)})
        if gone is not None:
            events.append({"id": f"exited:{peer}", "agent": peer, "kind": "exited", "why": gone["why"],
                           "round": gone["round"], "order": (gone["round"], 1, gone["seq"]),
                           "text": exit_text(peer, gone, round_number)})
    return [{k: v for k, v in e.items() if k != "order"} for e in sorted(events, key=lambda e: e["order"])]


def delivered_ids(state: AgentState) -> set[str]:
    return {m.metadata[PRESENCE_TAG]["id"] for m in state.messages
            if isinstance(m, ChatMessageUser) and (m.metadata or {}).get(PRESENCE_TAG)}


def deliver(recipient: str, state: AgentState) -> list[str]:
    """Append to the recipient's conversation every due line it lacks (due, delivered_ids), one user message per
    event with PRESENCE_TAG metadata, each recorded as an info event. Returns the ids appended. Outside a planned step
    nothing is due."""
    round_number = current_round()
    if round_number is None:
        return []
    have = delivered_ids(state)
    added = []
    for event in due(recipient, round_number):
        if event["id"] in have:
            continue
        tag = {k: event[k] for k in ("id", "agent", "kind", "why", "round")}
        state.messages.append(ChatMessageUser(content=event["text"], metadata={PRESENCE_TAG: tag}))
        transcript().info({PRESENCE_TAG: {"event": "delivered", "to": recipient, "at_round": round_number, **tag,
                                          "content": event["text"]}})
        added.append(event["id"])
    return added
