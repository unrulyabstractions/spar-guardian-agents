"""The run's communication topology, a fact with no label: who posted, who read, who was shown what, whom a post names,
and when, from the board records the export wrote (communication/<sample>.json). Stored as
processed/facts/topology.json by the pipeline's `topology` part (src/configuration/pipeline_parts.yaml); a label
change never touches it.

    fact = topology(RunDir(Path("runs/ian_seed_prototyping_0/swe_deepseek_pointer_s1")))
    fact["agents"]                       # [{"agent": "t9j4m", "role": None}, ...] in the run's order (roles_of)
    fact["boards"][0]["messages"][0]     # {"P": 1, "id", "sender", "in_roster", "seeded", "notice", "recipients",
                                         #  "sequence", "names"}
    fact["boards"][0]["events"][0]       # {"sequence", "kind", "agent_id", "tool_call_id", "message_ids", "source"}
    boards(fact), roles(fact)            # the boards and roles as src/measures/coordination.py reads them

A message's P is its place on its board, the label the viewer and the judges give it (P1, P2, ...). `names` holds the
roster ids its text names, as whole words. Events are kept in file order, every kind, with only the fields above.
Post text is not copied: a reader takes it from the board file. Pure: no model call, nothing written.
"""
from __future__ import annotations

import re

from src.configuration.seed_packs import is_seeded
from src.measures.run_directory import RunDir

SYSTEM = "system"
"""The sender of a harness notice on the board."""
EVENT_KEYS = ("sequence", "kind", "agent_id", "tool_call_id", "message_ids", "source")
"""The fields of a board event the fact keeps."""


def names_in(text: str, roster: list[str]) -> list[str]:
    """The roster ids a text names as whole words, in roster order."""
    return [agent for agent in roster if re.search(rf"(?<![A-Za-z0-9]){re.escape(agent)}(?![A-Za-z0-9])", text)]


def board_fact(sample: str, board: dict, roster: list[str]) -> dict:
    """One board's messages and events as the fact keeps them."""
    messages = [{"P": n, "id": m.get("id"), "sender": m.get("sender"), "in_roster": m.get("sender") in roster,
                 "seeded": is_seeded(m), "notice": m.get("sender") == SYSTEM, "recipients": m.get("recipients") or [],
                 "sequence": m.get("sequence"), "names": names_in(str(m.get("content") or ""), roster)}
                for n, m in enumerate(board.get("messages") or [], 1)]
    events = [{key: e.get(key) for key in EVENT_KEYS} for e in board.get("events") or []]
    return {"sample": sample, "messages": messages, "events": events}


def topology(run: RunDir) -> dict:
    """The fact: the run's agents and roles in their order, and each board in file name order."""
    roster = run.roles()
    folder = run.file("communication")
    files = sorted(folder.glob("*.json")) if folder.is_dir() else []
    return {"agents": [{"agent": agent, "role": role} for agent, role in roster.items()],
            "boards": [board_fact(path.stem, run.board(path.stem), list(roster)) for path in files]}


def boards(fact: dict) -> list[dict]:
    """The fact's boards, each {"messages", "events"}, in the shape src/measures/coordination.py reads."""
    return [{"messages": board["messages"], "events": board["events"]} for board in fact["boards"]]


def roles(fact: dict) -> dict[str, str | None]:
    """agent -> role, in the run's order."""
    return {row["agent"]: row["role"] for row in fact["agents"]}


def topology_part(job) -> dict:
    """The pipeline's `topology` part: the fact of the job's run."""
    return topology(job.run)
