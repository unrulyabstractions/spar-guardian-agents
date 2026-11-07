"""The board seed (src/swarm/board_seed.py): the table lookup and the config guards, and, on the mock provider, the
one stimulus posted on the board before any agent acts, which an agent then reads."""
from __future__ import annotations

import pytest

from src.configuration.swarm_config import SwarmConfig
from src.swarm import board_seed
from tests.support.turn_rules_harness import board_post, do, pull, read, run, status, tool_results


def test_norm_level_and_table_lookup():
    assert board_seed.norm_level("0") == board_seed.norm_level("L0") == board_seed.norm_level("l0") == "0"
    assert board_seed.table_value("pro-coord", "2") == board_seed.SEED_TABLE[("pro-coord", "2")]
    assert board_seed.table_value("anti-coord", "1") == board_seed.USE_REGULATOR
    with pytest.raises(ValueError, match="unknown board seed"):
        board_seed.table_value("pro-coord", "9")


def test_seed_text_by_condition():
    assert board_seed.seed_text(pull(n_agents=3)) is None            # no board_seed: nothing
    neutral = board_seed.seed_text(pull(n_agents=3, board_seed="neutral", board_level="0"))
    pro1 = board_seed.seed_text(pull(n_agents=3, board_seed="pro-coord", board_level="1"))
    # a message condition returns its own non-empty table text, no leftover placeholder.
    assert neutral == board_seed.SEED_TABLE[("neutral", "0")] and neutral and "<<" not in neutral
    assert pro1 == board_seed.SEED_TABLE[("pro-coord", "1")] and pro1 and "<<" not in pro1
    # the anti-coordination condition posts no message; the hidden regulator carries it.
    assert board_seed.seed_text(pull(n_agents=3, board_seed="anti-coord", board_level="1")) is None


def test_config_guards():
    with pytest.raises(ValueError, match="board_seed needs a board"):
        SwarmConfig(n_agents=1, board_seed="neutral", board_level="0")
    with pytest.raises(ValueError, match="board_seed needs a board_level"):
        SwarmConfig(n_agents=3, board_seed="neutral")
    with pytest.raises(ValueError, match="board_level needs a board_seed"):
        SwarmConfig(n_agents=3, board_level="0")


def test_anti_coordination_needs_a_regulator():
    with pytest.raises(ValueError, match="anti-coordination lever"):
        board_seed.board_seed_setup(pull(n_agents=3, board_seed="anti-coord", board_level="1"), ["a01", "a02", "a03"])
    # with a regulator it builds without complaint (and posts nothing).
    board_seed.board_seed_setup(pull(n_agents=3, regulators=1, board_seed="anti-coord", board_level="1"),
                                ["a01", "a02", "a03"])


def test_setup_solvers_appends_the_seed_only_when_asked():
    """src/tasks.py setup_solvers appends board_seed_setup exactly when the config has a board and a board_seed, and
    leaves every other cell's setup untouched."""
    from types import SimpleNamespace

    from inspect_ai.solver import Generate, Solver, TaskState, solver

    from src import tasks

    @solver
    def noop() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            return state
        return solve

    class StubBench:
        def keeps_access_log(self, config):
            return False

        def setup(self):
            return noop()

    def plan_for(cfg):
        # setup_solvers reads only plan.config and plan.roles (stop_after_delivery is None, so delivery() is a no-op).
        return SimpleNamespace(config=cfg, roles=None)

    base = pull(n_agents=3, concurrent_rounds=False, turn_order="round_robin")
    seeded = base.replace(board_seed="neutral", board_level="0")
    n_base = len(tasks.setup_solvers(StubBench(), plan_for(base)))
    n_seeded = len(tasks.setup_solvers(StubBench(), plan_for(seeded)))
    assert n_seeded == n_base + 1


def test_the_seed_is_post_one_and_an_agent_reads_it(tmp_path):
    """A message condition posts one system message on the board at setup; it is post #1, before any agent's work,
    and the first board_read returns it."""
    config = pull(steps_per_agent=2, board_seed="pro-coord", board_level="2", concurrent_rounds=False,
                  turn_order="round_robin")
    seed = board_seed.board_seed_setup(config, config.names)
    scripts = {"a01": [read(), do("A")], "a02": [status(), do("B")], "a03": [do("C"), board_post("hi")]}
    sample, _ = run(config, scripts, tmp_path, extra_setup=[seed])
    seed_body = board_seed.SEED_TABLE[("pro-coord", "2")]
    messages = sample.store.get("CommunicationState:messages")
    # the seed is the first message on the board, sent by the system.
    assert (messages[0]["sender"], messages[0]["content"]) == ("system", seed_body)
    reads = [r for f, r in tool_results(sample) if f == "board_read"]
    assert reads and seed_body in reads[0]        # a01's first read sees the seed as #1
    assert "system: 1 posts" in next(r for f, r in tool_results(sample) if f == "board_status")
