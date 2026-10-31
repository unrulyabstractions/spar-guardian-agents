"""Who moves in a shared game, and who asked to reset it.

A shared_task team plays one game (_chess_host_game.py). Its seats move in a fixed order, the roster's without any
regulator that oversees, whose moves and resets the game refuses. One seat holds the move at a time. After a move the
next seat holds it, from the next round, so the team plays one move per round and no seat moves blind on a position a
teammate has just changed. A reset takes the round's move too. A seat that passes hands on the move at once, since a
pass changes no position.

The game cannot wait on one seat. A seat that is not active (the host reads that from the turn rules: its turns ended,
it handed in, or its window is closed) is skipped, and a seat that holds the move for HOLD_ROUNDS rounds without moving
loses it to the next active seat. Each call settles the rotation before it asks who holds the move.

A reset needs every active seat to ask in the same game. A seat's request stands until that game is reset.

    rotation = MoveRotation(["k7q2m", "p8s3z", "t4x9n"])
    rotation.settle(round_number, active)
    rotation.moved("k7q2m", round_number, "Qf6+", active)      # p8s3z holds the move from the next round
    rotation.ask_reset("p8s3z")
    rotation.waiting_for(active)                               # ["k7q2m", "t4x9n"]
"""
from __future__ import annotations

from dataclasses import dataclass, field

HOLD_ROUNDS = 3         # the rounds a seat holds the move without moving before it passes on


@dataclass
class MoveRotation:
    """One shared game's move and reset requests. Every method runs under the owner's lock."""

    seats: list[str]
    """The fixed order."""
    mover: str = ""
    """The seat that holds the move."""
    since: int = 1
    """The first of the mover's HOLD_ROUNDS."""
    last: dict | None = None
    """The latest game action: {round, agent, action ("moved" or "reset"), move}."""
    asked: list[str] = field(default_factory=list)
    """The seats that asked to reset the current game, in order."""
    handoffs: list[dict] = field(default_factory=list)
    """Every time the move went to another seat: {round, from, to, why}, the reason one of "moved", "passed",
    "not_active" and "hold_ran_out"."""

    def __post_init__(self) -> None:
        self.mover = self.mover or self.seats[0]

    def _hand_on(self, round_number: int, since: int, why: str, active: set[str]) -> None:
        """A new hold from round `since`, for the next active seat after the mover. With no other active seat the mover
        keeps the move."""
        self.since = since
        at = self.seats.index(self.mover)
        seat = next((s for s in [*self.seats[at + 1:], *self.seats[:at]] if s in active), None)
        if seat is not None:
            self.handoffs.append({"round": round_number, "from": self.mover, "to": seat, "why": why})
            self.mover = seat

    def settle(self, round_number: int, active: set[str]) -> None:
        """Bring the rotation up to the round: past a mover that is not active, then past each hold that ran out."""
        if self.mover not in active:
            self._hand_on(round_number, round_number, "not_active", active)
        while round_number - self.since >= HOLD_ROUNDS:
            self._hand_on(round_number, self.since + HOLD_ROUNDS, "hold_ran_out", active)

    def moved(self, agent: str, round_number: int, move: str, active: set[str]) -> None:
        """The move stands: the round's game action is taken, and the next seat holds the move from the next round."""
        self.last = {"round": round_number, "agent": agent, "action": "moved", "move": move}
        self._hand_on(round_number, round_number + 1, "moved", active)

    def passed(self, agent: str, round_number: int, active: set[str]) -> bool:
        """The agent passes: if it holds the move the next active seat holds it, in this round too. Returns whether the
        move went on."""
        if agent != self.mover:
            return False
        self._hand_on(round_number, round_number + 1, "passed", active)
        return self.mover != agent

    def ask_reset(self, agent: str) -> None:
        if agent not in self.asked:
            self.asked.append(agent)

    def waiting_for(self, active: set[str]) -> list[str]:
        """The active seats that have not asked to reset the current game."""
        return [seat for seat in self.seats if seat in active and seat not in self.asked]

    def was_reset(self, agent: str, round_number: int) -> None:
        """The game was reset: the round's game action is taken, the requests end with their game, and the mover's hold
        starts next round."""
        self.last = {"round": round_number, "agent": agent, "action": "reset"}
        self.asked = []
        self.since = max(self.since, round_number + 1)

    def view(self, active: set[str]) -> dict:
        """What a game answer shows of the rotation."""
        return {"mover": self.mover, "order": self.seats, "active": [seat for seat in self.seats if seat in active],
                "reset_asked": [seat for seat in self.asked if seat in active]}
