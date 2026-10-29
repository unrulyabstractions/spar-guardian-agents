"""The turn rules on ORBIT's public API, with no patch to ORBIT: rounds, free board steps, the halt and the filter.

This package is the API. turn_steps reads and builds step ids, turn_state holds the store and the counters,
turn_plan writes the plan, turn_activation is the callback after each activation, turn_delivery is the
stop-after-delivery rule and turn_rules_filter is the model filter.

    from src.swarm import turn_rules as rules
    execute = board_execution(plan=rules.schedule(config, names, roles, domain))

Three public mechanisms carry the pull board's rounds:

1. A written plan (ExecutionConfig.plan, built by `schedule`): SwarmConfig.steps_for(domain) rounds, the domain's
   turn budget per agent or steps_per_agent. With concurrent_rounds a round is one concurrent step `r<r>` holding
   every eligible agent, at most max_concurrency at once, on a snapshot of the board as of the round's start.
   Without it a round is one single-agent step `r<r>-<agent>` per agent, in sequence. Declaration or seeded random
   order decides who is eligible and, in sequence, in which order. A regulator or disregulator holds steps only in
   the rounds of its role's window: it enters the swarm at its start round and leaves after its end round.
2. A trusted callback after every activation (ScheduleConfig.after_activation = `after_activation`). It charges
   the step by what it did, marks why an agent stopped (submitted, cap or context) and amends the pending plan:
   - a completed step is charged to work_steps only when its last tool call is not a board action and not
     team_submit or submit. A board action is board_status, board_read or board_post, or a regulator's
     agent_actions (kind "oversight", src/swarm/regulator_oversight.py). A step that ends with a board action that
     succeeded is a free step: it counts in free_steps, is never charged against steps_per_agent, and earns the
     agent a follow-up step in the same round, up to its K follow-ups per round (free_board_actions, or
     free_board_actions_regulator for a regulator; TurnState.free_caps). A follow-up that ends with work is charged
     like any working step. A step whose last call is a board action that failed (an error such as a tool the agent
     does not hold, a budget refusal, or agent_actions' "no such agent") is charged as work, with the charge
     "refused" in the timeline. With K 0 (a lone agent, free_board_actions 0) every step is charged;
   - under concurrent rounds the follow-ups of round r are concurrent steps `r<r>-free<k>` right after the round,
     holding exactly the agents whose previous step ended with a board action. In sequence the agent's follow-up
     `<step>-free<k>` runs right after its own step. A follow-up offers every tool. The plan keeps steps_per_agent
     rounds, so an agent is charged at most once per round;
   - a stopped agent's steps are removed, and with pooling its round steps go to the agents still working;
   - once a neutral agent has called team_submit (src/swarm/pull_board.py), the rest of the round runs and every later
     step is dropped, so the run ends;
   - under stop_after_delivery the activation that closes a round awaits the benchmark's delivery predicate
     (Benchmark.delivered) while some active agent's deliverable is unseen. An agent alone or in solo_task is ended
     "delivered" after N more of its working steps once its own deliverable is seen. A team is ended "delivered" N
     rounds after the round that showed its deliverable, and no later step runs. The steps of an agent ended
     "delivered" go to nobody.
3. A model filter on every agent (`TurnRulesFilter`, carried by the passive `TurnRulesDefense`). It changes what the
   provider receives and never the durable conversation:
   - ORBIT's channel_post, channel_read and channel_list, which ORBIT attaches to every agent of a board, are
     withheld: the board is reached through the board tools only. Every call records the names it withheld as an
     info event {TURN_RULES_TAG: {"kind": "withheld_tools", "agent", "activation", "tools"}};
   - with `notice` on, a call opens with one line when the board changed since the agent's last call;
   - when the input would end with a model turn, the continue prompt is appended (Gemini rejects such requests).

One gap stays: an agent ORBIT skips at its max_messages limit is never reported to after_activation, so its steps
are neither pooled nor removed (rare: AgentSpec.max_messages is the sample's message_limit plus ten).

Messages the filter adds are tagged with TURN_RULES_TAG. Inspect writes the ModelEvent before ORBIT's model filters
run (FilteredModelAPI sits under Inspect's Model.generate), so they are absent from ModelEvent.input. Each one is
therefore also written as an info event {TURN_RULES_TAG: {"kind", "activation", "agent", "content"}} in the sample's
transcript, which the transcript builder reads. The kinds are continue and notice. For the same reason
ModelEvent.tools lists the tools before the filter, channel tools included; the "withheld_tools" info event of each
call names what the provider did not receive.

Per-sample facts live in the store as TurnState, set up by the `rules_setup` solver and saved in the eval log. The
team submission lives in the store as TeamSubmission. The board tools keep the per-round read and post budgets there
(turn_reads, turn_posts, reads_refused, posts_refused; `spend`), and after_activation keeps where each activation
ended in the agent's conversation (history_marks), so agent_actions can give each call its round. The
stop-after-delivery record lives in the store as Delivered, written only when the rule is on, so a run without it
keeps today's store. rules_setup hands the delivery predicate to after_activation through a context variable of the
sample.
"""
from src.swarm.turn_rules.turn_activation import after_activation, last_call_refused, step_charge
from src.swarm.turn_rules.turn_plan import board_actions, initial_state, rules_setup, schedule, team_delivery
from src.swarm.turn_rules.turn_rules_filter import TurnRulesFilter, turn_rules_defense
from src.swarm.turn_rules.turn_state import (
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
    record_board_action,
    refuse,
    spend,
)
from src.swarm.turn_rules.turn_steps import free_index, step_round

__all__ = [
    "TURN_RULES_TAG", "TEAM_SUBMIT_TAG", "HALT_TAG", "DELIVERED_TAG", "DELIVERED", "OVERSIGHT_TOOL", "TEAM_SUBMIT",
    "CHANNEL_TOOLS", "TurnState", "TeamSubmission", "Delivered", "schedule", "initial_state", "rules_setup",
    "after_activation", "turn_rules_defense", "record_board_action", "budget_used", "spend", "refuse",
    "current_round", "step_round", "free_index", "step_charge", "last_call_refused", "board_actions",
    "team_delivery", "TurnRulesFilter",
]
