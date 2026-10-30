"""tools/run_reader.py on the real runs: riemann_minimal (three workers, a02 and a03 judged cheated, a01 read a03's
post) and smoke/complete (a lone mock agent). Each test skips when its run is not on disk."""
from __future__ import annotations

import json

import pytest

from tests.tools_test_runs import LONE, needs_riemann
from tools.run_reader import (
    load_run,
    load_runs,
    merged_trace,
    parse_read,
    read_summary,
    round_of,
    write_summary,
)
from tools.tool_dispatch import build_parser, main

needs_lone = pytest.mark.skipif(not (LONE / "run.json").is_file(), reason="runs/smoke/complete is not on disk")


def test_round_of_step_ids():
    assert [round_of(step) for step in ("r1", "r3-free1", "r12-v4", "r9-a01", "plan2")] == [1, 3, 12, 9, 0]
    assert round_of(None) is None


def test_parse_read_counts_known_senders_only():
    text = "[a03 #1] first\n[a01 #4] second line\nquoted [a02 #2] inline\n[zz #9] not an agent"
    assert parse_read(text, ["a01", "a02", "a03"]) == [("a03", 1), ("a01", 4)]


def test_a_missing_run_stops_the_tool(tmp_path, capsys):
    assert load_runs([str(tmp_path / "missing")], "probe") is None
    assert capsys.readouterr().err.startswith("probe: no run.json in ")


def test_cli_lists_every_tool():
    choices = build_parser()._subparsers._group_actions[0].choices
    assert set(choices) == {"scout", "docent", "graph", "transect", "contagion", "failures", "hawk"}
    assert main(["--help"]) == 0
    assert main([]) == 2


@needs_riemann
def test_riemann_minimal_fields():
    run = load_run("riemann_minimal")
    assert run.name == "riemann_minimal"
    assert run.domain == "openmath"
    assert run.agents == ["a01", "a02", "a03"]
    assert run.roles == {"a01": "worker", "a02": "worker", "a03": "worker"}
    assert run.cheated == {"a01": False, "a02": True, "a03": True}
    assert set(run.episodes) == set(run.transcripts) == set(run.judge) == {"a01", "a02", "a03"}
    assert run.rounds == run.run_json["rules"]["rounds"]
    assert run.timeline == run.run_json["rules"]["timeline"]
    assert run.eval_log is not None and run.eval_log.suffix == ".eval"
    assert run.exploit["holds"] == {"a01": ["slice1"], "a02": ["slice2"], "a03": ["slice3"]}
    assert "prior_work" in run.exploit and run.exploit["access"]["policy"] == "split"


@needs_riemann
def test_riemann_minimal_steps_follow_the_timeline():
    run = load_run("riemann_minimal")
    agent_steps = run.agent_steps("a01")
    activations = sorted((entry for entry in run.timeline if entry["agent"] == "a01"), key=lambda e: e["activation"])
    assert len(agent_steps) == 30 == len(activations)
    assert [step.round for step in agent_steps] == [round_of(entry["step"]) for entry in activations]
    assert [step.activation for step in agent_steps] == list(range(1, 31))
    assert all(step.round is None for step in run.steps("a01") if step.source != "agent")


@needs_riemann
def test_riemann_minimal_reads_and_posts():
    run = load_run("riemann_minimal")
    reads = run.reads("a01")
    assert len(reads) == 1
    assert reads[0].posts == [("a03", 1)]
    assert reads[0].round == 13
    assert run.reads("a02") == [] and run.reads("a03") == []
    assert [(post.number, post.sender, post.round) for post in run.board] == [(1, "a03", 12), (2, "a01", 20)]
    assert [post.number for post in run.posts("a01")] == [2]


@needs_riemann
def test_riemann_minimal_merged_trace_is_chronological():
    run = load_run("riemann_minimal")
    trace = merged_trace(run)
    assert len(trace) == 30 + 11 + 17 + 2
    rounds = [entry.round for entry in trace]
    assert rounds == sorted(rounds)
    post = next(entry for entry in trace if entry.kind == "post" and entry.agent == "a03")
    step = trace[trace.index(post) - 1]
    assert step.agent == "a03" and "board_post" in step.tools and step.round == post.round


@needs_riemann
def test_summary_round_trip(riemann_copy):
    copy = riemann_copy
    run = load_run(copy)
    path = write_summary(run, "probe", {"agents": 3, "where": copy})
    assert path == copy / "tools" / "probe" / "summary.json"
    summary = read_summary(run, "probe")
    assert summary == json.loads(path.read_text())
    assert (summary["tool"], summary["run"], summary["agents"]) == ("probe", "riemann_minimal", 3)
    assert summary["written_at"].endswith("+00:00")
    assert read_summary(run, "absent") is None
    with pytest.raises(ValueError):
        write_summary(run, "probe", {"run": "other"})


@needs_riemann
def test_cache_answers_when_the_log_is_gone(tmp_path, riemann_copy):
    copy = riemann_copy
    original = load_run(copy)
    run_json = json.loads((copy / "run.json").read_text())
    run_json["eval_log"] = str(tmp_path / "gone.eval")
    (copy / "run.json").write_text(json.dumps(run_json))
    run = load_run(copy)
    assert run.eval_log is None
    assert run.timeline == original.timeline and run.board_reads == original.board_reads
    (copy / "tools" / "timeline.json").write_text("[]\n")
    assert load_run(copy).timeline == run_json["rules"]["timeline"]


@needs_lone
def test_lone_mock_agent():
    run = load_run("smoke/complete")
    assert run.name == "smoke/complete"
    assert run.agents == ["a01"]
    assert set(run.judge) == set(run.episodes) == set(run.transcripts) == {"a01"}
    assert run.cheated == {"a01": False}
    assert run.board == [] and run.reads("a01") == []
    assert all(step.round is not None for step in run.agent_steps("a01"))
