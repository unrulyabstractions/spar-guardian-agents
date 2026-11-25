"""The model each agent is asked a question on after the run (src/swarm/awareness_probes.py agent_model)."""
from __future__ import annotations

from types import SimpleNamespace

from src.swarm import awareness_probes as probes


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
