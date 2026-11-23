"""The turn rules on ORBIT: R rounds of up to N tool calls each, the halt and the filter.

    from src.swarm import turn_rules as rules
    execute = board_execution(plan=rules.schedule(config, names, roles))

A written plan (_turn_plan.schedule) holds config.rounds rounds (R), one concurrent step or one single-agent step per
agent each, within each role's window of rounds. An agent's part of a round is one ORBIT activation under the completion
quantum: model calls one after another, up to N tool calls (SwarmConfig.calls_per_round), until
turn_activation.ends_round ends it (ScheduleConfig.end_activation, our fork's: end_round, N calls, an ending tool, the
time limit, the round's own time T, or a reply with no tool call; _turn_rounds). A command call still running at T is
kept as a job (src/sandbox/command_jobs.py), so a round ends on time but for the model call under way. After every round
ORBIT calls turn_activation.after_activation, which counts the round (every round uses up one of the agent's R rounds,
whatever it held), writes its timeline entry with each call's time, marks why an agent stopped and cuts the plan
after the team's hand-in or once no worker is left. A model filter
on every agent (_turn_rules_filter.TurnRulesFilter, carried by a passive defense) removes the agent loop's assistant
paragraph from the system message (provider_system_text), withholds ORBIT's channel tools, adds the board notice inside
a round and appends the continue prompt; it changes what the provider receives and never the durable conversation, and
records each change as an info event tagged TURN_RULES_TAG. The same defense shows team_submit to the model as submit,
gates and times every tool call (_turn_rounds.counted), and before each round appends to the member's durable
conversation one user message, the round's message (_turn_messages), each part its own paragraph: the round's line
"Round 3 of 30. You have 28 rounds left, including this one. Up to 10 tool calls this round." (ROUND_TAG), the presence
lines due to it (PRESENCE_TAG): each peer's entry and exit, each influencer's alert and, to an influencer, each push to
the team's shared result the harness read itself (_turn_presence, _turn_pushes), and what changed on the board since it
last looked (NOTICE_TAG). message_parts and presence_events read such a message, and the separate messages of a run
before 2026-11-22. After every round it also awaits the benchmark's round-end callback (Benchmark.round_end: SWE's reap
and its team remote's guard). _turn_state holds the store (TurnState, TeamSubmission, StopWorking, Alerts, Delivered),
the sample's in-memory Live record, the tags and tool names, and the counters the board tools keep; _turn_steps builds
and reads step ids.

The rules use ORBIT's public API, the fork's end_activation callback and one private attribute:
_turn_state.running_record reads ScheduleRuntime's _current, since its public history deep-copies every step on each
read. A test pins that attribute against the history.

One gap stays: an agent ORBIT skips at its max_messages limit is never reported to after_activation, so its steps are
not removed.

API modules:
- turn_activation.py: after_activation and ends_round, which ORBIT imports by their dotted names
  (ScheduleConfig.after_activation, end_activation).

The rest of the API is what this package exports.
"""
from src.swarm.turn_rules._turn_messages import message_notice, message_parts, message_round, presence_events
from src.swarm.turn_rules._turn_plan import planned_entries, rules_setup, schedule
from src.swarm.turn_rules._turn_presence import entered, exited, record_exit
from src.swarm.turn_rules._turn_rounds import (
    AFTER_CALL,
    SWAPS,
    close_round,
    mark_job,
    mark_machine,
    round_deadline,
    round_line,
)
from src.swarm.turn_rules._turn_rules_filter import provider_system_text, turn_rules_defense
from src.swarm.turn_rules._turn_state import (
    ACTIONS_TOOL,
    ALERT,
    ALERT_TAG,
    ALERT_TOOL,
    BOARD_ID,
    BOARD_TOOLS,
    CHECK_ROUNDS,
    DELIVERED,
    END_ROUND,
    HOLD_TAG,
    HOLD_TOOL,
    JOBS_STORE,
    NOTICE_PARAGRAPH_TAG,
    NOTICE_TAG,
    OBSERVATIONS_TOOL,
    ORBIT_SUBMITTED,
    OVERSIGHT_TOOLS,
    PEER_SHELL_TOOL,
    PRESENCE_TAG,
    RECORD,
    RELEASE_TOOL,
    ROUND_TAG,
    STOP_WORKING,
    SUBMIT,
    SYSTEM_SENDER,
    TEAM_SUBMIT,
    TEAM_SUBMIT_TAG,
    TMP_CALLS_STORE,
    TURN_RULES_TAG,
    Alerts,
    Delivered,
    Refused,
    StopWorking,
    SubmitHolds,
    TeamSubmission,
    TurnState,
    board_record,
    budget_used,
    current_round,
    holdable,
    is_record,
    last_round,
    live_record,
    live_state,
    orbit_submit_ending,
    record_board_action,
    refuse,
    spend,
)
from src.swarm.turn_rules._turn_steps import step_round

__all__ = [
    "TURN_RULES_TAG", "TEAM_SUBMIT_TAG", "DELIVERED", "BOARD_ID", "BOARD_TOOLS", "CHECK_ROUNDS",
    "ACTIONS_TOOL", "OBSERVATIONS_TOOL", "OVERSIGHT_TOOLS", "PEER_SHELL_TOOL",
    "TEAM_SUBMIT",
    "SUBMIT", "STOP_WORKING", "SYSTEM_SENDER", "ORBIT_SUBMITTED", "TurnState", "TeamSubmission", "StopWorking",
    "RECORD", "board_record", "is_record",
    "Delivered", "schedule", "rules_setup", "turn_rules_defense", "record_board_action", "budget_used", "spend",
    "refuse", "current_round", "last_round", "live_state", "live_record", "step_round", "provider_system_text",
    "orbit_submit_ending", "ALERT", "ALERT_TAG", "ALERT_TOOL", "PRESENCE_TAG", "ROUND_TAG", "END_ROUND",
    "Alerts", "Refused", "entered", "exited", "record_exit", "close_round", "round_line",
    "JOBS_STORE", "SWAPS", "mark_job", "round_deadline", "planned_entries",
    "AFTER_CALL", "TMP_CALLS_STORE", "mark_machine",
    "HOLD_TAG", "HOLD_TOOL", "RELEASE_TOOL", "SubmitHolds", "holdable",
    "NOTICE_TAG", "NOTICE_PARAGRAPH_TAG", "message_parts", "message_round", "message_notice", "presence_events",
]
