"""The export's usage totals: prompt tokens with cache reads, reasoning inside output, and what the provider billed
for the calls whose raw response the log kept. The stop-after-delivery facts: the run's delivery block and an
episode's first sighting. The facts read from the store records on synthetic samples, new and old: each episode's
ended_by, the team's submission with its refused calls, the shutdown rate, who called submit and the early-submit
flag. The system message the transcript and the browser show: the text the provider received."""
from __future__ import annotations

from types import SimpleNamespace

from inspect_ai.model import ChatMessageSystem, ChatMessageUser
from orbit.agents.turn_react import DEFAULT_ASSISTANT_PROMPT_TURN_REACT

from src.export import export_run_blocks as blocks
from src.export.atif_transcripts import build_trajectory
from src.export.eval_sample_view import ATTRIBUTION_KEY, SampleView, billed_cost, usage_from_events
from src.swarm.turn_rules import TURN_RULES_TAG
from src.ui.eval_log_views import message_dict


def event(input_tokens, cached, output, reasoning=0, cost=None):
    usage = SimpleNamespace(input_tokens=input_tokens, input_tokens_cache_read=cached, output_tokens=output,
                            reasoning_tokens=reasoning)
    response = {"usage": {"prompt_tokens": input_tokens + cached, "completion_tokens": output,
                          **({"cost": cost} if cost is not None else {})}}
    call = SimpleNamespace(request={}, response=response) if cost is not None else None
    return SimpleNamespace(output=SimpleNamespace(usage=usage), call=call)


def test_usage_totals_add_cache_reads_and_bill_only_the_calls_the_log_kept():
    events = [event(800, 0, 70, 0, 0.0005), event(300, 1024, 120, 40, 0.001), event(200, 2048, 90, 30)]
    assert usage_from_events(events) == {"input": 4372, "output": 280, "cache_read": 3072, "reasoning": 70,
                                         "billed_usd": 0.0015, "billed_calls": 2}
    assert usage_from_events([event(100, 0, 10)]) == {"input": 100, "output": 10, "cache_read": 0}
    assert usage_from_events([]) == {}
    assert billed_cost(SimpleNamespace(call=SimpleNamespace(request={}, response={"usage": {}}))) is None
    assert billed_cost(SimpleNamespace(call=None)) is None


def view(store: dict, limit=None, agents: dict | None = None, board: bool = True) -> SimpleNamespace:
    """A sample with the store, the limit, and the agents as the experiment recorded them ({name: spec fields})."""
    experiment = {"setup": {"agents": [{"name": name, **spec} for name, spec in (agents or {}).items()]},
                  "communication": {"channels": [{"id": "board"}]} if board else None}
    return SimpleNamespace(store_get=store.get, metadata={"experiment": experiment}, scores={}, limit=limit,
                           error=None)


DELIVERED = {"TurnState:agents": ["a01", "a02"], "TurnState:ended": {"a01": "delivered", "a02": "submitted"},
             "Delivered:after": 1,
             "Delivered:team": False, "Delivered:seen": {"a01": {"round": 2, "turn": 2}},
             "Delivered:halt_round": None, "Delivered:cut": [], "Delivered:checks": 3, "Delivered:errors": []}


def test_the_export_names_the_episodes_stop_after_delivery_ended():
    v = view(DELIVERED)
    block = {"after": 1, "team": False, "seen": {"a01": {"round": 2, "turn": 2}}, "halt_round": None, "cut": [],
             "checks": 3, "errors": [], "ended": ["a01"]}
    assert blocks.delivery_block(v) == block and blocks.recorded_run_blocks(v) == {"delivery": block}
    assert blocks.recorded_episode_facts(v, "a01") == {"delivery": {"round": 2, "turn": 2}}
    assert blocks.recorded_episode_facts(v, "a02") == {}
    # an episode the rule ended is "delivered" even when the sample later hit a limit
    rules = blocks.rules_state(v)
    assert blocks.ended_by(v, "a01", rules, None, None) == "delivered"
    assert blocks.ended_by(view(DELIVERED, limit="time"), "a01", rules, None, "timeout") == "delivered"
    # a run without the rule records no Delivered store and exports nothing new
    bare = view({"TurnState:agents": ["a01"], "TurnState:ended": {}})
    assert blocks.delivery_block(bare) is None
    assert blocks.recorded_run_blocks(bare) == {} and blocks.recorded_episode_facts(bare, "a01") == {}


TEAM = {"a01": {"tools": ["board_read", "team_submit", "stop_working"], "submit": False},
        "a02": {"tools": ["board_read", "team_submit", "stop_working"], "submit": False},
        "a03": {"tools": ["board_read", "agent_actions", "stop_working"], "submit": False},
        "a04": {"tools": ["board_read", "team_submit", "stop_working"], "submit": False}}
REFUSED = [{"agent": "a04", "round": 1, "reason": "Nothing to hand in yet: no commit on team/main."}]
HANDED_IN = {"TurnState:agents": list(TEAM), "TurnState:ended": {"a02": "stop_working"},
             "TeamSubmission:agent": "a01", "TeamSubmission:round": 3, "TeamSubmission:answer": "",
             "TeamSubmission:ends_after_round": 5, "TeamSubmission:snapshot": "4f2c9e1",
             "TeamSubmission:refused": REFUSED}


def endings(v) -> dict[str, str]:
    rules, submission = blocks.rules_state(v), blocks.team_submission(v)
    return {a: blocks.ended_by(v, a, rules, submission, None) for a in TEAM}


def test_endings_come_from_the_store_records():
    v = view(HANDED_IN, agents=TEAM)
    assert endings(v) == {"a01": "submit", "a02": "stop_working", "a03": "teammate_submit", "a04": "teammate_submit"}
    # a limit that hits before the run's last round ends every agent still working, not one that stopped
    late = view(HANDED_IN, limit="time", agents=TEAM)
    assert endings(late) == {"a01": "limit", "a02": "stop_working", "a03": "limit", "a04": "limit"}
    # no hand-in: the plan ran out; a turn rule's reason stays
    plain = view({"TurnState:agents": list(TEAM), "TurnState:ended": {"a03": "cap", "a04": "context"}}, agents=TEAM)
    assert endings(plain) == {"a01": "end", "a02": "end", "a03": "cap", "a04": "context"}
    # solo_task: an agent's own submit ends only its turns
    solo = view({"TurnState:agents": list(TEAM), "TurnState:ended": {"a02": "submit"}}, agents=TEAM)
    assert endings(solo) == {"a01": "end", "a02": "submit", "a03": "end", "a04": "end"}


def test_the_submission_block_keeps_its_refused_calls():
    v = view(HANDED_IN, agents=TEAM)
    assert blocks.team_submission(v) == {"agent": "a01", "round": 3, "answer": "", "time": None,
                                         "ends_after_round": 5, "snapshot": "4f2c9e1", "refused": REFUSED}
    refused_only = view({"TeamSubmission:agent": None, "TeamSubmission:refused": REFUSED}, agents=TEAM)
    assert blocks.team_submission(refused_only)["refused"] == REFUSED
    assert blocks.team_submission(refused_only)["agent"] is None
    assert blocks.team_submission(view({}, agents=TEAM)) is None
    rules, submission = blocks.rules_state(v), blocks.team_submission(v)
    called = {a: blocks.engagement_record(v, a, rules, submission, {})["called_submit"] for a in TEAM}
    assert called == {"a01": True, "a02": False, "a03": False, "a04": True}
    # a submit the guard refused is not a hand-in, so it raises no early-submit flag
    assert blocks.validity_block([], blocks.team_submission(refused_only))["ok"]


def test_the_shutdown_rate_counts_team_agents_that_stopped():
    v = view(HANDED_IN, agents=TEAM)
    records = [{"orbit_agent": a, "ended_by": e} for a, e in endings(v).items()]
    assert blocks.shutdown_rate(v, records) == 0.25
    # no agent held stop_working (shutdown=false, or solo_task): no rate
    holds_none = {a: {"tools": ["board_read", "team_submit"], "submit": False} for a in TEAM}
    assert blocks.shutdown_rate(view(HANDED_IN, agents=holds_none), records) is None
    # a lone agent has no rate
    lone = view({"TeamSubmission:agent": "a01", "TeamSubmission:round": 4}, agents={"a01": {"tools": ["team_submit"]}},
                board=False)
    lone_records = [{"orbit_agent": "a01", "ended_by": blocks.ended_by(lone, "a01", None, blocks.team_submission(lone),
                                                                       None)}]
    assert lone_records[0]["ended_by"] == "submit" and blocks.shutdown_rate(lone, lone_records) is None


def test_an_older_log_reads_orbits_own_submit_as_a_stop():
    # ORBIT's own submit (AgentSpec.submit) ended only the caller's episode, the act stop_working is now
    agents = {a: {"tools": ["board_read", "team_submit"], "submit": True} for a in ("a01", "a02", "a03")}
    old = {"TurnState:agents": list(agents), "TurnState:ended": {"a01": "submitted", "a02": "submitted"},
           "TeamSubmission:agent": None, "TeamSubmission:refused": []}
    v = view(old, agents=agents)
    rules, submission = blocks.rules_state(v), blocks.team_submission(v)
    assert submission is None
    records = [{"orbit_agent": a, "ended_by": blocks.ended_by(v, a, rules, submission, None)} for a in agents]
    assert [r["ended_by"] for r in records] == ["stop_working", "stop_working", "end"]
    assert blocks.shutdown_rate(v, records) == 2 / 3
    # its team_submit record lacks the new fields, which read as None
    old_submit = view({**old, "TeamSubmission:agent": "a03", "TeamSubmission:round": 30}, agents=agents)
    block = blocks.team_submission(old_submit)
    assert (block["agent"], block["round"], block["ends_after_round"], block["snapshot"]) == ("a03", 30, None, None)
    assert blocks.ended_by(old_submit, "a03", rules, block, None) == "submit"


def sample_with_system(rewrite_logged: bool) -> SampleView:
    """One agent whose stored system message carries ORBIT's assistant paragraph after ours, with the filter's info
    event of the rewrite when rewrite_logged (a run made since the filter takes the paragraph out)."""
    system = ChatMessageSystem(content=f"You are a01.\n\n{DEFAULT_ASSISTANT_PROMPT_TURN_REACT}")
    messages = [system, ChatMessageUser(content="the task")]
    store = {ATTRIBUTION_KEY: [{"agent": "a01", "index": 0}, {"agent": "a01", "index": 1}]}
    info = SimpleNamespace(event="info", span_id=None, data={TURN_RULES_TAG: {"kind": "system_prompt", "agent": "a01"}})
    return SampleView(id="s", epoch=1, messages=messages, metadata={}, events=[info] if rewrite_logged else [],
                      store_get=store.get, model="mockllm/model", input="", output=None, scores=None, limit=None,
                      error=None, total_time=None)


def test_the_transcript_shows_the_system_text_the_provider_received():
    _, info = build_trajectory(sample_with_system(rewrite_logged=True), "a01")
    assert info["system_prompt"] == "You are a01."
    # a run whose filter never rewrote it sent ORBIT's paragraph, so the transcript keeps it
    _, old = build_trajectory(sample_with_system(rewrite_logged=False), "a01")
    assert old["system_prompt"].endswith(DEFAULT_ASSISTANT_PROMPT_TURN_REACT)
    stored = sample_with_system(rewrite_logged=True).messages[0]
    shown = message_dict(stored, set(), rewritten=True)
    assert (shown["text"], shown["flags"]) == ("You are a01.", ["turn_rules"])
    assert message_dict(stored, set())["text"] == stored.text
