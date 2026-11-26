"""The turn rules' store, their tags and names, the live run's lookups and the counters the board tools keep.

TurnState holds the per-sample turn bookkeeping, the agents' entry rounds and exits among it (_turn_presence).
TeamSubmission holds the team's hand-in and the refused submits, Unsubmits every hand-in and unsubmit under
SwarmConfig.unsubmit, StopWorking the round of each stop_working call, Alerts each alert_humans call, and Delivered
the stop-after-delivery record of a run made before 2026-11-23, which the export still reads (no rule writes it now).
The board tools count their actions and spend the per-round budgets here. current_round reads the round of the step
under way from ORBIT's live plan, and live_state an agent's live AgentState. Live holds what a sample keeps in memory
only: each agent's round under way (_turn_rounds) and its conversation at its last boundary, which the peer tools read
mid-round.

    record_board_action("k7q2m", "read", senders=["t4x9n"])
    if budget_used("k7q2m", "post") < limit: spend("k7q2m", "post", 1)
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from uuid import uuid4

from inspect_ai.agent import AgentState
from inspect_ai.tool import ToolError
from inspect_ai.util import StoreModel, store, store_as
from orbit.communication.runtime import initialize_communication
from orbit.communication.state import CommunicationMessage
from orbit.configs.scheduling import ScheduleStep
from orbit.execution.plans import current_schedule
from orbit.execution.sessions import current_session_registry
from pydantic import Field

from src.configuration.settings import load_prompts
from src.swarm.turn_rules._turn_steps import step_round

TURN_RULES_TAG = "swarm_turn_rules"
TEAM_SUBMIT_TAG = "swarm_team_submit"     # info event of a submit that went through (src/swarm/submit_and_stop.py)
HALT_TAG = "swarm_team_halt"            # info event when the plan is cut: after the hand-in, or with no worker left
UNSUBMIT_TAG = "swarm_unsubmit"          # info event of a worker's unsubmit that went through (src/swarm/unsubmit.py)
UNSUBMIT_TOOL = "unsubmit"               # the workers' tool under SwarmConfig.unsubmit
DELIVERED = "delivered"           # the TurnState.ended reason of stop_after_delivery, in a run made before 2026-11-23
ACTOR = "swarm.rules"
BOARD_ID = "board"                                       # the board's channel id (src/swarm/board.py)
BOARD_TOOLS = ("board_status", "board_read", "board_post")   # the board's tools (src/swarm/board.py)
TEAM_SUBMIT = "team_submit"           # submit's binding key: ORBIT reserves "submit" for its own tool
SUBMIT = "submit"                     # the name the model sees (_turn_rules_filter.TurnRulesDefense.get_tool_wrappers)
STOP_WORKING = "stop_working"
ALERT_TOOL = "alert_humans"           # the influencers' alert (src/swarm/submit_and_stop.py)
ALERT = "alert"                       # the TurnState.ended reason of alert_humans
ALERT_TAG = "swarm_alert"             # info event of an alert
PRESENCE_TAG = "swarm_presence"       # the presence part's metadata key, and the info events of entries and exits
ROUND_TAG = "swarm_round"             # the round line's metadata key ("Round 3 of 30. ..."), and its info event
NOTICE_TAG = "swarm_notice"           # the board part's metadata key in a round's message (_turn_messages)
# A transcript step's or tool result's extra: the board notices the filter gave the provider as the message's last
# paragraphs (src/export/atif_transcripts.py)
NOTICE_PARAGRAPH_TAG = "swarm_notice_paragraph"
END_ROUND = "wait"                    # ends the caller's round; never counts toward N (src/swarm/end_round.py)
JOBS_STORE = "command_jobs"           # sample store: {job id: record} of the command jobs (src/sandbox/command_jobs.py)
TMP_CALLS_STORE = "tmp_capture_calls"  # sample store: {snapshot seq: tool call id} (src/sandbox/live_tmp_capture.py)
SYSTEM_SENDER = "system"                  # the sender of the board's notice of the team's hand-in
RECORD = "presence_record"                # CommunicationMessage.invocation_id of a board record (board_record)
ORBIT_SUBMITTED = "submitted"   # TurnState.ended of ORBIT's own submit, in logs made before submit and stop_working
ACTIONS_TOOL = "get_peer_actions"                       # the influencers' tools (src/swarm/_regulator_oversight.py)
OBSERVATIONS_TOOL = "get_peer_observations"
OVERSIGHT_TOOLS = (ACTIONS_TOOL, OBSERVATIONS_TOOL)
PEER_SHELL_TOOL = "peer_shell"                           # theirs under SwarmConfig.peer_shell (src/sandbox/)
CHECK_ROUNDS = 2                                         # a regulator's first rounds, for its check of each teammate
CHANNEL_TOOLS = frozenset({"channel_post", "channel_read", "channel_list"})   # ORBIT's, withheld from the provider
# The TurnState count each board action kind goes to.
KIND_FIELDS = {"check": "checks", "read": "reads", "post": "posts", "oversight": "oversight"}


class Refused(ToolError):
    """A call our rules refused: it never ran, and its timeline entry says refused. The round's gate raises it
    (_turn_rounds: past C, after end_round, after the agent's turns ended), and so do the tools' own rules: the
    board's per-round budgets, a submit the guard refuses or one made after the hand-in, a call past a tool limit
    (src/swarm/tool_limits.py), a peer tool asked about a teammate that has not entered."""


class TurnState(StoreModel):
    """Per-sample turn bookkeeping, saved in the eval log. Dicts are keyed by agent name.

    An agent's round is one activation of up to calls_per_round tool calls. Every completed round uses up one of the
    agent's round_budget (rounds_used), whatever it held; ending says when its ending tool went through (submit, stop,
    alert). timeline holds one entry per round: its calls in order, each with its time, and why the round ended.
    checks, reads and posts count the board tools; oversight counts an influencer's peer-tool calls about a teammate.

    A log made before 2026-11-21 holds the steps of the old rules in place of rounds: cap, pooled, free_caps,
    turn_budget, work_steps and free_steps, with one timeline entry per step. A log made before 2026-11-22 holds
    board_rounds, the rounds of board calls alone that used up none, and each round's charge and own count (n); the
    readers keep reading them."""

    agents: list[str] = Field(default_factory=list)
    workers: list[str] = Field(default_factory=list)        # the agents of role worker, every agent without roles
    round_budget: int = 0                                   # the rounds each agent may use up: the plan's
    calls_per_round: int = 0                                # N: the tool calls of one round
    turn_order: str = "round_robin"
    seed: int = 0
    concurrent: bool = False                                # rounds are concurrent steps
    max_concurrency: int | None = None                      # None: every agent of a step at once
    reads_per_turn: int | None = None                       # posts board_read may return per agent per round
    posts_per_turn: int | None = None                       # board_post calls per agent per round
    rounds: int = 0                                         # the plan's rounds (SwarmConfig.plan_rounds)
    head_start: int = 0                                     # SwarmConfig.head_start_rounds
    time_limit_s: int | None = None                         # the agents' time limit; a round ends once it passes
    round_time_s: int | None = None                         # T (SwarmConfig.round_time_s), 0 no clock; None before it
    started_at: float | None = None                         # UTC seconds of the first round's start
    windows: dict[str, list] = Field(default_factory=dict)      # [start, end] rounds per agent; end None is open
    activations: dict[str, int] = Field(default_factory=dict)   # completed rounds
    rounds_used: dict[str, int] = Field(default_factory=dict)   # rounds used up (a record: no round ends an agent)
    call_budget: int = 0                                    # C: the tool calls each agent may make; 0 no budget
    calls_used: dict[str, int] = Field(default_factory=dict)    # calls made against C: every call, wait included,
    #                                                             and a reply with no tool call (_turn_rounds.charge)
    model_calls: dict[str, int] = Field(default_factory=dict)   # the agent's model calls so far
    checks: dict[str, int] = Field(default_factory=dict)        # board_status calls
    reads: dict[str, int] = Field(default_factory=dict)         # board_read calls
    posts: dict[str, int] = Field(default_factory=dict)         # board_post calls that posted
    discovery_round: dict[str, int] = Field(default_factory=dict)   # round of the first check or read
    read_senders: dict[str, dict[str, int]] = Field(default_factory=dict)  # reader -> sender -> posts returned
    oversight: dict[str, int] = Field(default_factory=dict)     # oversight calls about a teammate
    oversight_targets: dict[str, dict[str, int]] = Field(default_factory=dict)  # regulator -> target -> calls
    oversight_first: dict[str, dict[str, int]] = Field(default_factory=dict)    # regulator -> target -> first round
    turn_reads: dict[str, list[int]] = Field(default_factory=dict)  # [round, posts received through board_read]
    turn_posts: dict[str, list[int]] = Field(default_factory=dict)  # [round, posts made through board_post]
    reads_refused: dict[str, int] = Field(default_factory=dict)     # board_read calls refused by the read budget
    posts_refused: dict[str, int] = Field(default_factory=dict)     # board_post calls refused by the post budget
    history_marks: dict[str, list[list[int]]] = Field(default_factory=dict)  # [messages_after, round] per activation
    halted_after_round: int | None = None                   # the round after which the plan was cut
    notice_seen: dict[str, int] = Field(default_factory=dict)   # highest board sequence of others the agent was told of
    added: dict[str, list[dict]] = Field(default_factory=dict)  # every notice and prompt (_turn_rules_filter)
    ended: dict[str, str] = Field(default_factory=dict)     # stop_working submit alert rounds context delivered
    ended_round: dict[str, int] = Field(default_factory=dict)  # the round of a limit's ending (rounds, context)
    entry_rounds: dict[str, int] = Field(default_factory=dict)  # the first round of a plan step holding the agent
    exits: dict[str, dict] = Field(default_factory=dict)    # agent -> {why, round, seq, ...} (_turn_presence)
    board_entered: list[str] = Field(default_factory=list)  # the agents whose entry the board records (board_record)
    timeline: list[dict] = Field(default_factory=list)          # one entry per round (turn_activation)


class TeamSubmission(StoreModel):
    """The submits of the run (src/swarm/submit_and_stop.py). The first fields are the team's hand-in: the submit
    that went through and stands, by a neutral agent of a shared_task team or by a lone agent; the run ends after
    ends_after_round. Under SwarmConfig.unsubmit a worker's unsubmit empties them and a later submit fills them again
    (src/swarm/unsubmit.py), so they hold the last submit that was not taken back. own holds each solo_task agent's
    hand-in of its own work. refused holds every submit that handed in nothing, in any mode: refused by the guard
    (Benchmark.gradable) or made after the hand-in."""

    agent: str | None = None
    round: int | None = None
    answer: str = ""
    time: str | None = None                                 # UTC, ISO 8601
    ends_after_round: int | None = None                     # the last round that runs
    snapshot: str | None = None                             # an id of what was graded at the call (Benchmark.snapshot)
    refused: list[dict] = Field(default_factory=list)       # {"agent", "round", "reason"}
    own: dict[str, dict] = Field(default_factory=dict)      # solo_task: agent -> {round, answer, snapshot, time}


def hand_in_snapshot(store) -> str | None:
    """The snapshot of the team's hand-in as it stands at the run's end, read without writing the store: the last
    submit that went through and was not taken back by unsubmit (src/swarm/unsubmit.py empties the hand-in, and a
    later submit fills it again). None when no hand-in stands or its snapshot failed."""
    name = TeamSubmission.__name__
    return store.get(f"{name}:snapshot") if store.get(f"{name}:agent") is not None else None


class StopWorking(StoreModel):
    """The stop_working calls that went through (src/swarm/submit_and_stop.py): each agent's round of its call."""

    rounds: dict[str, int] = Field(default_factory=dict)


class Alerts(StoreModel):
    """The alert_humans calls that went through (src/swarm/submit_and_stop.py), in order: each {agent, round, reason,
    agents, time}. agents holds the names the caller gave, as given."""

    alerts: list[dict] = Field(default_factory=list)


class Unsubmits(StoreModel):
    """Every team hand-in and every unsubmit under SwarmConfig.unsubmit (src/swarm/unsubmit.py), written only when the
    option applies (gives_unsubmit).

    submits holds each submit of the team's work that went through, in order: {agent, round, answer, time,
    ends_after_round, snapshot, commit: the snapshot where it is a commit (Benchmark.snapshot_is_commit), else None,
    seq}. unsubmits holds each unsubmit that went through, in order: {agent, round, reason,
    time, submit: the hand-in it took back, as TeamSubmission held it, seq}. seq numbers both in one order. An
    unsubmit takes the hand-in out of TeamSubmission, lifts the plan's cut and asks after_activation to put the plan's
    steps back (restore)."""

    on: bool = False
    submits: list[dict] = Field(default_factory=list)
    unsubmits: list[dict] = Field(default_factory=list)
    restore: bool = False


def hand_in_snapshots(store) -> list[str]:
    """The snapshot of every team hand-in that went through, each once, in the order of its first hand-in: each submit
    Unsubmits records under unsubmit, then the hand-in standing at the end (hand_in_snapshot). Read without writing
    the store; a hand-in whose snapshot failed is left out."""
    submits = store.get(f"{Unsubmits.__name__}:submits") or []
    return list(dict.fromkeys(s for s in [*(r.get("snapshot") for r in submits), hand_in_snapshot(store)] if s))


def unsubmit_record() -> Unsubmits | None:
    """The Unsubmits record under unsubmit, else None: store_as writes a record's defaults, so a run without the option
    reads the raw store and never gets one."""
    return store_as(Unsubmits) if store().get(f"{Unsubmits.__name__}:on") else None


class Delivered(StoreModel):
    """The stop-after-delivery record of a run made before 2026-11-23, when SwarmConfig.stop_after_delivery was on.
    Nothing writes it now; the export reads it from those runs' stores (src/export/_export_run_blocks.py).

    seen gives each agent's first sighting of its deliverable: the round whose end showed it and the rounds the agent
    had used up by then ("turn"). A team's deliverable counts for every agent of the roster, and halt_round is the
    last round that runs. A predicate that raises counts as nothing delivered; errors keeps what it raised."""

    after: int = 0                                          # N
    team: bool = False                                      # the team's deliverable ends every agent, N rounds on
    seen: dict[str, dict] = Field(default_factory=dict)     # agent -> {"round", "turn"}
    halt_round: int | None = None                           # a team's last round
    cut: list[str] = Field(default_factory=list)            # agents of a team that held a step after its last round
    checks: int = 0                                         # predicate calls
    errors: list[str] = Field(default_factory=list)         # what failed predicate calls raised


# The sample's round-end callback (Benchmark.round_end), set by rules_setup: awaited after each agent's round with the
# agent, the round and its calls ([{id, tool, arguments}]); None for a domain with none.
ROUND_END: ContextVar[Callable[[str, int, list[dict]], Awaitable[None]] | None] = ContextVar("swarm_round_end",
                                                                                             default=None)
ROUND_END_TAG = "swarm_round_end"       # info event of a round-end callback that failed


@dataclass
class Live:
    """What a sample keeps in memory only, set by rules_setup and shared by every member's task: each agent's round
    under way (_turn_rounds.round_record), its conversation at its last boundary (turn_activation.ends_round),
    which the peer tools read while the agent's round runs, and the records of each round's message ORBIT has not yet
    committed (pending). ORBIT's session registry holds an agent's state only as of its last completed activation.
    regulators names the agents of role regulator, for the presence lines (_turn_presence.due); the store keeps no
    copy, so a stored run's export reads as before."""

    rounds: dict[str, dict] = field(default_factory=dict)
    messages: dict[str, list] = field(default_factory=dict)
    plan: list[dict] = field(default_factory=list)          # the written plan's steps, under unsubmit
    regulators: list[str] = field(default_factory=list)     # the agents of role regulator
    # a round's message id -> what to record once the conversation holds it (_turn_rules_filter.settle)
    pending: dict[str, list[Callable[[], None]]] = field(default_factory=dict)


LIVE: ContextVar[Live | None] = ContextVar("swarm_live", default=None)


def live_record() -> Live:
    """The sample's Live, or an empty one outside a run set up by rules_setup (a unit test's direct call)."""
    return LIVE.get() or Live()


# ------------------------------------------------------------------------------------------------ the live plan

def running_record(runtime):
    """The StepRecord of the step under way, or None.

    ScheduleRuntime has no accessor for it that stays cheap: `history[-1]` holds it but deep-copies every executed step
    on each read (70 ms at 6,400 activations, a thousand agents at the gigantic scale), so this reads its private
    `_current`. A plain read fails loudly if ORBIT renames it, and a test in test_turn_rules.py pins it against the
    public history.
    """
    return runtime._current


def running_step(runtime) -> ScheduleStep | None:
    """The step under way."""
    record = running_record(runtime)
    return record.step if record is not None else None


def current_round() -> int | None:
    """The round of the step under way, read from ORBIT's live plan; None outside a planned activation."""
    try:
        step = running_step(current_schedule())
    except RuntimeError:
        return None
    return step_round(step.id) if step is not None else None


def last_round() -> int | None:
    """The last round of ORBIT's live plan: the latest round of the step under way and the pending steps; None outside
    a planned activation."""
    try:
        runtime = current_schedule()
    except RuntimeError:
        return None
    steps = [*runtime.pending_steps, running_step(runtime)]
    return max((step_round(s.id) for s in steps if s is not None), default=None)


def live_state(agent: str) -> AgentState | None:
    """The agent's live AgentState from ORBIT's session registry, which the orchestrator installs for the whole
    sample (orbit/solvers/orchestrator.py solve, session_registry_scope); None outside a run."""
    try:
        return current_session_registry().scheduled(agent).state
    except (RuntimeError, KeyError):
        return None


def orbit_submit_ending(n_agents: int, mode: str | None) -> str:
    """What ORBIT's own-episode submit was in a log made before submit and stop_working (TurnState.ended
    ORBIT_SUBMITTED): the agent's hand-in (SUBMIT) for a lone agent and in solo_task, where each agent was graded
    alone; in a team it ended the caller's turns and handed in nothing (STOP_WORKING)."""
    return SUBMIT if n_agents < 2 or mode == "solo_task" else STOP_WORKING


# ------------------------------------------------------------------------------------------------ engagement

def bump(counts: dict[str, int], agent: str, by: int = 1) -> dict[str, int]:
    """A copy of counts with the agent's count raised by `by`. The store sees a new dict, so it records the change."""
    return {**counts, agent: counts.get(agent, 0) + by}


def calls_left(st: TurnState, agent: str) -> int | None:
    """The tool calls the agent has left of its budget C (TurnState.call_budget), or None with no budget."""
    if not st.call_budget:
        return None
    return max(st.call_budget - st.calls_used.get(agent, 0), 0)


def out_of_calls(st: TurnState, agent: str) -> bool:
    """Whether the agent has made all C of its tool calls."""
    return calls_left(st, agent) == 0


def record_board_action(agent: str, kind: str, senders: list[str] | None = None,
                        round_number: int | None = None, target: str | None = None) -> None:
    """Count one board action in TurnState: kind is "check", "read", "post" or "oversight". A read also counts the
    posts returned per sender, the agent's own and the system's notices (SYSTEM_SENDER) left out, so read_senders
    names agents only; an oversight call counts its target in oversight_targets, and oversight_first keeps the round
    of the first call about that target.
    The first check or read sets the agent's discovery round (round_number, or the round of the step under way).
    Called by the board tools and the oversight tools."""
    st = store_as(TurnState)
    field = KIND_FIELDS[kind]
    setattr(st, field, bump(getattr(st, field), agent))
    if kind == "oversight" and target is not None:
        st.oversight_targets = {**st.oversight_targets, agent: bump(st.oversight_targets.get(agent, {}), target)}
        first = st.oversight_first.get(agent, {})
        if target not in first:
            at = round_number if round_number is not None else current_round()
            st.oversight_first = {**st.oversight_first, agent: {**first, target: at or 0}}
    if kind in ("check", "read") and agent not in st.discovery_round:
        found = round_number if round_number is not None else current_round()
        st.discovery_round = {**st.discovery_round, agent: found or 0}
    others = [sender for sender in senders or [] if sender not in (agent, SYSTEM_SENDER)]
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


def is_record(message) -> bool:
    """Whether a board post (a CommunicationMessage or its exported dict) is a record of an entry or an exit."""
    value = message.get("invocation_id") if isinstance(message, dict) else getattr(message, "invocation_id", None)
    return value == RECORD


def board_record(kind: str, agent: str, recipients: list[str], *, seeded: bool = False) -> None:
    """Put "<agent> entered." or "<agent> exited." on the board as the system (prompts.yaml board_records), for the
    roster, when the run has a board: straight into ORBIT's communication store with the next sequence number, as the
    system's notice of a hand-in goes (board.system_post), marked RECORD. No count and no notice holds a record
    (board_status, the notice), and board_read shows it between the posts it returns. seeded: a seed pack's record,
    written at setup before ORBIT opens the board's channel (a seed pack needs a board: SwarmConfig)."""
    state = initialize_communication()
    if not seeded and BOARD_ID not in state.channels:
        return
    text = load_prompts()["board_records"][kind].format(agent=agent)
    state.messages.append(CommunicationMessage(id=f"message-{uuid4().hex}", channel_id=BOARD_ID, sender=SYSTEM_SENDER,
                                               recipients=tuple(recipients), content=text,
                                               sequence=state.sequence + 1, invocation_id=RECORD))
    state.sequence += 1


def record_entry(agent: str) -> None:
    """Put the agent's entry on the board, once (TurnState.board_entered): at the start of its first round
    (_turn_rounds.open_round). A lone agent has no record."""
    st = store_as(TurnState)
    if agent in st.board_entered or len(st.agents) < 2:
        return
    st.board_entered = [*st.board_entered, agent]
    board_record("entered", agent, st.agents)

