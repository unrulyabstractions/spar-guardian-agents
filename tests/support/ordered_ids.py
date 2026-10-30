"""The ordered agent names a01, a02, a03, for tests and scripted smoke suites only. A real run never reaches them.

    use_ordered_ids()                   # tests/unit/conftest.py and tests/smoke/common.py, once, at import
    with drawn_ids():                   # a test or a smoke scenario on the real draw
        config.names                    # ["k7q2m", "s3t9j", "z4n8p"]

Tests read and script their agents by name, so they swap SwarmConfig's draw (swarm_config.agent_ids) for this order. The
swap happens in the test process alone. src/, examples/, scripts/ and tools/ never import this module, so a real run has
only the draw.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from src.configuration import swarm_config

DRAWN = swarm_config.agent_ids


def ordered_ids(n: int, seed: int = 0) -> list[str]:
    """a01..a99, then a001..a999, then a0001.. for a thousand, each name of one run as wide as the longest. The seed is
    ignored: a test's names stay put."""
    width = max(2, len(str(n)))
    return [f"a{i:0{width}d}" for i in range(1, n + 1)]


def use_ordered_ids() -> None:
    """SwarmConfig's seats and names become ordered_ids, in this process."""
    swarm_config.agent_ids = ordered_ids


@contextmanager
def drawn_ids() -> Iterator[None]:
    """The real draw inside the block, then ordered_ids again."""
    swarm_config.agent_ids = DRAWN
    try:
        yield
    finally:
        use_ordered_ids()
