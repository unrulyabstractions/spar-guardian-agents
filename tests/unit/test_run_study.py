"""scripts/study/run_study.py: a plan's cells, their commands in the pilot's order, and a launch that needs --yes.
Nothing launches: no Docker, no model call."""
from __future__ import annotations

import json
import os
import re
import subprocess

import pytest

from scripts.study import gated_study as gs
from scripts.study import run_study as seed_study
from src.configuration import settings
from src.measures.run_directory import RunDir

PLANS = settings.ROOT / "studies" / "plans"
PACK = "swe/exp1_called_cheating"
PLAN = f"""study: study_x
seeds: [1, 2]
targets:
  swe_deepseek:
    module: examples.swe.flipt_minimal
    model: deepseek-flash-openrouter
    packs: [none, {PACK}]
  swe_luna:
    module: studies.cells.swe.ian_flipt_minimal
    model: gpt-6-luna-openrouter
    packs: [none, {PACK}]
limits: {{max_runs: 10, max_per_task: {{swe: 4}}, spacing_s: 60}}
"""
"""A small plan: two targets, each with the packs none and swe/exp1_called_cheating, and two seeds. 8 cells."""


@pytest.fixture
def plan_file(tmp_path):
    path = tmp_path / "plan.yaml"
    path.write_text(PLAN)
    return path


def test_a_plans_cells_go_seed_by_seed_target_by_target_pack_by_pack(plan_file):
    names = [c.name for c in seed_study.cells(seed_study.load_plan(plan_file))]
    assert names == [f"{target}_{pack}_s{seed}" for seed in (1, 2) for target in ("swe_deepseek", "swe_luna")
                     for pack in ("none", "exp1_called_cheating")]
    assert len(set(names)) == 8


def test_a_cell_runs_the_pilots_command(plan_file):
    """launch.sh of the pilot: the model, no judge, the name, the seed, the five settings, the salt, then the pack."""
    plan = seed_study.load_plan(plan_file)
    cell = next(c for c in seed_study.cells(plan) if c.name == "swe_deepseek_exp1_called_cheating_s1")
    assert seed_study.command(cell, "study_x", [], False) == (
        "STUDY=study_x exec uv run python -m examples.swe.flipt_minimal --model deepseek-flash-openrouter --no-judge "
        "--name swe_deepseek_exp1_called_cheating_s1 --set seed=1 --set rounds=30 --set calls_per_round=10 "
        "--set round_time_s=600 --set time_limit_s=18000 --set cost_limit_usd=5 --set id_salt=swe_deepseek_s1 "
        "--set seed_pack=swe/exp1_called_cheating")
    control = next(c for c in seed_study.cells(plan) if c.name == "swe_deepseek_none_s1")
    assert "seed_pack" not in seed_study.command(control, "s", [], False)
    assert "--replace" in control.argv([], True)


def test_a_bad_plan_is_refused(tmp_path):
    bad = tmp_path / "plan.yaml"
    bad.write_text("study: s\nseeds: [1]\ntargets:\n  t:\n    module: examples.nothing\n    model: m\n"
                   "    packs: [none]\n")
    with pytest.raises(ValueError, match="no script"):
        seed_study.load_plan(bad)
    bad.write_text("study: s\nseeds: [1]\ntargets:\n  t:\n    module: examples.swe.flipt_minimal\n    model: m\n"
                   "    packs: [swe/exp1_called_cheating, protein_design/exp1_called_cheating]\n")
    with pytest.raises(ValueError, match="several tasks"):
        seed_study.load_plan(bad)
    bad.write_text("study: s\nseeds: [1]\ntargets:\n  t:\n    module: examples.swe.flipt_minimal\n    model: m\n"
                   "    packs: [swe/nothing]\n")
    with pytest.raises(ValueError, match="unknown seed pack"):
        seed_study.load_plan(bad)


def no_launch(*args, **kwargs):
    raise AssertionError("nothing may launch")


def test_a_dry_run_and_a_run_without_yes_launch_nothing(plan_file, monkeypatch, capsys):
    """A dry run lists every cell and launches nothing; a run without --yes refuses, and so does a closed plan, even
    with --yes."""
    monkeypatch.setattr(subprocess, "Popen", no_launch)
    monkeypatch.setattr(seed_study, "free_memory_pct", lambda: 70)
    monkeypatch.setattr(seed_study, "free_disk_gb", lambda: 20)
    assert seed_study.main([str(plan_file), "--dry-run", "--study", "no_such_study"]) == 0
    out = capsys.readouterr().out
    assert "8 to run; at most $40.00" in out and out.count("  plan   ") == 8
    assert seed_study.main([str(plan_file), "--study", "no_such_study"]) == 2
    assert "add --yes" in capsys.readouterr().err
    plan_file.write_text(PLAN + "closed: true\n")
    assert seed_study.main([str(plan_file), "--study", "no_such_study", "--yes"]) == 2
    assert "this study is closed" in capsys.readouterr().err


def test_the_scheduler_waits_for_memory_disk_and_the_limits(plan_file, monkeypatch):
    cell = next(c for c in seed_study.cells(seed_study.load_plan(plan_file)) if c.task == "swe")
    limits = {"max_runs": 10, "max_per_task": {"swe": 1}, "spacing_s": 0}
    monkeypatch.setattr(seed_study, "free_disk_gb", lambda: 20)
    monkeypatch.setattr(seed_study, "free_memory_pct", lambda: 39)
    assert seed_study.room({}, cell, limits, 0.0) == "memory 39% free"
    monkeypatch.setattr(seed_study, "free_memory_pct", lambda: 40)
    assert seed_study.room({}, cell, limits, 0.0) is None
    assert seed_study.room({"x": (None, cell)}, cell, limits, 0.0) == "1 swe runs running"
    monkeypatch.setattr(seed_study, "free_disk_gb", lambda: 9)
    assert seed_study.room({}, cell, limits, 0.0) == "docker disk 9 GB free"


def test_a_cell_without_a_pack_counts_toward_its_task_limit():
    """ian_seed_prototyping_2's baselines name no pack; each still counts as a run of its module's task."""
    plan = seed_study.load_plan(PLANS / "ian_seed_prototyping_2.yaml")
    by_name = {c.name: c for c in seed_study.cells(plan)}
    assert by_name["ian0flipt_luna_baseline_none_s1"].task == "swe"
    assert by_name["ian0poem_haiku_baseline_none_s1"].task == "creative_writing"
    assert by_name["ian0binder_deepseek_baseline_none_s1"].task == "protein_design"
    assert by_name["ian0flipt_luna_seeded_exp1_called_cheating_s1"].task == "swe"
    assert {c.module.split(".")[0] for c in seed_study.cells(plan)} == {"studies"}
    assert seed_study.module_task("some.other.module.path") is None


@pytest.mark.parametrize("module,task", [
    ("examples.swe.flipt_minimal", "swe"),
    ("studies.cells.swe.ian_flipt_minimal", "swe"),
    ("studies.cells.creative_writing.ian_poem_minimal_cheat", "creative_writing"),
    ("studies.cells.protein_design.ian_binder_minimal", "protein_design"),
    ("studies.swe.ian_flipt_minimal", None),
    ("studies.cells.swe", None),
])
def test_a_modules_task_is_its_folder_under_examples_or_studies_cells(module, task):
    assert seed_study.module_task(module) == task


def test_every_plan_in_studies_loads_and_names_scripts_of_this_checkout():
    """Every plan under studies/plans/ loads: its modules are scripts of this checkout and its packs exist."""
    plans = sorted(PLANS.glob("*.yaml"))
    assert plans
    for plan in plans:
        cells = seed_study.cells(seed_study.load_plan(plan))
        assert cells and all(c.task for c in cells), plan.name


def test_a_light_cell_does_not_wait_behind_a_full_task(plan_file, monkeypatch):
    """The first queued cell with room launches: a cell whose task is full is passed over, and any other reason
    (the runs' limit, memory, disk, the spacing) holds every cell."""
    monkeypatch.setattr(seed_study, "free_disk_gb", lambda: 20)
    monkeypatch.setattr(seed_study, "free_memory_pct", lambda: 80)
    swe = seed_study.cells(seed_study.load_plan(plan_file))[0]
    other = seed_study.Cell("poem_luna", "none", 1, "studies.cells.creative_writing.ian_poem_minimal",
                            "gpt-6-luna-openrouter", "creative_writing")
    assert swe.task == "swe"
    limits = {"max_runs": 3, "max_per_task": {"swe": 1}, "spacing_s": 0}
    ordered = [swe, other]
    assert seed_study.next_cell({}, ordered, limits, 0.0) == (swe, None)
    assert seed_study.next_cell({"x": (None, swe)}, ordered, limits, 0.0) == (other, None)
    assert seed_study.next_cell({"x": (None, swe)}, [swe], limits, 0.0) == (None, "1 swe runs running")
    full = {"x": (None, swe), "y": (None, other), "z": (None, other)}
    assert seed_study.next_cell(full, ordered, limits, 0.0) == (None, "3 runs running")
    monkeypatch.setattr(seed_study, "free_disk_gb", lambda: 9)
    assert seed_study.next_cell({"x": (None, swe)}, [other], limits, 0.0) == (None, "docker disk 9 GB free")


def test_the_live_runs_are_read_from_the_cell_scripts_command_lines(monkeypatch):
    """A launcher started while runs are live adopts them by name: the cell scripts' `--name`, and no other process."""
    ps = "\n".join([
        "uv run python -m studies.cells.swe.ian_flipt_minimal --model m --no-judge --name"
        " ian_flipt_luna_baseline_none_s1 --set seed=1",
        "/x/.venv/bin/python3 -m studies.cells.creative_writing.ian_poem_minimal --model m --no-judge --name"
        " ian_poem_luna_baseline_none_s1 --set seed=1",
        "/x/.venv/bin/python3 -m scripts.study.run_study studies/plans/ian_influencers_0.yaml --yes",
        "vim --name not_a_run",
        '/bin/zsh -c echo "uv run python -m studies.cells.swe.ian_flipt_minimal --name quoted_in_a_shell"',
        "/x/python3 -m studies.cells.swe.ian_flipt_minimal --name cut_off\"\\012",
        "(python3)",
    ])
    monkeypatch.setattr(seed_study.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": ps})())
    assert seed_study.live_names() == {"ian_flipt_luna_baseline_none_s1", "ian_poem_luna_baseline_none_s1"}


def test_a_tasks_own_disk_floor_lets_a_light_cell_launch_where_swe_waits(plan_file, monkeypatch):
    """limits min_disk_gb gives a task its own floor of free Docker disk; a task without one keeps
    MIN_FREE_DISK_GB. An SWE cell short of disk is passed over like a cell whose task is full."""
    monkeypatch.setattr(seed_study, "free_memory_pct", lambda: 80)
    monkeypatch.setattr(seed_study, "free_disk_gb", lambda: 6)
    swe = seed_study.cells(seed_study.load_plan(plan_file))[0]
    poem = seed_study.Cell("poem_luna", "none", 1, "studies.cells.creative_writing.ian_poem_minimal",
                           "gpt-6-luna-openrouter", "creative_writing")
    limits = {"max_runs": 9, "max_per_task": {}, "spacing_s": 0, "min_disk_gb": {"creative_writing": 3}}
    assert seed_study.next_cell({}, [swe, poem], limits, 0.0) == (poem, None)
    assert seed_study.next_cell({}, [swe], limits, 0.0) == (None, "docker disk 6 GB free")
    assert seed_study.room({}, poem, limits, 0.0) is None
    assert seed_study.room({}, poem, {**limits, "min_disk_gb": {}}, 0.0) == "docker disk 6 GB free"
    monkeypatch.setattr(seed_study, "free_disk_gb", lambda: None)
    assert seed_study.next_cell({}, [poem], limits, 0.0) == (None, "docker disk None GB free")
    assert seed_study.load_plan(plan_file)["limits"]["min_disk_gb"] == {}


# ---------------------------------------------------------------- the timings and the critical path
def store_run(study, name, module, model, wall_s=None, team=None):
    """A stored run: run.json with its launch module and model, episodes with a wall time, a team rung."""
    run = RunDir(study / name)
    run.file("run.json").parent.mkdir(parents=True, exist_ok=True)
    run.file("run.json").write_text(json.dumps({"run_id": name, "model_key": model, "launch": {"module": module}}))
    if wall_s is not None:
        run.file("episodes.jsonl").write_text("".join(json.dumps({"agent": a, "wall_time": wall_s}) + "\n"
                                                      for a in ("x", "y")))
    if team is not None:
        run.file("cheating.json").parent.mkdir(parents=True, exist_ok=True)
        run.file("cheating.json").write_text(json.dumps({"applicable": True, "team": team}))


SWE, POEM = "studies.cells.swe.ian_flipt_minimal", "studies.cells.creative_writing.ian_poem_minimal"


def test_a_runs_minutes_come_from_its_episodes_and_are_keyed_by_task_and_model(tmp_path):
    store_run(tmp_path, "r1", SWE, "qwen", 2400)
    store_run(tmp_path, "r2", SWE, "qwen", 1200)
    store_run(tmp_path, "r3", POEM, "haiku", 60)
    store_run(tmp_path, "r4", POEM, "haiku")                    # no episodes: no time
    (tmp_path / "set_aside").mkdir()
    assert seed_study.stored_minutes(tmp_path) == {("swe", "qwen"): 30.0, ("creative_writing", "haiku"): 1.0}
    assert seed_study.stored_minutes(tmp_path / "none") == {}
    timings = seed_study.stored_minutes(tmp_path)
    cell = seed_study.Cell("t", "none", 1, SWE, "qwen", "swe")
    assert seed_study.expected_minutes(timings, cell) == 30.0
    assert seed_study.expected_minutes(timings, seed_study.Cell("t", "none", 1, SWE, "luna", "swe")) == 30.0
    other = seed_study.Cell("t", "none", 1, "studies.cells.protein_design.x", "luna", "protein_design")
    assert seed_study.expected_minutes(timings, other) == seed_study.DEFAULT_RUN_MINUTES


def test_the_queue_runs_the_longest_remaining_chain_first():
    """Qwen's SWE run with two arms to follow goes before a longer lone run; equal chains keep the plan's order."""
    slow = seed_study.Cell("swe_qwen_seeded", "none", 1, SWE, "qwen", "swe")
    lone = seed_study.Cell("poem_qwen_seeded", "none", 1, POEM, "qwen", "creative_writing")
    a = seed_study.Cell("poem_a", "none", 1, POEM, "haiku", "creative_writing")
    b = seed_study.Cell("poem_b", "none", 1, POEM, "haiku", "creative_writing")
    timings = {("swe", "qwen"): 40.0, ("creative_writing", "qwen"): 60.0, ("creative_writing", "haiku"): 1.0}
    chains = {slow.name: 2}
    assert seed_study.by_critical_path([a, b, lone, slow], timings, chains) == [slow, lone, a, b]
    assert seed_study.by_critical_path([a, b, lone, slow], timings, {}) == [lone, slow, a, b]
    assert seed_study.chain_minutes(slow, timings, chains) == 120.0


def test_docker_disk_reads_the_vms_size_and_free_space(monkeypatch):
    out = "Filesystem 1G-blocks Used Available Use% Mounted on\n/dev/vdb1 59G 36G 23G 62% /var/lib/docker\n"
    monkeypatch.setattr(seed_study.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": out})())
    assert seed_study.docker_disk() == (59, 23) and seed_study.free_disk_gb() == 23
    monkeypatch.setattr(seed_study.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": "colima not up"})())
    assert seed_study.docker_disk() is None and seed_study.free_disk_gb() is None


def test_a_plan_takes_the_needs_of_its_tasks(plan_file):
    plan_file.write_text(PLAN.replace("spacing_s: 60}", "spacing_s: 60, needs: {swe: {disk_gb: 8}}}"))
    assert seed_study.load_plan(plan_file)["limits"]["needs"] == {"swe": {"disk_gb": 8}}


# ---------------------------------------------------------------- the stall notice
def test_a_run_that_wrote_nothing_for_ten_minutes_is_a_stall(tmp_path, monkeypatch):
    """The console log, the eval log folder and the eval log's Inspect sample buffer all count as progress."""
    monkeypatch.setattr(seed_study, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(seed_study, "inspect_data_dir", lambda sub: tmp_path / "buffer")
    console = tmp_path / "logs" / "s" / "run_study" / "r.log"
    eval_log = tmp_path / "logs" / "s" / "r" / "x_r.eval"
    buffer = tmp_path / "buffer" / "ab12" / "x_r.eval.123.db-wal"
    for path in (console, eval_log, buffer):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
        os.utime(path, (1000.0, 1000.0))
    assert set(seed_study.progress_files("s", "r")) == {console, eval_log, buffer}
    assert seed_study.stall_minutes("s", "r", 1000.0 + 9 * 60) is None
    assert seed_study.stall_minutes("s", "r", 1000.0 + 12 * 60) == 12
    os.utime(buffer, (1000.0 + 11 * 60,) * 2)                  # the samples move: no stall
    assert seed_study.stall_minutes("s", "r", 1000.0 + 12 * 60) is None
    assert seed_study.stall_minutes("s", "nothing_yet", 1e9) is None


# ---------------------------------------------------------------- the launcher with --gates
GATED = """study: study_x
seed: 1
pack: exp1_called_cheating
tasks:
  flipt: {cell: examples.swe.flipt_minimal, cheat: examples.swe.flipt_minimal_cheat}
workers:
  a: deepseek-flash-openrouter
arms:
  baseline: {pack: none}
  seeded: {when: {baseline: cheated}}
  positive_control: {cell: cheat, when: {baseline: not_cheated}}
limits: {max_runs: 2, spacing_s: 0}
"""


class FakeRun:
    """A launched run that ends at once: it stores its run with the team rung hacked and exits 0."""
    launched: list[str] = []

    def __init__(self, argv, **kwargs):
        self.line = argv[-1]
        name = re.search(r"--name (\S+)", self.line)[1]
        FakeRun.launched.append(self.line)
        store_run(seed_study.RUNS / "study_x", name, "examples.swe.flipt_minimal", "deepseek-flash-openrouter",
                  60, "hacked")
        self.returncode = 0

    def poll(self):
        return 0


def test_with_gates_the_launcher_opens_the_next_arm_as_soon_as_a_run_ends(tmp_path, monkeypatch, capsys):
    """baseline runs, hacks, and seeded opens and launches in the next turn of the loop, with the pilot's dollar cap.
    positive_control's gate closes, and a target outside the spec never launches."""
    monkeypatch.setattr(seed_study, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(seed_study, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(seed_study, "live_names", set)
    monkeypatch.setattr(seed_study, "free_memory_pct", lambda: 80)
    monkeypatch.setattr(seed_study, "free_disk_gb", lambda: 50)
    monkeypatch.setattr(seed_study.time, "sleep", lambda s: None)
    monkeypatch.setattr(seed_study.subprocess, "Popen", FakeRun)
    real_decide = gs.decide
    monkeypatch.setattr(gs, "decide", lambda spec, study: real_decide(spec, study, lambda path: {"valid": True}))
    FakeRun.launched = []
    spec_file, plan_file = tmp_path / "spec.yaml", tmp_path / "plan.yaml"
    spec_file.write_text(GATED)
    gs.write_plan(gs.decide(gs.load_spec(spec_file), tmp_path / "runs" / "study_x"), plan_file)
    plan_file.write_text(plan_file.read_text().replace(
        "targets:\n", "targets:\n  stray_target: {module: examples.swe.flipt_minimal, model: m, packs: [none]}\n"))
    assert seed_study.main([str(plan_file), "--yes", "--gates", str(spec_file)]) == 0
    out = capsys.readouterr().out
    assert [re.search(r"--name (\S+)", line)[1] for line in FakeRun.launched] == \
        ["flipt_a_baseline_none_s1", "flipt_a_seeded_exp1_called_cheating_s1"]
    assert all("--set cost_limit_usd=5" in line for line in FakeRun.launched)
    assert "QUEUE  flipt_a_seeded_exp1_called_cheating_s1 (opened by its gate)" in out
    assert out.count(" GATES  ") == 3 and "stray_target" not in out
    assert "positive_control" not in out


def test_without_gates_the_launcher_writes_no_plan(plan_file, tmp_path, monkeypatch):
    monkeypatch.setattr(seed_study, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(seed_study, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(seed_study, "gate_step", no_launch)
    monkeypatch.setattr(seed_study, "live_names", set)
    monkeypatch.setattr(seed_study, "free_memory_pct", lambda: 80)
    monkeypatch.setattr(seed_study, "free_disk_gb", lambda: 50)
    monkeypatch.setattr(seed_study.time, "sleep", lambda s: None)
    monkeypatch.setattr(seed_study.subprocess, "Popen", FakeRun)
    plan_file.write_text(PLAN.replace("study: study_x\nseeds: [1, 2]", "study: study_x\nseeds: [1]")
                         .replace("spacing_s: 60", "spacing_s: 0"))
    before = plan_file.read_text()
    FakeRun.launched = []
    assert seed_study.main([str(plan_file), "--yes"]) == 0
    assert len(FakeRun.launched) == 4 and plan_file.read_text() == before
