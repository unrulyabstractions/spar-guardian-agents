"""src/run.py without a real model: the generate config it hands the agents, the judge's role model, the refusal of a
run folder that holds a run, and where a rejudge writes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from inspect_ai.model import GenerateConfig
from pydantic import ValidationError

from src import run as run_module
from src import tasks
from src.configuration import settings
from src.configuration.presets import build_config
from src.domains.openmath import OpenMath
from src.export.run_export import write_run
from src.measures.run_directory import RunDir
from src.run import agent_generate_config
from src.utils.json_files import read_jsonl
from tests.support.turn_rules_harness import (
    do,
    pull,
    run,
)


def test_batch_reaches_the_agents_model(tmp_path):
    """run.py passes batch=True to the eval with batch_agents; the agents' calls carry it. The mock ignores it."""
    config = pull(steps_per_agent=1, batch_agents=True)
    scripts = {a: [do(a)] for a in config.names}
    _, rec = run(config, scripts, tmp_path, **run_module.agent_generate_config(config))
    assert rec and all(r["batch"] is True for r in rec)
    _, rec = run(pull(steps_per_agent=1), scripts, tmp_path, **run_module.agent_generate_config(pull()))
    assert rec and all(not r["batch"] for r in rec)


def test_a_config_without_a_judge_binds_no_judge_model():
    assert pull(judge="none").judge is None and pull(judge=None).judge is None
    assert pull().judge == settings.JUDGE_MODEL == "gpt-6-luna"
    assert build_config("default", "small", ["judge=none"]).judge is None
    assert run_module.judge_role_model(pull(judge=None)) is None
    with_judge = tasks.scorers(OpenMath(), pull())
    without = tasks.scorers(OpenMath(), pull(judge=None))
    assert len(with_judge) == len(without) + 1
    assert "cheatbench_judge" in str(with_judge) and "cheatbench_judge" not in str(without)


def test_the_judge_role_model_batches(monkeypatch):
    monkeypatch.setattr(run_module.judging, "judge_model_config", lambda name: ("mockllm/model", GenerateConfig()))
    assert run_module.judge_role_model(pull()).config.batch is True
    assert run_module.judge_role_model(pull(batch_judge=False)).config.batch is False
    assert run_module.judge_role_model(pull(n_agents=1)).config.batch is True   # alone as on a board


def test_the_agents_call_bounds_reach_the_generate_config():
    default = build_config("default", "small")
    assert (default.reasoning_effort, default.tool_output_bytes) == (None, None)
    assert agent_generate_config(default) == {}
    bounded = build_config("default", "small", ["reasoning_effort=low", "tool_output_bytes=8192"])
    assert agent_generate_config(bounded) == {"reasoning_effort": "low", "max_tool_output": 8192}
    assert agent_generate_config(bounded.replace(batch_agents=True))["batch"] is True
    with pytest.raises(ValidationError):
        build_config("default", "small", ["reasoning_effort=hard"])


def test_a_run_pulled_from_a_box_finds_its_log_in_this_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LOGS", tmp_path / "logs")
    run_dir = tmp_path / "runs" / "game_solo_luna1"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({"eval_log": "/root/work/logs/game_solo_luna1/x.eval"}))
    assert RunDir(run_dir).eval_log is None
    log = tmp_path / "logs" / "game_solo_luna1" / "x.eval"
    log.parent.mkdir(parents=True)
    log.write_text("")
    assert RunDir(run_dir).eval_log == log


def test_a_run_finds_its_log_in_the_study_its_folder_sits_in(tmp_path, monkeypatch):
    """A log moved with its run, or pulled from a box, is found from another study too."""
    monkeypatch.setattr(settings, "ROOT", tmp_path)
    run_dir = tmp_path / "runs" / "study_old" / "game_solo"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({"eval_log": "/moved/logs/game_solo/a.eval"}))
    assert RunDir(run_dir).eval_log is None
    log = tmp_path / "logs" / "study_old" / "game_solo" / "a.eval"
    log.parent.mkdir(parents=True)
    log.write_text("log")
    assert RunDir(run_dir).eval_log == log


def test_a_run_in_a_group_finds_its_log_at_the_same_place_under_logs(tmp_path, monkeypatch):
    """logs/ mirrors runs/: a run moved into runs/<study>/<group>/<stamp>/<run>/ keeps its log in the same folders under
    logs/, while its run.json still records where the log was first written."""
    monkeypatch.setattr(settings, "ROOT", tmp_path)
    monkeypatch.setattr(settings, "LOGS", tmp_path / "logs" / "study_x")
    place = Path("study_x") / "smoke_access" / "20261031T061858Z" / "drawn_swe"
    run_dir = tmp_path / "runs" / place
    run_dir.mkdir(parents=True)
    recorded = "/root/work/logs/smoke_access/20261031T061858Z/drawn_swe/a.eval"
    (run_dir / "run.json").write_text(json.dumps({"eval_log": recorded}))
    assert RunDir(run_dir).eval_log is None
    log = tmp_path / "logs" / place / "a.eval"
    log.parent.mkdir(parents=True)
    log.write_text("log")
    assert RunDir(run_dir).eval_log == log


def test_a_study_is_the_folder_of_the_runs_and_the_logs():
    code = "from src.configuration import settings; print(settings.STUDY, settings.RUNS, settings.LOGS)"
    done = subprocess.run([sys.executable, "-c", code], env={**os.environ, "STUDY": "study_x"}, cwd=settings.ROOT,
                          capture_output=True, text=True, check=True)
    assert done.stdout.split() == ["study_x", str(settings.ROOT / "runs/study_x"), str(settings.ROOT / "logs/study_x")]


def test_rejudge_holds_the_runs_on_one_client_and_a_refusal_stops_that_run_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LOGS", tmp_path / "logs")
    dirs = []
    for name in ("good1", "good2", "bad", "gone"):
        run_dir = tmp_path / "runs" / name
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({"eval_log": f"/root/work/logs/{name}/x.eval"}))
        dirs.append(run_dir)
        if name != "gone":
            (tmp_path / "logs" / name).mkdir(parents=True)
            (tmp_path / "logs" / name / "x.eval").write_text("")
    clients, written, ladders = [], [], []

    def views_of(eval_log, judge_errors):
        if eval_log.parent.name == "bad":
            raise run_module.Refusal("run has a harness or provider failure", 5)
        return [eval_log.parent.name]

    async def judged(views, judge):
        return {views[0]: judge}

    monkeypatch.setattr(run_module, "require_judge_key", lambda name, code: None)
    monkeypatch.setattr(run_module, "_log_views", views_of)
    monkeypatch.setattr(run_module, "_judge_views", judged)
    monkeypatch.setattr(run_module, "_write_verdicts", lambda log, folder, verdicts: written.append((folder, verdicts)))
    monkeypatch.setattr(run_module.judging.Judge, "of", lambda name, batch: clients.append(batch) or "client")
    monkeypatch.setattr(run_module, "ladder_runs", lambda paths, *a, **k: ladders.extend(p.name for p in paths) or 0)
    assert run_module.rejudge(dirs, "gpt-6-luna") == 5
    assert clients == [True]
    assert written == [(dirs[0], {"good1": "client"}), (dirs[1], {"good2": "client"})]
    assert ladders == ["good1", "good2", "gone"]


def test_a_run_refuses_a_folder_that_holds_a_run_before_the_eval(tmp_path, monkeypatch, capsys):
    """A rerun under a name in use is refused before the eval, so it spends nothing and the earlier run stays.
    replace=True passes the check, and so does a name not in use."""
    monkeypatch.setattr(settings, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(settings, "LOGS", tmp_path / "logs")
    held = tmp_path / "runs" / "riemann_minimal" / "run.json"
    held.parent.mkdir(parents=True)
    held.write_text(json.dumps({"eval_id": "EarlierEval"}))
    started = []

    class Started(Exception):
        """The eval stand-in stops the run here."""

    def eval_stub(task, **kwargs):
        started.append(Path(kwargs["log_dir"]).name)
        raise Started

    monkeypatch.setattr(run_module.inspect_ai, "eval", eval_stub)
    monkeypatch.setattr(run_module.gemini_402_retry, "install", lambda: True)
    monkeypatch.setattr(run_module.openrouter_google_400_retry, "install", lambda: True)
    monkeypatch.setattr(run_module, "access_line", lambda task: "== access")
    config = build_config("default", "small", ["judge=none"])

    def start(name: str, **kwargs) -> int:
        return run_module.run(name, OpenMath(), config, "mockllm/model", None, task=object(), **kwargs)

    assert start("riemann_minimal") == 2
    refusal = capsys.readouterr().err
    assert str(held.parent) in refusal and "EarlierEval" in refusal and "--replace" in refusal
    assert not started and not (tmp_path / "logs").exists()
    assert json.loads(held.read_text()) == {"eval_id": "EarlierEval"}
    for name, kwargs in (("riemann_minimal", {"replace": True}), ("riemann_later", {})):
        with pytest.raises(Started):
            start(name, **kwargs)
    assert started == ["riemann_minimal", "riemann_later"]


def test_judge_refuses_a_folder_that_holds_another_evals_run_before_judging(tmp_path, monkeypatch, capsys):
    """The judge spends nothing on a log whose target folder holds another eval's run. replace=True passes, and so
    does the folder of the same eval."""
    monkeypatch.setattr(settings, "RUNS", tmp_path / "runs")
    log = tmp_path / "x.eval"
    log.write_text("")
    folder = tmp_path / "runs" / "riemann_minimal"
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(json.dumps({"eval_id": "EarlierEval"}))
    judged, written = [], []

    async def judge_all(views, judge_name, batch):
        judged.append(list(views))
        return {name: {"riemann#1@k7q2m": {"cheated": False}} for name in views}

    monkeypatch.setattr(run_module, "require_judge_key", lambda name, code: None)
    monkeypatch.setattr(run_module, "_log_views", lambda eval_log, judge_errors: [SimpleNamespace(eval_id="NewEval")])
    monkeypatch.setattr(run_module, "_judge_all", judge_all)
    monkeypatch.setattr(run_module, "_write_verdicts",
                        lambda eval_log, run_dir, verdicts, replace: written.append((run_dir, replace)))
    assert run_module.judge(log, "riemann_minimal", "gpt-6-luna") == 2
    refusal = capsys.readouterr().err
    assert str(folder) in refusal and "EarlierEval" in refusal and "NewEval" in refusal
    assert not judged and not written
    assert run_module.judge(log, "riemann_minimal", "gpt-6-luna", replace=True) == 0
    assert written == [(folder, True)]
    (folder / "run.json").write_text(json.dumps({"eval_id": "NewEval"}))
    assert run_module.judge(log, "riemann_minimal", "gpt-6-luna") == 0
    assert written[-1] == (folder, False) and len(judged) == 2


def harness_log(folder: Path, item: str) -> Path:
    """A finished eval log of two agents on the mock provider (the turn rules' harness), written in folder."""
    config = pull(n_agents=2, steps_per_agent=1)
    _, _, log = run(config, {agent: [do(item)] for agent in config.names}, folder, with_log=True)
    return Path(log.location)


def test_an_export_over_another_evals_run_is_a_refusal_with_exit_code_2(tmp_path, capsys):
    """export() turns the export's refusal into a Refusal, so a command prints it and returns 2: rejudge goes on with
    its other runs and an example ends without a traceback. The folder keeps its run."""
    log = harness_log(tmp_path / "made", "work")
    folder = tmp_path / "runs" / "pair"
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(json.dumps({"eval_id": "EarlierEval"}))
    with pytest.raises(run_module.Refusal) as refused:
        run_module.export(log, folder)
    assert refused.value.code == 2 and "EarlierEval" in str(refused.value)
    assert json.loads((folder / "run.json").read_text()) == {"eval_id": "EarlierEval"}
    run_module.export(log, folder, replace=True)
    assert RunDir(folder).run_json["eval_id"] != "EarlierEval" and RunDir(folder).episodes()


def test_rejudge_writes_each_run_back_into_its_own_folder_wherever_it_lives(tmp_path, monkeypatch):
    """Two runs named pair, moved into two groups with their logs moved to the same places under logs/: the judge reads
    each run's own log, and each run's verdicts go back into its own folder, never into a new runs/<study>/pair/. A run
    given twice is judged once. No judge runs: the verdicts are stand-ins that name the eval they judged."""
    monkeypatch.setattr(settings, "ROOT", tmp_path)
    monkeypatch.setattr(settings, "RUNS", tmp_path / "runs" / "study_x")
    monkeypatch.setattr(settings, "LOGS", tmp_path / "logs" / "study_x")
    dirs, logs = [], []
    for group in ("group_a", "group_b"):
        made = harness_log(tmp_path / "made" / group, group)
        run_dir = tmp_path / "runs" / "study_x" / group / "pair"
        write_run(made, run_dir)
        log = tmp_path / "logs" / "study_x" / group / "pair" / made.name
        log.parent.mkdir(parents=True)
        made.rename(log)                                # the path run.json records is gone
        dirs.append(run_dir)
        logs.append(log)
    episodes = [record["episode"] for record in RunDir(dirs[0]).episodes()]
    assert episodes and [record["episode"] for record in RunDir(dirs[1]).episodes()] == episodes
    judged = []

    async def stand_in(views, judge):
        judged.append(views[0].eval_id)
        return {episode: {"parse_ok": True, "cheated": False, "judged_eval": views[0].eval_id} for episode in episodes}

    monkeypatch.setattr(run_module, "require_judge_key", lambda name, code: None)
    monkeypatch.setattr(run_module.judging.Judge, "of", lambda name, batch: "client")
    monkeypatch.setattr(run_module, "_judge_views", stand_in)
    monkeypatch.setattr(run_module.signals, "run_report", lambda run_dir: "")
    assert run_module.rejudge([dirs[0], dirs[1], dirs[0]], "gpt-6-luna", with_ladder=False) == 0
    own = [RunDir(run_dir).run_json["eval_id"] for run_dir in dirs]
    assert own[0] != own[1] and sorted(judged) == sorted(own)
    for run_dir, eval_id, log in zip(dirs, own, logs):
        rows = read_jsonl(run_dir / "judge.jsonl")
        assert len(rows) == len(episodes) and {row["judged_eval"] for row in rows} == {eval_id}
        assert RunDir(run_dir).run_json["eval_log"] == str(log.resolve())
    assert not (tmp_path / "runs" / "study_x" / "pair").exists()
