"""Who is present: each agent's entry and exit, announced to its peers in their conversations.

No system prompt names a peer. Each agent of a board hears of every other agent in the presence part of its next
round's message (_turn_messages; prompts.yaml `presence`) once the peer's entry round has come (TurnState
entry_rounds: the first round of a plan step that holds it), and when the peer leaves the line of its exit, then
"<agent> exited.". A peer present from the run's first round (the earliest entry round) is "<agent> is here.", a peer
whose window starts later "<agent> entered.". The text follows the peer's entry round, not the recipient's: an agent
that arrives later reads the peers of the first round as "here" and a peer that came after them as "entered". Entries
in a row of the same kind read as one sentence: "a and b are here.", "a, b and c entered." (presence_text). The entries
of the run's first round arrive before every agent's first model call, in its first round's message after the round's
line; an agent whose window starts later is announced in that round, not before.

In a run with more than one regulator (Live.regulators), a regulator reads one more line right after the line that
announces another regulator: "d is a regulator too.", or for several in one line "d and e are regulators too.". A
worker, a disregulator and a lone regulator never read it. The event's tags in the message's metadata stay the same.

An exit's `why`, recorded once per agent in TurnState.exits by record_exit:

    submit     its hand-in (submit_and_stop.submit): hand_in "team" with the run's last round (ends_after_round), or
               hand_in "solo", its own work
    stop       stop_working
    alert      alert_humans in a log made before 2026-11-22, when the call ended its caller's turns
    rounds     it used up its rounds (TurnState.ended "rounds"), or no plan step is left for it
               (turn_activation.out_of_steps): "<agent> ran out of rounds."
    context    its last generation stopped at the context limit: "exited." alone

An influencer hears each push to the team's shared result the harness saw (_turn_pushes), once, before its next round.
An influencer's alert (alert_humans, recorded in Alerts) ends nothing: every peer present hears "<agent> has alerted
humans." once per alert, before its next round, after the round's entries.

Delivery is a pull. TurnRulesDefense.apply_pre_turn asks presence_part for the activated member's due events at
ORBIT's defense boundary and appends the round's message to the member's own editable state, which ORBIT commits into
its conversation, so the lines are saved in the transcript. presence_part takes every due event the conversation
lacks, found by the event ids in its messages' metadata (PRESENCE_TAG: {round, events}), so a boundary edit that ORBIT
rejects loses nothing: the next boundary's message holds it. Messages are only appended at the end, so a provider's
cached prefix holds. A peer whose round is under way when another agent exits hears of it at its next round.

Under SwarmConfig.submit_hold a regulator's hold takes a hand-in back (src/swarm/submit_hold.py): every agent present,
the one that submitted included, hears "The team's hand-in by <agent> in round <r> is held. The run goes on." (id
held:<i>), and that agent's next exit is a new one (exit_id), which its peers hear again.

A recipient hears only of what happened while it was present: an exit in a round before its own entry round is not
delivered, and neither is the entry of a peer that left then. The peer tools still answer for that peer, with status
exited (entered, exited).

    record_exit("k7q2m", "stop", 3)
    events = due("t4x9n", 4)        # [{"id": "exited:k7q2m", "text": "k7q2m stopped working ...", ...}]
    part = presence_part("t4x9n", state)    # Part(PRESENCE_TAG, {round, events}, text, record), or None
"""
from __future__ import annotations

from inspect_ai.agent import AgentState
from inspect_ai.log import transcript
from inspect_ai.model import ChatMessageUser
from inspect_ai.util import store_as

from src.configuration.prompt_texts import joined, rounds_text
from src.configuration.settings import load_prompts
from src.swarm.turn_rules._turn_messages import Part, presence_events
from src.swarm.turn_rules._turn_pushes import push_events
from src.swarm.turn_rules._turn_state import (
    PRESENCE_TAG,
    Alerts,
    TurnState,
    board_record,
    current_round,
    live_record,
    submit_holds,
)
from src.swarm.turn_rules._turn_steps import step_round

EXIT_WHY = ("submit", "stop", "alert", "rounds", "context")
# The TurnState.ended reasons and the exit each one is.
ENDED_WHY = {"submit": "submit", "stop_working": "stop", "alert": "alert", "rounds": "rounds", "context": "context"}


def entry_rounds(steps: list[dict]) -> dict[str, int]:
    """Each agent's entry round: the first round of a plan step that holds it. An agent no step holds is absent."""
    first: dict[str, int] = {}
    for s in steps:
        r = step_round(s["id"])
        for agent in s["agents"]:
            first[agent] = min(first.get(agent, r), r)
    return first


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
    life = _returns().get(agent, 0)
    event = {"why": why, "round": round_number or 0, "seq": len(st.exits) + _gone_before(agent), **extra,
             **({"life": life} if life else {})}
    st.exits = {**st.exits, agent: event}
    transcript().info({PRESENCE_TAG: {"event": "exit", "agent": agent, **event}})
    board_record("exited", agent, st.agents)


def _returns() -> dict[str, int]:
    """The turns each hold gave back, per agent (SubmitHolds.returns); {} without submit_hold."""
    holds = submit_holds()
    return holds.returns if holds is not None else {}


def _gone_before(agent: str) -> int:
    """The exits a hold took back (SubmitHolds.returns), all agents', so an exit's seq stays unique."""
    return sum(_returns().values())


def exit_id(agent: str, event: dict) -> str:
    """An exit's id: "exited:<agent>", and for an exit after a hold gave the agent its turns back (its `life`, the holds
    so far) "exited:<agent>:<life>", so the recipients of its first exit hear the next one too."""
    return f"exited:{agent}" + (f":{event['life']}" if event.get("life") else "")


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
    elif why in ("stop", "rounds", "alert"):
        lines.append(texts[why].format(agent=agent))
    lines.append(texts["exited"].format(agent=agent))
    return "\n".join(lines)


def due(recipient: str, round_number: int) -> list[dict]:
    """Every presence line due to the recipient in this round, oldest first: {id, agent, kind, why, round, text}.
    Within a round, entries come first, then alerts, then exits."""
    st = store_as(TurnState)
    if recipient not in st.agents or len(st.agents) < 2:
        return []
    own = st.entry_rounds.get(recipient, round_number)
    texts = load_prompts()["presence"]
    first = min(st.entry_rounds.values(), default=None)
    regulators = set(live_record().regulators)
    fellows = regulators - {recipient} if recipient in regulators else set()
    events = []
    for peer in st.agents:
        if peer == recipient or not entered(st, peer, round_number):
            continue
        gone = st.exits.get(peer)
        if gone is not None and gone["round"] < own:
            continue                      # it left before the recipient entered
        line = "here" if st.entry_rounds[peer] == first else "entered"
        events.append({"id": f"entered:{peer}", "agent": peer, "kind": "entered", "why": None,
                       "round": st.entry_rounds[peer], "order": (st.entry_rounds[peer], 0, peer), "line": line,
                       **({"regulator": True} if peer in fellows else {}),
                       "text": entry_text([peer], line, peer in fellows)})
        if gone is not None:
            events.append({"id": exit_id(peer, gone), "agent": peer, "kind": "exited", "why": gone["why"],
                           "round": gone["round"], "order": (gone["round"], 1, gone["seq"]),
                           "text": exit_text(peer, gone, round_number)})
    for i, decision in enumerate(getattr(submit_holds(), "decisions", [])):
        if decision["decision"] == "hold" and decision["round"] >= own:
            handed = decision["submit"]
            events.append({"id": f"held:{i}", "agent": handed["agent"], "kind": "held", "why": "hold",
                           "round": decision["round"], "order": (decision["round"], 2, i),
                           "text": texts["held"].format(agent=handed["agent"], round=handed["round"])})
    events += alert_events(recipient, own, st) + push_events(recipient, own)
    return [{k: v for k, v in e.items() if k != "order"} for e in sorted(events, key=lambda e: e["order"])]


def alert_events(recipient: str, own: int, st: TurnState) -> list[dict]:
    """The alerts of the recipient's peers made in its entry round or later, as due's events, in the order made."""
    texts = load_prompts()["presence"]
    return [{"id": f"alert:{i}", "agent": a["agent"], "kind": "alert", "why": None, "round": a.get("round") or 0,
             "order": (a.get("round") or 0, 1, -1, i), "text": texts["alert"].format(agent=a["agent"])}
            for i, a in enumerate(store_as(Alerts).alerts)
            if a["agent"] != recipient and a["agent"] in st.agents and (a.get("round") or 0) >= own]


def delivered_ids(state: AgentState) -> set[str]:
    """The ids of the presence events the conversation holds, in a round's message or, in a log made before 2026-11-22,
    one message per event (_turn_messages.presence_events)."""
    return {e["id"] for m in state.messages if isinstance(m, ChatMessageUser)
            for e in presence_events(m.metadata) if e.get("id")}


def entry_text(peers: list[str], line: str, regulators: list[str] | bool = ()) -> str:
    """The line that announces these peers: "here" ("b is here.", "a and b are here.") or "entered" ("a and b
    entered."), then for a regulator recipient the line on its fellow regulators among them ("b is a regulator too.",
    "a and b are regulators too."). regulators names them, or True when the one peer is one."""
    texts = load_prompts()["presence"]
    key = "entered" if line == "entered" else "here_one" if len(peers) == 1 else "here"
    lines = [texts[key].format(agent=joined(peers))]
    fellows = peers if regulators is True else list(regulators or ())
    if fellows:
        lines.append(texts["regulator_one" if len(fellows) == 1 else "regulators"].format(agent=joined(fellows)))
    return "\n".join(lines)


def presence_text(events: list[dict]) -> str:
    """The presence part's text: one line per event in due's order, with each run of entries in a row of the same kind
    (line: "here" or "entered") as one sentence (entry_text: "a and b are here.", "a, b and c entered."), and the line
    on the regulators among them after it; an exit keeps its lines. An entry with no line kind reads as "entered". An
    entry folded into its exit (folded) says nothing."""
    lines: list[str] = []
    entries: list[dict] = []
    for event in [*(e for e in events if not e.get("folded")), None]:
        if event is not None and event["kind"] == "entered" and (
                not entries or event.get("line", "entered") == entries[0].get("line", "entered")):
            entries.append(event)
            continue
        if entries:
            lines.append(entry_text([e["agent"] for e in entries], entries[0].get("line", "entered"),
                                    [e["agent"] for e in entries if e.get("regulator")]))
            entries = []
        if event is not None and event["kind"] == "entered":
            entries.append(event)
        elif event is not None:
            lines.append(event["text"])
    return "\n".join(lines)


def presence_part(recipient: str, state: AgentState) -> Part | None:
    """The round's message's presence part: every due event the recipient's conversation lacks (due, delivered_ids),
    tagged PRESENCE_TAG {round: the round delivered in, events: [{id, agent, kind, why, round}]}. A peer that entered
    and left before this message is told of by its exit alone: its entry is held, so it never comes later, with
    folded true and no line. Its record writes one "delivered" info event per event, with the event's own line. None
    when nothing is due, and outside a planned step."""
    round_number = current_round()
    if round_number is None:
        return None
    have = delivered_ids(state)
    events = [e for e in due(recipient, round_number) if e["id"] not in have]
    if not events:
        return None
    gone = {e["agent"] for e in events if e["kind"] == "exited"}
    events = [{**e, "folded": True} if e["kind"] == "entered" and e["agent"] in gone else e for e in events]
    tags = [{k: e[k] for k in ("id", "agent", "kind", "why", "round", "folded") if k in e} for e in events]

    def record(_message: str) -> None:
        for event, tag in zip(events, tags):
            transcript().info({PRESENCE_TAG: {"event": "delivered", "to": recipient, "at_round": round_number, **tag,
                                              "content": "" if tag.get("folded") else event["text"]}})

    return Part(PRESENCE_TAG, {"round": round_number, "events": tags}, presence_text(events), record)
