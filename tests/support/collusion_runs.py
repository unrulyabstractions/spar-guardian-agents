"""Small synthetic runs for the Collusion ladder's tests (tests/unit/test_collusion.py) and its take rules'
(tests/unit/test_collusion_takes.py): tool calls, a run of them, and a creative-writing text."""
from __future__ import annotations

from pathlib import Path

from src.measures.collusion_takes import TOOLS, Call, Run, Take

LINES = "the brass lantern swings above the flooded quay\nand gulls argue over the copper roofs at dusk"
HAND_IN = "/workspace/writing.md"


def call(agent: str, k: int, turn: int | None, cmd: str = "", result: str = "", tool: str = "shell",
         args: dict | None = None, cid: str | None = None, start: float | None = None) -> Call:
    return Call(agent, k, turn, start, None, tool, cid or f"{agent}-{k}", cmd if tool in TOOLS else "",
                args if args is not None else ({"command": cmd} if tool in TOOLS else {}), result)


def make_run(calls: list[Call], roles: dict, run_json: dict | None = None, boards: list[dict] | None = None,
             prompts: dict | None = None, path: Path = Path("unused")) -> Run:
    by: dict[str, list[Call]] = {a: [] for a in roles}
    for c in calls:
        by[c.agent].append(c)
    return Run(path, run_json or {}, roles, by, prompts or dict.fromkeys(roles, ""), boards or [])


def directed(takes: list[Take]) -> set[tuple[str, str]]:
    return {(t.taker, t.giver) for t in takes}
