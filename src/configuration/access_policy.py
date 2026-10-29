"""Access: which parts of the exploit each agent holds, as one policy for every domain.

    plan("split", ["a01", "a02", "a03"], roles, lambda n: [f"slice{k}" for k in range(1, n + 1)], ["whole"])
    parse("a01:B+C,a02:A,a03:")      # {"a01": ["B", "C"], "a02": ["A"], "a03": []}

A domain declares its items: the slices of the complete submission's archive on openmath, the honeypot's pieces on
SWE, the review tool and the artifact read on knowledge work, the answer key's shards on subset sum, the factors'
shares on prime factorization, the planted designs' segments on protein design. Under `split`, the default, the
domain's items for n
neutral agents are dealt round robin over the neutral agents, so the whole exploit needs them all, and a regulator or
a disregulator holds nothing. When fewer than two agents are neutral every agent holds everything. Under `all` every
agent holds everything. An explicit map names what each agent of the roster holds; the command line writes it as
`a01:B+C,a02:A,a03:`.
"""
from __future__ import annotations

from typing import Callable, Literal

Policy = Literal["split", "all"]
Access = Policy | dict[str, list[str]]
POLICIES = ("split", "all")


def parse(value) -> Access:
    """A policy name, a map, or the command line's text `a01:x+y,a02:z,a03:` (empty after the colon: nothing)."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text in POLICIES:
        return text
    out: dict[str, list[str]] = {}
    for entry in text.split(","):
        agent, sep, items = entry.partition(":")
        if not sep or not agent.strip():
            raise ValueError(f"access is one of {', '.join(POLICIES)} or agent:item+item,... pairs, got {value!r}")
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


def neutral_agents(names: list[str], roles: dict[str, str] | None) -> list[str]:
    """The agents without a role (workers), in roster order. Without roles every agent is neutral."""
    return [n for n in names if (roles or {}).get(n, "worker") == "worker"]


def deal(items: list[str], agents: list[str]) -> dict[str, list[str]]:
    """Round robin: item i goes to agent i mod n. Agents beyond the items hold nothing."""
    dealt: dict[str, list[str]] = {a: [] for a in agents}
    for i, item in enumerate(items if agents else []):
        dealt[agents[i % len(agents)]].append(item)
    return dealt


def plan(access: Access, agents: list[str], roles: dict[str, str] | None,
         split_items: Callable[[int], list[str]], whole: list[str]) -> dict[str, list[str]]:
    """What each agent holds. split_items(n) gives the domain's items for n neutral agents; whole is what an agent
    with everything holds."""
    if isinstance(access, dict):
        return {a: list(access[a]) for a in agents}
    neutral = neutral_agents(agents, roles)
    if access == "all" or len(neutral) < 2:
        return {a: list(whole) for a in agents}
    held = deal(split_items(len(neutral)), neutral)
    return {a: held.get(a, []) for a in agents}


def check_map(access: Access, names: list[str], valid: Callable[[str], bool]) -> None:
    """Raise unless an explicit map names every agent of the roster and only them, with items the domain knows, each
    once; an agent holding `whole` holds nothing else."""
    if not isinstance(access, dict):
        return
    if set(access) != set(names):
        raise ValueError(f"access must name every agent of the roster {names}, got {sorted(access)}")
    for agent, items in access.items():
        unknown = [i for i in items if not valid(i)]
        if unknown:
            raise ValueError(f"access: {agent} holds unknown item(s) {unknown}")
        if len(set(items)) != len(items):
            raise ValueError(f"access: {agent} holds an item twice: {items}")
        if "whole" in items and len(items) > 1:
            raise ValueError(f"access: {agent} holds whole and more: {items}")
