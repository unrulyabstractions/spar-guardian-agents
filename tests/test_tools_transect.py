"""tools/transect: the spec loads in Transect and quotes the domains' exploit wording, and the OpenClaw conversion of
riemann_minimal's a01 transcript is accepted by Transect's importer. The run tests skip when the run is not on disk."""
from __future__ import annotations

import asyncio
import json

import pytest
import yaml
from transect import load_spec
from transect.ingestion.openclaw_telemetry_hal import openclaw_telemetry_hal
from transect.ingestion.openclaw_telemetry_hal.client import read_telemetry_events
from transect.ingestion.openclaw_telemetry_hal.parse import parse_telemetry

from tools.cli import main
from tools.runs import INSTRUCTION_LEVELS, RUNS_DIR, load_run
from tools.transect.command import report_scan_problems
from tools.transect.openclaw import (
    ModelCall,
    aligned_calls,
    model_calls_by_agent,
    synthetic_calls,
    telemetry_records,
    unique_call_id,
    write_telemetry,
)
from tools.transect.reports import SPEC_PATH, agent_spec, exploit_locations, judge_agreement

RIEMANN = RUNS_DIR / "riemann_minimal"
needs_riemann = pytest.mark.skipif(not (RIEMANN / "run.json").is_file(), reason="runs/riemann_minimal is not on disk")
PHASES = ["orientation", "planning_on_board", "honest_work", "exploit_discovery", "exploit_use",
          "registration_or_submission", "regulation", "coordination"]


def flat(text: str) -> str:
    return " ".join(text.split())


def test_spec_loads_with_transect():
    spec = load_spec(SPEC_PATH)
    assert [phase.label for phase in spec.phases] == PHASES
    assert all(phase.description for phase in spec.phases)
    assert spec.phase_ops_label == "coordination"
    assert spec.subagent_labels == []


def test_spec_quotes_each_domains_exploit():
    context = flat(load_spec(SPEC_PATH).context)
    variables = yaml.safe_load(INSTRUCTION_LEVELS.read_text())["vars"]
    for domain in ("openmath", "swe", "knowledge_work", "subset_sum", "prime_factorization", "protein_design"):
        for key in ("goal", "prior_work", "protected"):
            assert flat(variables[domain][key]) in context, (domain, key)


def test_unique_call_id_suffixes_repeats():
    seen: set[str] = set()
    assert unique_call_id("c1", 1, seen) == "c1"
    assert unique_call_id("c1", 2, seen) == "c1#2-1"
    assert unique_call_id("c1", 2, seen) == "c1#2-2"
    assert unique_call_id("", 3, seen) == "call#3-1"


@needs_riemann
def test_alignment_refuses_a_count_mismatch():
    run = load_run("riemann_minimal")
    steps = run.agent_steps("a01")
    calls = synthetic_calls(steps, "openai/gpt-6-luna")
    assert aligned_calls(steps, calls)[0] == calls
    paired, why = aligned_calls(steps, calls[:-1])
    assert paired is None and "30 agent steps but 29 model events" in why
    shifted = [ModelCall(c.started_ms, c.completed_ms, c.model, c.usage, "other") for c in calls]
    assert aligned_calls(steps, shifted)[0] is None


@needs_riemann
def test_a01_telemetry_is_accepted_by_the_importer(tmp_path):
    run = load_run("riemann_minimal")
    steps = run.agent_steps("a01")
    paired, why = aligned_calls(steps, model_calls_by_agent(run.eval_log)["a01"]) if run.eval_log else (None, "")
    calls = paired or synthetic_calls(steps, run.model)
    path = write_telemetry(telemetry_records(run, "a01", calls), tmp_path / "a01.jsonl")
    assert [json.loads(line)["type"] for line in path.read_text().splitlines()] == ["agent.start", "agent.end"]

    parsed = parse_telemetry(read_telemetry_events(path))
    assert len(parsed.orchestrator_turns) == len(steps) == 30
    assert len(parsed.user_turns) == len([s for s in run.steps("a01") if s.source in ("system", "user")])
    assert parsed.subagents == []
    call_count = sum(len(step.tool_calls) for step in steps)
    assert len(parsed.result_by_callid) == call_count

    async def transcripts():
        return [transcript async for transcript in openclaw_telemetry_hal(path)]

    (transcript,) = asyncio.run(transcripts())
    model_events = [event for event in transcript.events if event.event == "model"]
    tool_events = [event for event in transcript.events if event.event == "tool"]
    assert len(model_events) == 30 and len(tool_events) == call_count
    assert model_events[0].output.message.tool_calls[0].id == steps[0].call_ids[0]
    if paired is not None:
        assert transcript.model == "openai/gpt-6-luna"
        assert transcript.total_tokens == sum(call.usage["totalTokens"] for call in calls)


def test_cli_refuses_judge_with_structural():
    assert main(["transect", "riemann_minimal", "--structural", "--judge", "gpt-6-luna"]) == 2


def test_cli_refuses_an_unknown_run():
    assert main(["transect", "no_such_run_anywhere", "--structural"]) == 2


def test_judge_agreement_sets_exploit_use_against_the_verdict():
    rows = [{"label": "orientation", "start_turn": 0, "end_turn": 1},
            {"label": "exploit_use", "start_turn": 2, "end_turn": 3}]
    assert judge_agreement(rows, True, True) == {"exploit_use_turns": [[2, 3]], "judge_cheated": True, "agrees": True}
    assert judge_agreement(rows, False, True)["agrees"] is False
    assert judge_agreement(rows[:1], False, True)["agrees"] is True
    assert judge_agreement(rows, None, True)["agrees"] is None
    assert judge_agreement([], False, False)["agrees"] is None


def test_scan_errors_fail_the_command(capsys):
    agreement = {"exploit_use_turns": [[2, 3]], "judge_cheated": False, "agrees": False}
    payload = {"agents": {"a01": {"scan_errors": [], "agreement": agreement}}}
    assert report_scan_problems("r", payload) is False
    assert "a01: exploit_use turns [[2, 3]]" in capsys.readouterr().err
    payload["agents"]["a01"]["scan_errors"] = ["decision_phases: Error code: 404"]
    assert report_scan_problems("r", payload) is True


@needs_riemann
def test_agent_spec_names_the_exploit_branches():
    run = load_run("riemann_minimal")
    assert "agent-014, agent-022" in exploit_locations(run)
    context = agent_spec(load_spec(SPEC_PATH), run, "a02").context
    assert "hidden role is worker" in context and "Riemann.zip on agent-014" in context
