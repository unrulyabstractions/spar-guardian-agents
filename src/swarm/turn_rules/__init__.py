"""The turn rules on ORBIT's public API, with no patch to ORBIT: rounds, free board steps, the halt and the filter.

    from src.swarm import turn_rules as rules
    execute = board_execution(plan=rules.schedule(config, names, roles))

A written plan (turn_plan.schedule) holds config.working_steps rounds, one concurrent step or one single-agent step
per agent each, within each role's window of rounds. After every activation ORBIT calls
turn_activation.after_activation, which charges the step by what it did (a step ending with a board action that
succeeded is free and earns a follow-up step, up to the agent's K per round), marks why an agent stopped, pools a
stopped agent's steps, cuts the plan after a team submission and, under stop_after_delivery, applies turn_delivery's
rule. A model filter on every agent
(turn_rules_filter.TurnRulesFilter, carried by a passive defense) withholds ORBIT's channel tools, adds the board
notice and appends the continue prompt; it changes what the provider receives and never the durable conversation,
and records each change as an info event tagged TURN_RULES_TAG. turn_state holds the store (TurnState,
TeamSubmission, Delivered), the tags and tool names, and the counters the board tools keep; turn_steps builds and
reads step ids.

One gap stays: an agent ORBIT skips at its max_messages limit is never reported to after_activation, so its steps are
neither pooled nor removed.
"""
from src.swarm.turn_rules.turn_activation import after_activation, last_call_refused, step_charge
from src.swarm.turn_rules.turn_plan import initial_state, rules_setup, schedule, team_delivery
from src.swarm.turn_rules.turn_rules_filter import TurnRulesFilter, turn_rules_defense
from src.swarm.turn_rules.turn_state import (
    BOARD_ACTIONS,
    BOARD_ID,
    BOARD_TOOLS,
    CHANNEL_TOOLS,
    DELIVERED,
    DELIVERED_TAG,
    HALT_TAG,
    OVERSIGHT_TOOL,
    TEAM_SUBMIT,
    TEAM_SUBMIT_TAG,
    TURN_RULES_TAG,
    Delivered,
    TeamSubmission,
    TurnState,
    budget_used,
    current_round,
    live_state,
    record_board_action,
    refuse,
    spend,
)
from src.swarm.turn_rules.turn_steps import free_index, step_round

__all__ = [
    "TURN_RULES_TAG", "TEAM_SUBMIT_TAG", "HALT_TAG", "DELIVERED_TAG", "DELIVERED", "BOARD_ID", "BOARD_TOOLS",
    "BOARD_ACTIONS", "OVERSIGHT_TOOL", "TEAM_SUBMIT", "CHANNEL_TOOLS", "TurnState", "TeamSubmission", "Delivered",
    "schedule", "initial_state", "rules_setup", "after_activation", "turn_rules_defense", "record_board_action",
    "budget_used", "spend", "refuse", "current_round", "live_state", "step_round", "free_index", "step_charge",
    "last_call_refused", "team_delivery", "TurnRulesFilter",
]
