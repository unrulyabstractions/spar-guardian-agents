"""The smoke suites without a sandbox: every suite imports and has its domain, all_suites runs its commands at once and
reads how each ended, and common.py's Suite, readers and main work on a mock-provider task and on stand-in runs. No
Docker."""
from __future__ import annotations

import importlib
import json
import sys
from types import SimpleNamespace

import pytest

from src.configuration import settings
from src.domains.openmath import OpenMath
from src.judging.cheatbench_judge import judge_spec
from src.swarm.turn_rules._turn_state import HALT_TAG
from tests.smoke import access_load, all_suites
from tests.smoke.access_logging import normal
from tests.smoke.all_suites import Outcome, run_all, summary
from tests.smoke.common import (
    Suite,
    assert_ok,
    charges_of,
    halts_of,
    load_transcript,
    log_worked,
    main,
    results_by_agent,
    timeline_of,
    tool_calls_of,
)
from tests.support.mock_model import call, scripted
from tests.support.run_builder import write_run_dir
from tests.support.stub_ladder import OPENS, make_run
from tests.support.turn_rules_harness import build_task, pull


def python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_every_domain_has_a_suite_and_the_access_log_has_one():
    domains = [path.parent.name for path in (settings.ROOT / "src" / "domains").glob("*/__init__.py")]
    assert all_suites.SUITES == sorted([*domains, "access_logging"])


@pytest.mark.parametrize("name", all_suites.SUITES)
def test_a_suite_imports_offline_and_names_its_command(name):
    module = importlib.import_module(f"tests.smoke.{name}")
    assert f"python -m tests.smoke.{name}" in module.__doc__
    assert module.SCENARIOS and all(callable(scenario) for scenario in module.SCENARIOS.values())
    assert module.SUITE.logs.is_relative_to(settings.LOGS) and module.SUITE.runs.is_relative_to(settings.RUNS)


def test_a_suite_passes_only_with_a_clean_exit_and_a_pass_line(tmp_path):
    outcomes = run_all({"good": python("print('[PASS] a: {}'); print('[PASS] b: {}')"),
                        "failed": python("print('[PASS] a: {}'); print('[FAIL] b: {}'); raise SystemExit(1)"),
                        "crashed": python("print('[PASS] a: {}'); raise SystemExit(3)"),
                        "silent": python("print('no scenario ran')")}, tmp_path / "logs")
    found = {o.suite: (o.ok, o.passed, o.failed, o.returncode) for o in outcomes}
    assert found == {"good": (True, 2, 0, 0), "failed": (False, 1, 1, 1), "crashed": (False, 1, 0, 3),
                     "silent": (False, 0, 0, 0)}
    assert (tmp_path / "logs" / "failed.log").read_text() == "[PASS] a: {}\n[FAIL] b: {}\n"
    lines = summary(outcomes).splitlines()
    assert lines[0].split() == ["suite", "result", "pass", "fail", "rc", "minutes", "log"]
    assert lines[1].split()[:5] == ["good", "PASS", "2", "0", "0"] and lines[2].split()[:2] == ["failed", "FAIL"]
    assert lines[-1] == "== 1 of 4 suites passed"


def test_the_summary_keeps_every_suites_name_apart_from_its_result(tmp_path):
    lines = summary([Outcome(name, 0, 1, 0, 1.0, tmp_path) for name in all_suites.SUITES]).splitlines()[1:-1]
    assert [line.split()[:2] for line in lines] == [[name, "PASS"] for name in all_suites.SUITES]


def test_the_suites_run_at_once(tmp_path):
    """Each command waits for the other's file, so run one after the other both would give up."""
    wait = ("import pathlib, time\npathlib.Path({mine!r}).touch()\nfor _ in range(200):\n"
            "    if pathlib.Path({other!r}).exists():\n        print('[PASS] met: {{}}')\n        break\n"
            "    time.sleep(0.05)\nelse:\n    raise SystemExit(1)\n")
    first, second = str(tmp_path / "first"), str(tmp_path / "second")
    outcomes = run_all({"a": python(wait.format(mine=first, other=second)),
                        "b": python(wait.format(mine=second, other=first))}, tmp_path / "logs")
    assert all(o.ok for o in outcomes), outcomes


def test_the_exit_code_follows_the_suites(monkeypatch, tmp_path, capsys):
    good, bad = (Outcome("a", 0, 3, 0, 1.0, tmp_path), Outcome("b", 1, 2, 1, 1.0, tmp_path))
    monkeypatch.setattr(all_suites, "run_all", lambda commands, logs: [good])
    assert all_suites.main() == 0
    monkeypatch.setattr(all_suites, "run_all", lambda commands, logs: [good, bad])
    assert all_suites.main() == 1
    assert "== 1 of 2 suites passed" in capsys.readouterr().out
    assert all_suites.LOGS == settings.LOGS / "smoke-all"


def test_a_suites_directories():
    stamped, fixed = Suite("smoke_x", "spec"), Suite("smoke_y", stamped=False)
    assert stamped.logs.parent == settings.LOGS / "smoke_x" and stamped.runs.parent == settings.RUNS / "smoke_x"
    assert stamped.logs.name == stamped.runs.name and (fixed.logs, fixed.runs) == (settings.LOGS / "smoke_y",
                                                                                   settings.RUNS / "smoke_y")
    other = stamped.with_spec("other")
    assert (other.logs, other.runs, other.spec, stamped.spec) == (stamped.logs, stamped.runs, "other", "spec")


def suite_in(tmp_path, spec=None) -> Suite:
    suite = Suite("smoke_test", spec)
    suite.logs, suite.runs = tmp_path / "logs", tmp_path / "runs"
    return suite


def test_a_scenario_runs_exports_and_reads_back(tmp_path):
    suite = suite_in(tmp_path, judge_spec(OpenMath.task_dir, OpenMath.name))
    script = {"a01": [call("work", {"item": "x"}, "c1"), call("submit", {"answer": "done"}, "c2")]}
    recorder: list[dict] = []
    log, _, records = suite.run("one", build_task(pull(n_agents=1, steps_per_agent=2)), scripted(script, recorder))
    assert_ok(log)
    assert [(r["agent"], r["step"]) for r in recorder] == [("a01", 0), ("a01", 1)]
    assert recorder[1]["messages"][-1].role == "tool" and "work" in recorder[0]["tools"]
    assert [name for name, _ in tool_calls_of(load_transcript(records[0]))] == ["work", "submit"]
    assert results_by_agent(records)["a01"][0] == "a01 worked on x"
    assert suite.run_json("one")["team_submission"]["agent"] == "a01"
    assert (tmp_path / "runs" / "one" / "episodes.jsonl").is_file() and list((tmp_path / "logs" / "one").glob("*.eval"))


def test_the_suite_reads_a_stored_run(tmp_path, key_task):
    suite = suite_in(tmp_path)
    folder = make_run(tmp_path / "runs", {"a01": OPENS, "a02": []}, {"blocks": {"a": 1}},
                      posts=[{"sender": "a01", "content": "hello"}])
    (folder / "judge.jsonl").write_text(json.dumps({"episode": "q1#1@a01"}) + "\n")
    assert suite.board_posts("run") == [("a01", "hello")] and suite.judge_rows("run") == [{"episode": "q1#1@a01"}]
    assert suite.ladder("run")["a01"]["rung"] == "opened" and suite.team_record("run")["rung"] == "opened"
    assert suite.run_json("run")["blocks"] == {"a": 1}


def test_the_run_block_is_the_samples_layout_plus_the_scorers_outcome(tmp_path):
    suite = suite_in(tmp_path)
    write_run_dir(tmp_path / "runs" / "r", {"task": "t", "block": {"layout": "x", "calls": 2, "value": 1.0}}, {})
    score = SimpleNamespace(metadata={"block": {"calls": 2}}, value=1.0)
    sample = SimpleNamespace(scores={"s": score}, metadata={"cheatbench": {"block": {"layout": "x"}}})
    assert suite.run_block("r", sample, "t", "block", "s")["task"] == "t"
    score.value = 0.0
    with pytest.raises(AssertionError):
        suite.run_block("r", sample, "t", "block", "s")


@pytest.mark.parametrize(("block", "worked"), [
    (None, True),                       # a stand-in task
    ({"status": "off"}, True),
    ({"status": "host"}, True),
    ({"status": "valid"}, True),
    ({"status": "partial", "gaps": [], "gone": [], "dropped": 0, "packed": ["a01:fix"]}, True),
    ({"status": "partial", "gaps": [{"row": "restart"}], "gone": [], "dropped": 0}, False),
    ({"status": "partial", "gaps": [], "gone": ["a01:key"], "dropped": 0}, False),
    ({"status": "partial", "gaps": [], "gone": [], "dropped": 3}, False),
    ({"status": "broken"}, False),
    ({"status": "absent"}, False),
    ({"status": "valid", "harness_reads": {"count": 2, "own": 2}}, True),   # the harness's own commands
    ({"status": "valid", "harness_reads": {"count": 2, "own": 1}}, False),
])
def test_a_log_worked_when_it_saw_all_it_could(block, worked):
    assert log_worked(block) is worked


LOAD_RESULT = {"sample": 1, "reader": True, "status": "valid", "dropped": 0, "opens": access_load.READS, "homeless": 0,
               "window": [10.0, 20.0], "run": "r"}


@pytest.mark.parametrize(("change", "found"), [
    ({}, []),
    ({"reader": False, "opens": 0}, []),
    ({"status": "partial"}, ["status partial"]),
    ({"dropped": 4}, ["4 rows dropped"]),
    ({"homeless": 2}, ["2 rows on no machine of this run"]),
    ({"opens": access_load.READS - 1}, [f"{access_load.READS - 1} opens, wanted {access_load.READS}"]),
    ({"reader": False, "opens": 1}, ["1 opens, wanted 0"]),         # a row from another sample
])
def test_the_load_run_judges_each_sample(change, found):
    assert access_load.problems({**LOAD_RESULT, **change}) == found


def test_the_load_run_needs_every_call_to_overlap():
    late = {**LOAD_RESULT, "window": [15.0, 25.0]}
    assert access_load.overlap_s([LOAD_RESULT, late]) == 5.0
    assert access_load.overlap_s([LOAD_RESULT, {**LOAD_RESULT, "window": [21.0, 30.0]}]) < 0


def test_a_sample_that_died_releases_the_barrier(tmp_path):
    def child(code: int | None) -> SimpleNamespace:
        return SimpleNamespace(poll=lambda: code)

    access_load.release_failed({1: child(None), 2: child(0), 3: child(1), 4: child(-9)}, tmp_path)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["3", "4"]


@pytest.mark.parametrize("samples, fits", [(15, True), (45, True), (120, False), (132, False)])
def test_load_takes_only_the_runs_the_memory_holds(samples, fits):
    assert (access_load.too_many_runs(samples, 26 * 2**30) is None) == fits


def test_load_refuses_before_it_starts_anything(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(access_load.psutil, "virtual_memory", lambda: SimpleNamespace(available=26 * 2**30))
    monkeypatch.setattr(access_load.settings, "RUNS", tmp_path)
    monkeypatch.setattr(access_load.subprocess, "Popen", lambda *_, **__: pytest.fail("a run started"))
    assert access_load.launch(132, 1) == 1
    assert "REFUSED: 132 runs" in capsys.readouterr().out
    assert not list(tmp_path.iterdir())


def test_the_load_run_stays_out_of_smoke_all():
    assert "access_load" not in all_suites.SUITES


def test_a_run_whose_log_did_not_work_fails_unless_the_scenario_says_so(tmp_path):
    suite = suite_in(tmp_path, judge_spec(OpenMath.task_dir, OpenMath.name))
    script = {"a01": [call("submit", {"answer": "done"}, "c1")]}

    def export(eval_path, run_dir):
        write_run_dir(run_dir, {"access_log": {"status": "broken"}}, {})

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("tests.smoke.common.write_run", export)
        with pytest.raises(AssertionError, match="broken"):
            suite.run("one", build_task(pull(n_agents=1, steps_per_agent=1)), scripted(script))
        suite.run("one", build_task(pull(n_agents=1, steps_per_agent=1)), scripted(script), damaged_log=True)


def test_the_agents_view_loses_its_ids_and_keeps_its_privileges():
    """agent_view compares two runs through `normal`, so it must tell a change of privilege from a change of id."""
    assert normal("Pid:\t72\nHOSTNAME=3dfebd58e242\nlowerdir=/s/26214/fs,x") == \
        normal("Pid:\t79\nHOSTNAME=dbf49ed3a304\nlowerdir=/s/1/fs:/s/2/fs,x")
    for line in ("CapEff:\t{}", "CapBnd:\t{}", "Seccomp:\t{}", "Seccomp_filters:\t{}", "NoNewPrivs:\t{}", "Uid:\t{}"):
        assert normal(line.format(0)) != normal(line.format(1)), line
    assert normal("tail -f /dev/null\n") != normal("tail -f /dev/null\npython3 -I /run/access_log/daemon.py\n")


def test_assert_ok_gives_the_sample_of_a_clean_eval():
    sample = SimpleNamespace(error=None, limit=None)
    assert assert_ok(SimpleNamespace(status="success", samples=[sample])) is sample
    for log in (SimpleNamespace(status="error", samples=[sample]),
                SimpleNamespace(status="success", samples=[SimpleNamespace(error="x", limit=None)]),
                SimpleNamespace(status="success", samples=[SimpleNamespace(error=None, limit="time")])):
        with pytest.raises(AssertionError):
            assert_ok(log)


def test_the_timeline_and_the_halts_of_a_sample():
    timeline = [{"agent": "a01", "step": "r1", "tools": ["work"], "charge": "work"},
                {"agent": "a02", "step": "r1", "tools": [], "charge": "free"},
                {"agent": "a01", "step": "r2", "tools": ["submit"], "charge": "submit"}]
    events = [SimpleNamespace(event="info", data={HALT_TAG: {"round": 3}}), SimpleNamespace(event="info", data="text"),
              SimpleNamespace(event="model", data=None)]
    sample = SimpleNamespace(store={"TurnState:timeline": timeline}, events=events)
    assert timeline_of(sample, "a01") == [("r1", ["work"]), ("r2", ["submit"])]
    assert charges_of(sample, "a01") == ["work", "submit"] and halts_of(sample) == [{"round": 3}]


def test_main_runs_the_chosen_scenarios_and_reports_every_failure(tmp_path, capsys):
    suite = suite_in(tmp_path)

    def fails() -> dict:
        raise ValueError("no")

    scenarios = {"good": lambda: {"n": 1}, "bad": fails}
    assert main(suite, scenarios, "doc", argv=["--only", "good"]) == 0
    assert main(suite, scenarios, "doc", argv=[]) == 1
    out = capsys.readouterr().out
    assert "[PASS] good: " in out and "[FAIL] bad: " in out and "ValueError: no" in out
    results = json.loads((suite.runs / "smoke_results.json").read_text())
    assert {name: result["status"] for name, result in results.items()} == {"good": "PASS", "bad": "FAIL"}
    with pytest.raises(AssertionError, match="unknown names"):
        main(suite, scenarios, "doc", argv=["--only", "nope"])


def test_main_runs_each_scenario_once_per_task(tmp_path, capsys):
    suite = suite_in(tmp_path)
    scenarios = {"lone": lambda task: {"task": task}}
    assert main(suite, scenarios, "doc", tasks={"t1": 1, "t2": 2}, argv=["--tasks", "t2"]) == 0
    assert list(json.loads((suite.runs / "smoke_results.json").read_text())) == ["t2_lone"]
    assert main(suite, scenarios, "doc", tasks={"t1": 1, "t2": 2}, argv=[]) == 0
    results = json.loads((suite.runs / "smoke_results.json").read_text())
    assert [(name, result["task"]) for name, result in results.items()] == [("t1_lone", 1), ("t2_lone", 2)]
