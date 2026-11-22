"""The seed's setup: a seed pack's posts on the board and its files on the agents' machines, before round 1.

    solvers = [rules_setup(...), benchmark.setup(), seed_setup(benchmark, record, config.names), ...]

record is src/configuration/seed_packs.py seed_record's, which run.json "seed" keeps. The posts go straight into ORBIT's
communication store, as board.system_post's notice does, in the pack's order and before any agent's post: each under
its author's ID, for every agent of the roster, marked with SEEDED (CommunicationMessage.invocation_id) so the board's
tools and the exports can tell a seeded post from a teammate's. ORBIT's send takes only a configured agent as sender,
and the previous team is on no roster. An agent's first round's notice then names them as new posts.

The files go in after the benchmark's own setup, so the staging is done and each agent's seat known
(Benchmark.seats), and before the access log's sidecar starts. A `tmp` file goes once into /tmp of every machine of a
seat, root's, mode 0644; a `home` file into each seat's home, the seat user's, mode 0644. Neither goes into the task's
repository, so no graded tree holds it. SEED_STORE keeps each planted file's agent, service, path and owner.
"""
from __future__ import annotations

import shlex
from uuid import uuid4

from inspect_ai.solver import Generate, Solver, TaskState, solver
from orbit.communication.runtime import initialize_communication
from orbit.communication.state import CommunicationMessage

from src.configuration.seed_packs import SEEDED, file_path
from src.swarm.turn_rules import BOARD_ID

SEED_STORE = "seed_planted"     # sample store: [{agent, service, path, owner, name}] of each planted file


def seed_posts(record: dict, agents: list[str], board: str = BOARD_ID) -> list[str]:
    """Put the record's posts on the board, oldest first, for every agent of the roster; their ids."""
    state = initialize_communication()
    ids = []
    for post in record["posts"]:
        message = CommunicationMessage(id=f"message-{uuid4().hex}", channel_id=board, sender=post["author"],
                                       recipients=tuple(agents), content=post["text"], sequence=state.sequence + 1,
                                       invocation_id=SEEDED)
        state.sequence += 1
        state.messages.append(message)
        ids.append(message.id)
    return ids


async def plant(seat, path: str, text: str, owner: str) -> None:
    """Write one file in the seat's machine as root, then give it its owner and mode 0644."""
    box = seat.box()
    await box.write_file(path, text)
    result = await box.exec(["sh", "-c", f"chown {shlex.quote(owner)} {shlex.quote(path)} && "
                                         f"chmod 0644 {shlex.quote(path)}"], user="root")
    if not result.success:
        raise RuntimeError(f"seed: cannot set the owner of {path}: {result.stderr.strip()}")


async def seed_files(record: dict, seats: dict) -> list[dict]:
    """Plant the record's files: a tmp file once per machine, a home file in each seat's home."""
    planted, done = [], set()
    for f in record["files"]:
        for agent, seat in sorted(seats.items()):
            if f["place"] == "tmp":
                if (seat.service, f["name"]) in done:
                    continue
                done.add((seat.service, f["name"]))
                path, owner = file_path("tmp", f["name"], None), "root"
            else:
                path, owner = file_path("home", f["name"], seat.home), seat.user or "root"
            await plant(seat, path, f["text"], owner)
            planted.append({"agent": agent, "service": seat.service, "path": path, "owner": owner, "name": f["name"]})
    return planted


@solver
def seed_setup(benchmark, record: dict, agents: list[str]) -> Solver:
    """Task setup: the seed's posts on the board (on a run with one), then its files on the agents' machines."""
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        if record["posts"]:
            seed_posts(record, agents)
        planted = await seed_files(record, benchmark.seats(state)) if record["files"] else []
        state.store.set(SEED_STORE, planted)
        return state
    return solve
