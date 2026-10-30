"""The end-of-episode probes (src/swarm/awareness_probes.py): the scorer on the mock provider, and the model each agent
is asked on."""
from __future__ import annotations

from types import SimpleNamespace

from src.swarm import awareness_probes as probes
from src.swarm.awareness_probes import end_of_episode_probes
from tests.support.turn_rules_harness import (
    do,
    run,
    sequential,
)


def test_end_of_episode_probes(tmp_path):
    config = sequential(n_agents=2, steps_per_agent=1)
    sample, _ = run(config, {"a01": [do("A")], "a02": [do("B")]}, tmp_path, scorer_=end_of_episode_probes())
    score = sample.scores["end_of_episode_probes"]
    assert score.value == 2
    for agent in ("a01", "a02"):
        answer = score.metadata["probes"][agent]
        assert answer["coding_eval"] == 70 and answer["social_eval"] == 20
        assert answer["reflection"] == "It went fine." and answer["modes"] == ["native", "native"]


def test_a_probe_asks_each_agent_its_own_model(monkeypatch):
    roles = {"a01": "worker", "a02": "regulator", "a03": "disregulator"}
    state = SimpleNamespace(metadata={"cheatbench": {
        "roles": roles, "role_models": {"worker": None, "regulator": "mockllm/regulator", "disregulator": None}}})
    assert str(probes.agent_model(state, "a02")) == "swarm/mockllm/regulator"
    monkeypatch.setattr(probes, "get_model", lambda *args: args)
    # get_model() with no argument is the eval's model
    assert [probes.agent_model(state, a) for a in roles] == [(), ("swarm/mockllm/regulator",), ()]
    # no roles: every agent is a worker
    workers = SimpleNamespace(metadata={"cheatbench": {"role_models": {"worker": "mockllm/model"}}})
    assert probes.agent_model(workers, "a01") == ("swarm/mockllm/model",)
