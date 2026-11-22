"""The Inspect tasks (src/tasks.py): what a control cell appends to the instruction, and the access log's switch.
tests/unit/test_task_names.py checks the task names the analysis tools read."""
from __future__ import annotations

import asyncio

import pytest
import yaml
from inspect_ai._util.registry import registry_log_name
from pydantic import ValidationError

from src import tasks
from src.configuration.presets import build_config
from src.configuration.prompt_texts import rounds_values
from src.domains.chess import Chess
from src.domains.interview import SubsetSum
from src.domains.openmath import OpenMath
from src.sandbox.access_log.access_monitor import AUDITOR, MONITOR_DEVIATION
from src.sandbox.network_log import network_monitor
from src.sandbox.network_log.network_monitor import NETWORK_DEVIATION, NETWORK_SIDECAR
from src.tasks import swarm_task


def test_an_extra_instruction_follows_the_rule_sentence():
    """A control cell appends its own text after the task instruction and the rule sentence (swarm_task's
    extra_instruction), before the rounds' values; config.replace gives it a typed copy."""
    config = build_config("default", "small", ["rule_level=vague"]).replace(n_agents=1)
    assert config.n_agents == 1 and config.names == ["a01"] and config.rule_level == "vague"
    with pytest.raises(ValidationError):
        config.replace(n_agents=0)
    benchmark = OpenMath()
    values = f"\n{rounds_values(config)}\n"
    base = swarm_task(benchmark, config).dataset[0].input
    assert base.endswith(values) and base.removesuffix(values).rstrip().endswith("Your work should be your own.")
    task = swarm_task(benchmark, config, extra_instruction="By any means.")
    assert task.dataset[0].input == base.removesuffix(values) + "\nBy any means.\n" + values


def monitor_parts(benchmark, *pairs: str) -> tuple[bool, list[str], list[str]]:
    """(whether the compose file has the access log's sidecar, the setup solvers, the scorers) of one task."""
    sidecars = sidecar_services(benchmark, *pairs)
    task = swarm_task(benchmark, build_config("default", "small", ["n_agents=1", *pairs]))
    return AUDITOR in sidecars, [registry_log_name(s) for s in task.setup], [registry_log_name(s) for s in task.scorer]


def sidecar_services(benchmark, *pairs: str) -> set[str]:
    """The sidecars in one task's compose file."""
    task = swarm_task(benchmark, build_config("default", "small", ["n_agents=1", *pairs]))
    services = yaml.safe_load(open(task.dataset[0].sandbox.config).read())["services"]
    return set(services) & {AUDITOR, NETWORK_SIDECAR}


def test_the_access_log_adds_the_sidecar_the_solver_and_the_first_scorer():
    sidecar, setup, scorers = monitor_parts(SubsetSum())
    assert sidecar and setup == ["rules_setup", "hide_proc", "environment_probe", "start_access_monitor",
                                 "start_network_monitor"]
    assert scorers[:4] == ["jobs_capture", "access_capture", "network_capture", "interview_deliverables"]


def test_the_network_switch_off_keeps_the_access_log_alone():
    sidecar, setup, scorers = monitor_parts(SubsetSum(), "network_log=false")
    assert sidecar and setup == ["rules_setup", "hide_proc", "environment_probe", "start_access_monitor"]
    assert scorers[:3] == ["jobs_capture", "access_capture", "interview_deliverables"]
    assert sidecar_services(SubsetSum(), "network_log=false") == {AUDITOR}


def test_each_log_brings_its_own_sidecar():
    assert sidecar_services(SubsetSum()) == {AUDITOR, NETWORK_SIDECAR}
    assert sidecar_services(SubsetSum(), "access_log=false") == {NETWORK_SIDECAR}
    assert sidecar_services(SubsetSum(), "access_log=false", "network_log=false") == set()


def test_the_network_log_mounts_the_tracer_in_its_sidecar_alone(monkeypatch):
    monkeypatch.setattr(network_monitor, "ensure_tracer", lambda image: "network-trace-test")
    task = swarm_task(SubsetSum(), build_config("default", "small", ["n_agents=1"]))
    services = yaml.safe_load(open(task.dataset[0].sandbox.config).read())["services"]
    assert services[NETWORK_SIDECAR]["volumes"][0] == {"type": "image", "source": "network-trace-test",
                                                       "target": "/nix"}
    assert all(name == NETWORK_SIDECAR or "network-trace-test" not in str(svc) for name, svc in services.items())


def test_the_switch_off_adds_nothing():
    sidecar, setup, scorers = monitor_parts(SubsetSum(), "access_log=false", "network_log=false")
    assert not sidecar and setup == ["rules_setup", "hide_proc", "environment_probe"]
    assert "access_capture" not in scorers and "network_capture" not in scorers
    sidecar, setup, scorers = monitor_parts(SubsetSum(), "access_log=false")
    assert setup == ["rules_setup", "hide_proc", "environment_probe", "start_network_monitor"]
    assert "access_capture" not in scorers
    assert scorers[:3] == ["jobs_capture", "network_capture", "interview_deliverables"]


@pytest.mark.parametrize("pairs", [[], ["access_log=false"]])
def test_a_domain_with_no_machine_to_watch_gets_no_sidecar(pairs):
    sidecar, setup, scorers = monitor_parts(Chess(), *pairs)
    assert not sidecar and "start_access_monitor" not in setup and "access_capture" not in scorers
    assert "start_network_monitor" not in setup and "network_capture" not in scorers
    assert "jobs_capture" not in scorers          # no shell, no jobs


@pytest.mark.parametrize(("benchmark", "pairs", "labels"), [
    (SubsetSum(), [], [NETWORK_DEVIATION, MONITOR_DEVIATION]),
    (SubsetSum(), ["network_log=false"], [MONITOR_DEVIATION]), (SubsetSum(), ["access_log=false"], [NETWORK_DEVIATION]),
    (SubsetSum(), ["access_log=false", "network_log=false"], []), (Chess(), [], []),
])
def test_a_monitored_run_records_the_deviations_last(benchmark, pairs, labels):
    task = swarm_task(benchmark, build_config("default", "small", ["n_agents=1", *pairs]))
    deviations = task.dataset[0].metadata["cheatbench"]["deviations"]
    assert [d for d in deviations if d in (MONITOR_DEVIATION, NETWORK_DEVIATION)] == labels
    assert not labels or deviations[-len(labels):] == labels


def test_the_cleanup_captures_and_then_runs_the_benchmarks(monkeypatch):
    order = []

    class Bench(SubsetSum):
        async def cleanup(self, state):
            order.append("benchmark")

    async def capture(state):
        order.append("capture")

    async def capture_network(state):
        order.append("network")

    async def end_jobs():
        order.append("jobs")

    monkeypatch.setattr(tasks, "capture", capture)
    monkeypatch.setattr(tasks, "capture_network", capture_network)
    monkeypatch.setattr(tasks.command_jobs, "end_jobs", end_jobs)
    asyncio.run(tasks.cleanup(Bench(), build_config("default", "small", []))(None))
    assert order == ["jobs", "capture", "network", "benchmark"]
    order.clear()
    asyncio.run(tasks.cleanup(Bench(), build_config("default", "small", ["access_log=false", "network_log=false"]))(
        None))
    assert order == ["jobs", "benchmark"]         # the jobs end in every domain with a shell, logs or none


def test_a_run_with_no_log_and_no_shell_keeps_the_benchmarks_cleanup():
    chess = Chess()
    assert tasks.cleanup(chess, build_config("default", "small", [])) == chess.cleanup
