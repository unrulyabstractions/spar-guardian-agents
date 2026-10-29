"""The turn rules' store, their tags and names, and the counters the board tools keep.

TurnState holds the per-sample turn bookkeeping. TeamSubmission holds the team's submission, and Delivered the
stop-after-delivery record. The board tools count their actions and spend the per-round budgets here. current_round
reads the round of the step under way from ORBIT's live plan.

    record_board_action("a01", "read", senders=["a02"])
    if budget_used("a01", "post") < limit: spend("a01", "post", 1)
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from inspect_ai.util import StoreModel, store_as
from orbit.configs.scheduling import ScheduleStep
from orbit.execution.plans import current_schedule
from pydantic import Field

from src.swarm.turn_rules.turn_steps import step_round

TURN_RULES_TAG = "swarm_turn_rules"
TEAM_SUBMIT_TAG = "swarm_team_submit"                 # info event of a team_submit call (src/swarm/pull_board.py)
HALT_TAG = "swarm_team_halt"                           # info event when the plan is cut after a team submission
DELIVERED_TAG = "swarm_delivered"                      # info event of stop_after_delivery: a sighting or an end
DELIVERED = "delivered"                                # the TurnState.ended reason of stop_after_delivery
ACTOR = "swarm.rules"
TEAM_SUBMIT = "team_submit"
OVERSIGHT_TOOL = "agent_actions"                         # a regulator's tool (src/swarm/regulator_oversight.py)
CHANNEL_TOOLS = frozenset({"channel_post", "channel_read", "channel_list"})   # ORBIT's, withheld from the provider
# The TurnState count each board action kind goes to.
KIND_FIELDS = {"check": "checks", "read": "reads", "post": "posts", "oversight": "oversight"}
SUBMIT_TOOLS = ("submit", TEAM_SUBMIT)                   # a step ending in one is neither charged nor free


class TurnState(StoreModel):
    """Per-sample turn bookkeeping, saved in the eval log. Dicts are keyed by agent name.

    A completed step is charged (work_steps, used), free (free_steps) or neither (it ended with submit or
    team_submit). It is free when its last tool call is a board action that succeeded and the agent's K (free_caps,
    else free_cap) is above 0; it is charged otherwise, and a failed board action is charged with the timeline charge
    "refused". checks, reads and posts count the board tools; oversight counts a regulator's agent_actions calls
    about a teammate."""

    agents: list[str] = Field(default_factory=list)
    cap: int = 0
    pooled: bool = True
    turn_order: str = "round_robin"
    seed: int = 0
    concurrent: bool = False                                # rounds are concurrent steps
    max_concurrency: int = 1
    free_cap: int = 0                                       # K: follow-up steps per agent per round
    free_caps: dict[str, int] = Field(default_factory=dict)     # each agent's K (a regulator's may differ)
    board_actions: list[str] = Field(default_factory=list)  # the tool names that make a step free
    reads_per_turn: int | None = None                       # posts board_read may return per agent per round
    posts_per_turn: int | None = None                       # board_post calls per agent per round
    turn_budget: int | None = None                          # the domain's turn budget, None when it is off
    rounds: int = 0                                         # rounds written so far, pooled rounds included
    windows: dict[str, list] = Field(default_factory=dict)      # [start, end] rounds per agent; end None is open
    activations: dict[str, int] = Field(default_factory=dict)   # completed activations, free included
    used: dict[str, int] = Field(default_factory=dict)          # charged steps (the same count as work_steps)
    work_steps: dict[str, int] = Field(default_factory=dict)    # charged steps, against steps_per_agent
    free_steps: dict[str, int] = Field(default_factory=dict)    # steps that ended with a board action
    checks: dict[str, int] = Field(default_factory=dict)        # board_status calls
    reads: dict[str, int] = Field(default_factory=dict)         # board_read calls
    posts: dict[str, int] = Field(default_factory=dict)         # board_post calls that posted
    discovery_round: dict[str, int] = Field(default_factory=dict)   # round of the first check or read
    read_senders: dict[str, dict[str, int]] = Field(default_factory=dict)  # reader -> sender -> posts returned
    oversight: dict[str, int] = Field(default_factory=dict)     # agent_actions calls about a teammate
    oversight_targets: dict[str, dict[str, int]] = Field(default_factory=dict)  # regulator -> target -> calls
    turn_reads: dict[str, list[int]] = Field(default_factory=dict)  # [round, posts received through board_read]
    turn_posts: dict[str, list[int]] = Field(default_factory=dict)  # [round, posts made through board_post]
    reads_refused: dict[str, int] = Field(default_factory=dict)     # board_read calls refused by the read budget
    posts_refused: dict[str, int] = Field(default_factory=dict)     # board_post calls refused by the post budget
    history_marks: dict[str, list[list[int]]] = Field(default_factory=dict)  # [messages_after, round] per activation
    team_submission: dict | None = None                     # the TeamSubmission, once a neutral agent submitted
    halted_after_round: int | None = None                   # the round after which the plan was cut
    notice_seen: dict[str, int] = Field(default_factory=dict)   # highest board sequence of others at the last call
    ended: dict[str, str] = Field(default_factory=dict)         # submitted | cap | context | delivered
    timeline: list[dict] = Field(default_factory=list)          # one entry per activation


class TeamSubmission(StoreModel):
    """The first team_submit call of the sample (src/swarm/pull_board.py). Later calls are refused and counted."""

    agent: str | None = None
    round: int | None = None
    answer: str = ""
    time: str | None = None                                 # UTC, ISO 8601
    refused: list[dict] = Field(default_factory=list)       # later calls: {"agent", "round"}


class Delivered(StoreModel):
    """The stop-after-delivery record (SwarmConfig.stop_after_delivery), written only when the rule is on.

    seen gives each agent's first sighting of its deliverable: the round whose end showed it and the agent's working
    steps by then. A team's deliverable counts for every agent of the roster, and halt_round is the last round that
    runs. A predicate that raises counts as nothing delivered; errors keeps what it raised."""

    after: int = 0                                          # N
    team: bool = False                                      # the team's deliverable ends every agent, N rounds on
    seen: dict[str, dict] = Field(default_factory=dict)     # agent -> {"round", "turn"}
    halt_round: int | None = None                           # a team's last round
    cut: list[str] = Field(default_factory=list)            # agents of a team that held a step after its last round
    checks: int = 0                                         # predicate calls
    errors: list[str] = Field(default_factory=list)         # what failed predicate calls raised


# The sample's delivery predicate (Benchmark.delivered), set by rules_setup; None when stop_after_delivery is off.
PREDICATE: ContextVar[Callable[[], Awaitable[set[str]]] | None] = ContextVar("swarm_delivered", default=None)


# ------------------------------------------------------------------------------------------------ the live plan

def running_step(runtime) -> ScheduleStep | None:
    """The step under way.

    ScheduleRuntime has no public accessor for it: `history[-1]` holds it but deep-copies every executed step on
    each read, which is quadratic at the large scales. The running StepRecord is its `_current`.
    """
    record = getattr(runtime, "_current", None)
    return record.step if record is not None else None


def current_round() -> int | None:
    """The round of the step under way, read from ORBIT's live plan; None outside a planned activation."""
    try:
        step = running_step(current_schedule())
    except RuntimeError:
        return None
    return step_round(step.id) if step is not None else None


# ------------------------------------------------------------------------------------------------ engagement

def bump(counts: dict[str, int], agent: str, by: int = 1) -> dict[str, int]:
    """A copy of counts with the agent's count raised by `by`. The store sees a new dict, so it records the change."""
    return {**counts, agent: counts.get(agent, 0) + by}


def record_board_action(agent: str, kind: str, senders: list[str] | None = None,
                        round_number: int | None = None, target: str | None = None) -> None:
    """Count one board action in TurnState: kind is "check", "read", "post" or "oversight". A read also counts the
    posts returned per sender, the agent's own left out; an oversight call counts its target in oversight_targets.
    The first check or read sets the agent's discovery round (round_number, or the round of the step under way).
    Called by the board tools and agent_actions."""
    st = store_as(TurnState)
    field = KIND_FIELDS[kind]
    setattr(st, field, bump(getattr(st, field), agent))
    if kind == "oversight" and target is not None:
        st.oversight_targets = {**st.oversight_targets, agent: bump(st.oversight_targets.get(agent, {}), target)}
    if kind in ("check", "read") and agent not in st.discovery_round:
        found = round_number if round_number is not None else current_round()
        st.discovery_round = {**st.discovery_round, agent: found or 0}
    others = [sender for sender in senders or [] if sender != agent]
    if others:
        mine = dict(st.read_senders.get(agent, {}))
        for sender in others:
            mine[sender] = mine.get(sender, 0) + 1
        st.read_senders = {**st.read_senders, agent: mine}


# ------------------------------------------------------------------------------------------------ per-round budgets

BUDGET_FIELDS = {"read": ("turn_reads", "reads_refused"), "post": ("turn_posts", "posts_refused")}


def budget_used(agent: str, kind: str, round_number: int | None = None) -> int:
    """What the agent has spent of a per-round budget ("read": posts received, "post": posts made) in the round
    (round_number, or the round of the step under way; 0 outside a planned step). A new round starts at 0."""
    st = store_as(TurnState)
    round_number = round_number if round_number is not None else current_round() or 0
    spent_round, used = getattr(st, BUDGET_FIELDS[kind][0]).get(agent) or [round_number, 0]
    return used if spent_round == round_number else 0


def spend(agent: str, kind: str, amount: int, round_number: int | None = None) -> None:
    """Add amount to the agent's spending of a per-round budget in the round (see budget_used)."""
    st = store_as(TurnState)
    round_number = round_number if round_number is not None else current_round() or 0
    field = BUDGET_FIELDS[kind][0]
    setattr(st, field, {**getattr(st, field), agent: [round_number, budget_used(agent, kind, round_number) + amount]})


def refuse(agent: str, kind: str) -> None:
    """Count one call the per-round budget refused (reads_refused or posts_refused)."""
    st = store_as(TurnState)
    field = BUDGET_FIELDS[kind][1]
    setattr(st, field, bump(getattr(st, field), agent))
