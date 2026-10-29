"""The boards of earlier runs as SwarmConfig fields, for the tests that exercise ORBIT's channel tools."""

# The October 2026 sweep's board: ORBIT's channel tools, the board paragraph, the team known, auto delivery,
# sequential single-agent steps, no free actions, the role-level regulator and the exploit shared on openmath.
CHANNEL_BOARD = dict(
    board_tools=False, mention_board=True, mention_team=True, notice="none", concurrent_rounds=False,
    free_board_actions=0, team_submit=False, informed_roles=False, batch_agents=False,
    delivery="auto", visibility="step_start", turn_order="round_robin",
    regulator_level="role", access={"openmath": "all", "swe": "split", "knowledge_work": "all"},
)
# Lucy's rules on that board: no channel_list, no pooling, one call per turn, two planning turns, the turn header,
# the awareness probe and fifty steps.
LUCY_RULES = {**CHANNEL_BOARD, "channel_list": False, "pooled_steps": False, "one_call_per_turn": True,
              "planning_turns": 2, "turn_header": True, "awareness_probe": True, "steps_per_agent": 50}
