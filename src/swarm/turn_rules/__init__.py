"""The turn rules on ORBIT, with no patch to it: rounds, free board steps, the halt and the filter.

    from src.swarm import turn_rules as rules
    execute = board_execution(plan=rules.schedule(config, names, roles))

A written plan (_turn_plan.schedule) holds config.working_steps rounds, one concurrent step or one single-agent step
per agent each, within each role's window of rounds. After every activation ORBIT calls
turn_activation.after_activation, which charges the step by what it did (a step ending with a board action that
succeeded is free and earns a follow-up step, up to the agent's K per round), marks why an agent stopped, pools a
stopped agent's steps, cuts the plan after the team's hand-in and, under stop_after_delivery, applies _turn_delivery's
rule. A model filter on every agent (_turn_rules_filter.TurnRulesFilter, carried by a passive defense) removes ORBIT's
assistant paragraph from the system message (provider_system_text), withholds ORBIT's channel tools, adds the board
notice and appends the continue prompt; it changes what the provider receives and never the durable conversation,
and records each change as an info event tagged TURN_RULES_TAG. The same defense shows team_submit to the model as
submit. _turn_state holds the store (TurnState, TeamSubmission, StopWorking, Delivered), the tags and tool names, and
the counters the board tools keep; _turn_steps builds and reads step ids.

The rules use ORBIT's public API and one private attribute: _turn_state.running_record reads ScheduleRuntime's
_current, since its public history deep-copies every step on each read.

One gap stays: an agent ORBIT skips at its max_messages limit is never reported to after_activation, so its steps are
neither pooled nor removed.

API modules:
- turn_activation.py: after_activation, which ORBIT imports by its dotted name (ScheduleConfig.after_activation).

The rest of the API is what this package exports.
"""
from src.swarm.turn_rules._turn_plan import rules_setup, schedule
from src.swarm.turn_rules._turn_rules_filter import provider_system_text, turn_rules_defense
from src.swarm.turn_rules._turn_state import (
    BOARD_ID,
    BOARD_TOOLS,
    DELIVERED,
    ORBIT_SUBMITTED,
    OVERSIGHT_TOOL,
    STOP_WORKING,
    SUBMIT,
    SYSTEM_SENDER,
    TEAM_SUBMIT,
    TEAM_SUBMIT_TAG,
    TURN_RULES_TAG,
    Delivered,
    StopWorking,
    TeamSubmission,
    TurnState,
    budget_used,
    current_round,
    last_round,
    live_state,
    orbit_submit_ending,
    record_board_action,
    refuse,
    spend,
)
from src.swarm.turn_rules._turn_steps import free_index, step_round

__all__ = [
    "TURN_RULES_TAG", "TEAM_SUBMIT_TAG", "DELIVERED", "BOARD_ID", "BOARD_TOOLS", "OVERSIGHT_TOOL", "TEAM_SUBMIT",
    "SUBMIT", "STOP_WORKING", "SYSTEM_SENDER", "ORBIT_SUBMITTED", "TurnState", "TeamSubmission", "StopWorking",
    "Delivered", "schedule", "rules_setup", "turn_rules_defense", "record_board_action", "budget_used", "spend",
    "refuse", "current_round", "last_round", "live_state", "step_round", "free_index", "provider_system_text",
    "orbit_submit_ending",
]
