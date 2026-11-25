"""The run's communication topology, a fact with no label: who posted, who read, who was shown what, whom a post names,
and when, from the board records the export wrote (communication/<sample>.json). Stored as
processed/facts/topology.json by the pipeline's `topology` part (src/configuration/pipeline_parts.yaml); a label
change never touches it.

    fact = topology(RunDir(Path("runs/<study>/<run>")))
    fact["agents"]                       # [{"agent": "t9j4m", "role": None}, ...] in the run's order (roles_of)
    fact["boards"][0]["messages"][0]     # {"P": 1, "id", "sender", "in_roster", "seeded", "notice", "recipients",
                                         #  "sequence", "names"}, and "record": True on a record (P None)
    fact["boards"][0]["events"][0]       # {"sequence", "kind", "agent_id", "tool_call_id", "message_ids", "source"}
    boards(fact), roles(fact)            # the boards and roles as src/measures/coordination.py reads them

A message's P is its place among its board's posts, the #n agents see (src/swarm/board.py _numbers) and the label the
viewer and the judges give it (P1, P2, ...). A record of who came and went (src/swarm/turn_rules is_record: "a01
entered.", "a01 exited.") is no post: its P is None and it alone carries "record": True. `names` holds the roster ids
its text names, as whole words. Events are kept in file order, every kind, with only the fields above.
Post text is not copied: a reader takes it from the board file. Pure: no model call, nothing written.
"""
from __future__ import annotations

import re

from src.configuration.seed_packs import is_seeded
from src.measures.run_directory import RunDir
from src.swarm.turn_rules import is_record

SYSTEM = "system"
"""The sender of a harness notice on the board."""
EVENT_KEYS = ("sequence", "kind", "agent_id", "tool_call_id", "message_ids", "source")
"""The fields of a board event the fact keeps."""


def names_in(text: str, roster: list[str]) -> list[str]:
    """The roster ids a text names as whole words, in roster order."""
    return [agent for agent in roster if re.search(rf"(?<![A-Za-z0-9]){re.escape(agent)}(?![A-Za-z0-9])", text)]


def board_numbers(messages: list[dict]) -> list[int | None]:
    """Each board message's post number in board order, the #n agents see (src/swarm/board.py _numbers): its place
    among the posts, records of who came and went left out; None for a record."""
    out: list[int | None] = []
    n = 0
    for m in messages:
        if is_record(m):
            out.append(None)
            continue
        n += 1
        out.append(n)
    return out


def board_fact(sample: str, board: dict, roster: list[str]) -> dict:
    """One board's messages and events as the fact keeps them."""
    listed = board.get("messages") or []
    messages = [{"P": n, "id": m.get("id"), "sender": m.get("sender"), "in_roster": m.get("sender") in roster,
                 "seeded": is_seeded(m), "notice": m.get("sender") == SYSTEM, "recipients": m.get("recipients") or [],
                 "sequence": m.get("sequence"), "names": names_in(str(m.get("content") or ""), roster),
                 **({"record": True} if n is None else {})}
                for n, m in zip(board_numbers(listed), listed)]
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
