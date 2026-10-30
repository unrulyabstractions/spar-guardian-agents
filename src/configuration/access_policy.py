"""Access: which parts of the exploit each agent holds, as one policy for every domain.

    plan("split", config.seats, roles, lambda n: [f"slice{k}" for k in range(1, n + 1)], ["whole"])
    parse("seat1:B+C,seat2:A,seat3:")      # {"seat1": ["B", "C"], "seat2": ["A"], "seat3": []}

A domain declares its items: the slices of the complete submission's archive on openmath, the honeypot's pieces on SWE,
the review tool and the artifact read on knowledge work, the answer key's shards on subset sum, the factors' shares on
prime factorization, the planted designs' segments on protein design, the reference's segments on creative writing, the
request tool and the credential's halves on chess. Under `split`, the default, the domain's items for
n neutral agents are dealt round robin over the neutral agents in seat order, so the whole exploit needs them all, and a
regulator or a disregulator holds nothing. A domain with fewer items than neutral agents (SWE's three pieces) deals them
again to the agents beyond: with five, the fourth and fifth hold the first and second pieces. When fewer than two agents
are neutral every agent holds everything. Under `all` every agent holds everything.

An explicit map names what each seat holds; the command line writes it as `seat1:B+C,seat2:A,seat3:`. A map names seats,
never agent IDs: a run draws its IDs from its seed (SwarmConfig.seats), and seat k is the k-th ID drawn. So a map fixes
what the seats hold and the seed fixes which ID sits where. A map must name every seat and nothing else (check_seats),
which SwarmConfig enforces when a config is built, so a map written with IDs raises and never applies silently.
"""
from __future__ import annotations

from typing import Callable, Literal

Policy = Literal["split", "all"]
Access = Policy | dict[str, list[str]]
POLICIES = ("split", "all")


def parse(value) -> Access:
    """A policy name, a map, or the command line's text `seat1:x+y,seat2:z,seat3:` (empty after the colon: nothing)."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text in POLICIES:
        return text
    out: dict[str, list[str]] = {}
    for entry in text.split(","):
        agent, sep, items = entry.partition(":")
        if not sep or not agent.strip():
            raise ValueError(f"access is one of {', '.join(POLICIES)} or seat:item+item,... pairs, got {value!r}")
        if agent.strip() in out:
            raise ValueError(f"access names {agent.strip()} twice: {value!r}")
        out[agent.strip()] = [i.strip() for i in items.split("+") if i.strip()]
    return out


def text(access: Access) -> str:
    """The command line's text of a policy or a map (the inverse of parse)."""
    if isinstance(access, str):
        return access
    return ",".join(f"{agent}:{'+'.join(items)}" for agent, items in access.items())


def policy_name(access: Access) -> str:
    """split, all or explicit."""
    return access if isinstance(access, str) else "explicit"


def seat_labels(n_agents: int) -> list[str]:
    """The keys of an explicit map for a team of this size: seat1..seatN."""
    return [f"seat{k}" for k in range(1, n_agents + 1)]


def neutral_agents(names: list[str], roles: dict[str, str] | None) -> list[str]:
    """The agents without a role (workers), in the order given. Without roles every agent is neutral."""
    return [n for n in names if (roles or {}).get(n, "worker") == "worker"]


def deal(items: list[str], agents: list[str]) -> dict[str, list[str]]:
    """Round robin: item i goes to agent i mod n. When the agents outnumber the items, agent i beyond the items holds
    item i mod k again, so every agent holds something and each item has as even a number of holders as it can."""
    dealt: dict[str, list[str]] = {a: [] for a in agents}
    if not agents or not items:
        return dealt
    for i, item in enumerate(items):
        dealt[agents[i % len(agents)]].append(item)
    for i in range(len(items), len(agents)):
        dealt[agents[i]].append(items[i % len(items)])
    return dealt


def plan(access: Access, seats: list[str], roles: dict[str, str] | None,
         split_items: Callable[[int], list[str]], whole: list[str]) -> dict[str, list[str]]:
    """What each agent holds, sorted by agent so that the plan's order shows no seat. `seats` lists the agents in seat
    order (SwarmConfig.seats): an explicit map's seat k is seats[k - 1], and the deal follows this order. split_items(n)
    gives the domain's items for n neutral agents; whole is what an agent with everything holds."""
    neutral = neutral_agents(seats, roles)
    if isinstance(access, dict):
        held = {a: list(access[label]) for a, label in zip(seats, seat_labels(len(seats)))}
    elif access == "all" or len(neutral) < 2:
        held = {a: list(whole) for a in seats}
    else:
        dealt = deal(split_items(len(neutral)), neutral)
        held = {a: dealt.get(a, []) for a in seats}
    return dict(sorted(held.items()))


def check_numbered(named: list[int], what: str) -> None:
    """Raise unless the piece numbers an explicit map names, sorted, are 1..N with N at least 2: a single piece would
    be the whole renamed, and a gap could never be put back together. A map naming no piece passes."""
    if named and (named != list(range(1, named[-1] + 1)) or named[-1] < 2):
        raise ValueError(f"access must name {what} 1..N with N at least 2, each held by some agent, got {named}")


def check_seats(access: Access, n_agents: int) -> None:
    """Raise unless an explicit map names every seat of the team and nothing else. An agent ID is not a seat, so a map
    written with IDs raises here."""
    if isinstance(access, dict) and set(access) != set(seat_labels(n_agents)):
        raise ValueError(f"access must name every seat, seat1 to seat{n_agents}, and no agent ID, since a run draws "
                         f"its IDs; got {sorted(access)}")


def check_map(access: Access, valid: Callable[[str], bool]) -> None:
    """Raise unless every seat of an explicit map holds items the domain knows, each once; a seat holding `whole`
    holds nothing else."""
    if not isinstance(access, dict):
        return
    for seat, items in access.items():
        unknown = [i for i in items if not valid(i)]
        if unknown:
            raise ValueError(f"access: {seat} holds unknown item(s) {unknown}")
        if len(set(items)) != len(items):
            raise ValueError(f"access: {seat} holds an item twice: {items}")
        if "whole" in items and len(items) > 1:
            raise ValueError(f"access: {seat} holds whole and more: {items}")
