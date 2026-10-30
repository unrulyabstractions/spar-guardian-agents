"""Stand-ins for what a tool or a guard reaches during a sample: the store, the tool context and the sandbox."""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace


class FakeStore(dict):
    """The sample store's get and set."""

    def set(self, key, value):
        self[key] = value


@dataclass
class Context:
    """The ToolContext a tool factory reads."""
    agent_name: str
    sample_id: str = "row"
    epoch: int = 1


class Sandboxes:
    """Fake sandboxes: exec answers by (service, command), or by (service, user, command) with by_user, and records
    every call in that form; an unknown call fails."""

    def __init__(self, *, by_user: bool = False):
        self.by_user = by_user
        self.answers: dict[tuple, tuple[int, str]] = {}
        self.calls: list[tuple] = []

    def __call__(self, name: str | None = None):
        async def exec(cmd, user=None, timeout=None, **_):
            key = (name, user, tuple(cmd)) if self.by_user else (name, tuple(cmd))
            self.calls.append(key)
            rc, out = self.answers.get(key, (128, ""))
            return SimpleNamespace(returncode=rc, stdout=out, stderr="")
        return SimpleNamespace(exec=exec)
