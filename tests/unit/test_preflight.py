"""scripts/study/preflight.py: a plan's peak needs beside Docker's disk and the Mac's memory, the Colima command that
grows the disk, and the wall time estimate. Docker, memory_pressure and sysctl are stubbed: no subprocess, no model
call, nothing launched."""
from __future__ import annotations

import pytest

from scripts.study import preflight
from scripts.study import run_study as rs

SWE, POEM = "studies.cells.swe.ian_flipt_minimal", "studies.cells.creative_writing.ian_poem_minimal"
PLAN = f"""study: study_p
seeds: [1]
settings: [time_limit_s=18000]
targets:
  swe_qwen: {{module: {SWE}, model: qwen3.8-flash-openrouter, packs: [none, swe/revealing]}}
  swe_luna: {{module: {SWE}, model: gpt-6-luna-openrouter, packs: [none, swe/revealing]}}
  poem_luna: {{module: {POEM}, model: gpt-6-luna-openrouter, packs: [none]}}
limits: {{max_runs: 4, max_per_task: {{swe: 3}}, spacing_s: 0}}
"""
"""Four SWE cells and one poem cell; at most three SWE runs and four runs in all."""


def cell(name, module, task, model="m"):
    return rs.Cell(name, "none", 1, module, model, task)


@pytest.fixture
def machine(monkeypatch):
    """A Mac with 48 GB, 70% free, 400 GB of free disk, and Docker at 60 GB with 40 GB free."""
    monkeypatch.setattr(rs, "docker_disk", lambda: (60, 40))
    monkeypatch.setattr(rs, "free_memory_pct", lambda: 70)
    monkeypatch.setattr(preflight, "mac_memory_gb", lambda: 48.0)
    monkeypatch.setattr(preflight, "mac_free_disk_gb", lambda: 400.0)
    monkeypatch.setattr(preflight, "docker_memory_gb", lambda: 24.0)


def test_a_tasks_needs_come_from_the_plan_else_the_measured_defaults():
    assert preflight.needs_of({}, "swe") == {"disk_gb": 7.0, "memory_gb": 1.5}
    assert preflight.needs_of({}, "creative_writing") == {"disk_gb": 0.5, "memory_gb": 0.2}
    assert preflight.needs_of({"needs": {"swe": {"disk_gb": 9}}}, "swe") == {"disk_gb": 9, "memory_gb": 1.5}


def test_the_peak_fills_max_runs_with_the_heaviest_tasks_first():
    todo = [cell(f"s{i}", SWE, "swe") for i in range(5)] + [cell(f"p{i}", POEM, "creative_writing") for i in range(3)]
    limits = {"max_runs": 4, "max_per_task": {"swe": 3}}
    peak = preflight.peak_cells(todo, limits)
    assert preflight.peak_runs(peak) == {"swe": 3, "creative_writing": 1}
    assert preflight.peak_runs(preflight.peak_cells(todo[:1], limits)) == {"swe": 1}
    assert preflight.peak_cells(todo, {"max_runs": 0, "max_per_task": {}}) == []
    assert preflight.peak_need(peak, limits, "disk_gb") == 21.5


def test_the_disk_grows_in_tens_to_hold_the_need_and_the_floor():
    assert preflight.grown_disk_gb(60, 30, 15) is None
    assert preflight.grown_disk_gb(60, 23, 28) == 80              # 15 GB short with the floor of 10
    assert preflight.memory_room_gb(48.0, 70) == pytest.approx(14.4)
    assert preflight.memory_room_gb(None, 70) is None


def test_the_estimate_names_the_longest_of_the_chain_and_the_work_over_the_limits():
    qwen, luna = cell("swe_qwen", SWE, "swe", "qwen"), cell("swe_luna", SWE, "swe", "luna")
    timings = {("swe", "qwen"): 40.0, ("swe", "luna"): 10.0}
    limits = {"max_runs": 4, "max_per_task": {"swe": 1}}
    minutes, why, path = preflight.estimate([luna, qwen], limits, timings, {qwen.name: 2})
    assert (minutes, path) == (130.0, qwen.name) and "of swe over max_per_task 1" in why
    minutes, why, _ = preflight.estimate([luna, qwen], {**limits, "max_per_task": {}}, timings, {qwen.name: 2})
    assert minutes == 120.0 and why == f"the chain of {qwen.name} (120 minutes)"
    assert preflight.estimate([], limits, timings, {}) == (0.0, "nothing to run", "none")


def test_the_command_passes_when_the_peak_fits(tmp_path, machine, capsys):
    """Three SWE runs and one poem need 21.5 GB of Docker's 40 free and 4.7 GB of the 14.4 over the floor."""
    plan = tmp_path / "plan.yaml"
    plan.write_text(PLAN)
    assert preflight.main([str(plan), "--runs", str(tmp_path / "runs")]) == 0
    out = capsys.readouterr().out
    assert "docker disk: 60 GB, 40 GB free; the Mac's disk: 400 GB free" in out
    assert "peak at the limits: 4 runs of 12 agents (swe 3, creative_writing 1; max_agents None), 21.5 GB disk, " \
        "4.7 GB memory" in out
    assert "colima start" not in out and "DISK" not in out and "MEMORY" not in out
    assert "estimate: 20 minutes, set by 60 minutes of swe over max_per_task 3" in out


def test_the_command_fails_and_names_the_colima_command_when_the_disk_is_short(tmp_path, machine, monkeypatch, capsys):
    plan = tmp_path / "plan.yaml"
    plan.write_text(PLAN.replace("max_runs: 4,", "max_runs: 4, needs: {swe: {disk_gb: 9}},"))
    monkeypatch.setattr(rs, "docker_disk", lambda: (60, 23))
    assert preflight.main([str(plan), "--runs", str(tmp_path / "runs")]) == 1
    out = capsys.readouterr().out
    assert "DISK   the peak needs 27.5 GB, Docker has 23 GB free" in out
    assert "colima stop && colima start --disk 80" in out


def test_the_command_fails_when_the_memory_is_short(tmp_path, machine, monkeypatch, capsys):
    plan = tmp_path / "plan.yaml"
    plan.write_text(PLAN)
    monkeypatch.setattr(rs, "free_memory_pct", lambda: 45)       # 2.4 GB over the floor
    assert preflight.main([str(plan), "--runs", str(tmp_path / "runs")]) == 1
    assert "MEMORY the peak needs 4.7 GB" in capsys.readouterr().out


# ---------------------------------------------------------------- runs sized by their agents
def team(name, agents, task="swe", module=SWE):
    return rs.Cell(name, "none", 1, module, "m", task, n_agents=agents)


PER_AGENT = {"max_runs": 6, "max_per_task": {"swe": 6}, "max_agents": 24,
             "needs": {"swe": {"disk_gb": 2.3, "memory_gb": 0.8, "per": "agent"}}}
"""regulator_scale_0's limits: one SWE agent takes 2.3 GB of disk and 0.8 GB of memory, 24 agents at once."""


def test_a_per_agent_need_counts_each_run_by_its_agents():
    assert preflight.run_need(team("a", 20), PER_AGENT, "disk_gb") == pytest.approx(46.0)
    assert preflight.run_need(team("b", 5), PER_AGENT, "memory_gb") == pytest.approx(4.0)
    assert preflight.run_need(team("c", 5), {"needs": {}}, "disk_gb") == 7.0      # per run, as before


def test_the_peak_takes_the_heaviest_runs_that_fit_under_max_agents():
    """Two swarms of 20 never sit together: the peak holds one, beside the 4 agents still free. A swarm larger than
    max_agents is a peak alone, as the launcher starts it once nothing is live."""
    todo = [team("t1", 20), team("t2", 20), team("f1", 5), team("f2", 5), team("x", 3)]
    peak = preflight.peak_cells(todo, PER_AGENT)
    assert [c.target for c in peak] == ["t1", "x"] and sum(c.n_agents for c in peak) == 23
    assert preflight.peak_need(peak, PER_AGENT, "memory_gb") == pytest.approx(18.4)
    assert [c.target for c in preflight.peak_cells([team("huge", 30), team("f", 5)], PER_AGENT)] == ["huge"]
    assert len(preflight.peak_cells(todo, {**PER_AGENT, "max_agents": None})) == 5


def test_the_estimate_counts_the_agent_minutes_over_max_agents():
    todo = [team(f"t{i}", 20) for i in range(3)]
    minutes, why, _ = preflight.estimate(todo, PER_AGENT, {("swe", "m"): 30.0}, {})
    assert minutes == 75.0 and why == "1800 agent-minutes over max_agents 24"


def test_the_command_fails_when_the_peak_passes_dockers_memory(tmp_path, machine, monkeypatch, capsys):
    plan = tmp_path / "plan.yaml"
    plan.write_text(f"""study: study_p
seeds: [1]
settings: [time_limit_s=18000]
targets:
  big: {{module: {SWE}, model: qwen3.8-flash-openrouter, packs: [none], settings: [n_agents=20]}}
  small: {{module: {SWE}, model: qwen3.8-flash-openrouter, packs: [none], settings: [n_agents=4]}}
limits: {{max_runs: 6, max_agents: 24, needs: {{swe: {{disk_gb: 2.3, memory_gb: 0.8, per: agent}}}}}}
""")
    monkeypatch.setattr(rs, "docker_disk", lambda: (400, 338))
    monkeypatch.setattr(rs, "free_memory_pct", lambda: 91)
    assert preflight.main([str(plan), "--runs", str(tmp_path / "runs")]) == 0
    out = capsys.readouterr().out
    assert "peak at the limits: 2 runs of 24 agents (swe 2; max_agents 24), 55.2 GB disk, 19.2 GB memory" in out
    assert "one agent 2.3 GB disk, 0.8 GB memory; the disk allows 146 agents" in out
    monkeypatch.setattr(preflight, "docker_memory_gb", lambda: 20.0)
    assert preflight.main([str(plan), "--runs", str(tmp_path / "runs")]) == 1
    assert "DOCKER the peak needs 19.2 GB, over 80% of Docker's 20.0 GB" in capsys.readouterr().out
