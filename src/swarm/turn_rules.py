"""The turn rules on ORBIT's public API, with no patch to ORBIT: rounds, free board steps, the halt and the filter.

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
from __future__ import annotations

import random
import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from inspect_ai.log import transcript
from inspect_ai.model import ChatMessageAssistant, ChatMessageTool, ChatMessageUser
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import StoreModel, store_as
from orbit.communication.runtime import read_messages
from orbit.configs.defense import DefenseConfig
from orbit.configs.scheduling import ScheduleConfig, ScheduleStep
from orbit.defenses.base import DefenseBase, DefenseVerdict
from orbit.defenses.model_filter import ModelFilter
from orbit.defenses.registry import register_defense
from orbit.execution.plans import current_schedule
from orbit.execution.sessions import current_session_registry
from pydantic import Field

from src.configuration.settings import BOARD_ID, load_prompts
from src.configuration.swarm_config import BOARD_TOOLS, SwarmConfig, Window, notice_text

TURN_RULES_TYPE = "swarm.turn_rules.v1"
TURN_RULES_NAME = "turn_rules"
TURN_RULES_TAG = "swarm_turn_rules"
TEAM_SUBMIT_TAG = "swarm_team_submit"                 # info event of a team_submit call (src/swarm/pull_board.py)
HALT_TAG = "swarm_team_halt"                           # info event when the plan is cut after a team submission
DELIVERED_TAG = "swarm_delivered"                      # info event of stop_after_delivery: a sighting or an end
DELIVERED = "delivered"                                # the TurnState.ended reason of stop_after_delivery
ACTOR = "swarm.rules"
TEAM_SUBMIT = "team_submit"
OVERSIGHT_TOOL = "agent_actions"                         # a regulator's tool (src/swarm/regulator_oversight.py)
CHANNEL_TOOLS = frozenset({"channel_post", "channel_read", "channel_list"})   # ORBIT's, withheld from the provider
# The TurnState count each board action goes to.
ACTION_KINDS = {"board_status": "check", "board_read": "read", "board_post": "post", OVERSIGHT_TOOL: "oversight"}
KIND_FIELDS = {"check": "checks", "read": "reads", "post": "posts", "oversight": "oversight"}
SUBMIT_TOOLS = ("submit", TEAM_SUBMIT)                   # a step ending in one is neither charged nor free
# A target that names no agent builds no DefenseBoundary, so the filter runs without a check event per message.
FILTER_ONLY_TARGET = "__model_filter_only__"
ROUND_ID = re.compile(r"^r(\d+)\b")
FREE_ID = re.compile(r"-free(\d+)$")


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


# ------------------------------------------------------------------------------------------------ step ids

def step_round(step_id: str) -> int:
    """The round of a plan step from its id: r3, r3-a01, r3-free1, r3-a01-free2, r9-a01-v4 give 3, 3, 3, 3, 9."""
    match = ROUND_ID.match(step_id)
    return int(match.group(1)) if match else 0


def free_index(step_id: str) -> int:
    """k of a follow-up step `...-free<k>`, or 0 for any other step."""
    match = FREE_ID.search(step_id)
    return int(match.group(1)) if match else 0


def is_round_step(step_id: str) -> bool:
    """A round's own step, not a follow-up step. The plan holds steps_per_agent of them per agent; whether one is
    charged depends on what it did (step_charge)."""
    return not free_index(step_id)


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


# ------------------------------------------------------------------------------------------------ the plan

def round_order(agents: list[str], turn_order: str, seed: int, round_number: int) -> list[str]:
    """The agents of one round: declaration order, or a permutation seeded by (seed, round)."""
    order = list(agents)
    if turn_order == "random":
        random.Random(f"{seed}/{round_number}").shuffle(order)
    return order


def windows(config: SwarmConfig, agents: list[str], roles: dict[str, str] | None = None) -> dict[str, Window]:
    """Each agent's window of rounds, from its role. Without roles every agent holds every round."""
    return {agent: config.window((roles or {}).get(agent, "worker")) for agent in agents}


def concurrent(config: SwarmConfig, agents: list[str]) -> bool:
    """Whether rounds are concurrent steps. A lone agent keeps single-agent steps."""
    return config.concurrent_rounds and len(agents) > 1


def board_actions() -> list[str]:
    """The tool names whose call, as a step's last, makes the step free: the board tools and a regulator's
    agent_actions (kind "oversight")."""
    return [*BOARD_TOOLS, OVERSIGHT_TOOL]


def free_caps(config: SwarmConfig, agents: list[str], roles: dict[str, str] | None = None) -> dict[str, int]:
    """Each agent's K: free_board_actions_regulator for a regulator, free_board_actions otherwise; 0 for a lone
    agent."""
    if len(agents) < 2:
        return dict.fromkeys(agents, 0)
    return {a: config.free_board_actions_regulator if (roles or {}).get(a) == "regulator" else config.free_board_actions
            for a in agents}


def concurrent_step(step_id: str, agents: list[str], max_concurrency: int) -> dict[str, Any]:
    """A concurrent step on a snapshot of the board as of its start."""
    return {"id": step_id, "agents": list(agents), "concurrent": True, "max_concurrency": max_concurrency,
            "visibility": "snapshot"}


def schedule(config: SwarmConfig, agents: list[str], roles: dict[str, str] | None = None,
             domain: str | None = None) -> ScheduleConfig:
    """config.steps_for(domain) rounds over the agents whose window holds the round: the domain's turn budget per
    agent, or steps_per_agent without a domain or a budget.

    A round is one concurrent step `r<r>` (concurrent_rounds) or one single-agent step `r<r>-<agent>` per agent.
    Follow-up steps are not written here: after_activation inserts them."""
    steps: list[dict[str, Any]] = []
    held = windows(config, agents, roles)
    together = concurrent(config, agents)
    for round_number in range(1, config.steps_for(domain) + 1):
        eligible = [agent for agent in round_order(agents, config.turn_order, config.seed, round_number)
                    if held[agent].contains(round_number)]
        if together:
            steps += [concurrent_step(f"r{round_number}", eligible, config.max_concurrency)] if eligible else []
        else:
            steps += [{"id": f"r{round_number}-{agent}", "agents": [agent]} for agent in eligible]
    return ScheduleConfig(steps=steps, after_activation=f"{__name__}.after_activation")


def initial_state(config: SwarmConfig, agents: list[str], roles: dict[str, str] | None = None,
                  domain: str | None = None) -> dict:
    """The TurnState fields a sample starts with. cap and rounds are config.steps_for(domain)."""
    steps = config.steps_for(domain)
    return {"agents": list(agents), "cap": steps,
            "pooled": config.pooled_steps, "turn_order": config.turn_order, "seed": config.seed,
            "concurrent": concurrent(config, agents), "max_concurrency": config.max_concurrency,
            "free_cap": config.free_board_actions if len(agents) > 1 else 0,
            "free_caps": free_caps(config, agents, roles),
            "board_actions": board_actions(),
            "reads_per_turn": config.reads_per_turn, "posts_per_turn": config.posts_per_turn,
            "turn_budget": config.turn_budget_for(domain),
            "rounds": steps,
            "windows": {a: [w.start, w.end] for a, w in windows(config, agents, roles).items()}}


def team_delivery(config: SwarmConfig, agents: list[str], domain: str | None = None) -> bool:
    """Whether stop_after_delivery reads one deliverable for the team: two or more agents outside solo_task (on any
    board when no domain is named). Otherwise each agent has its own."""
    return len(agents) > 1 and (config.resolve_mode(domain) != "solo_task" if domain else True)


@solver
def rules_setup(config: SwarmConfig | dict, agents: list[str], roles: dict[str, str] | None = None,
                domain: str | None = None, delivered: Callable[[], Awaitable[set[str]]] | None = None) -> Solver:
    """Task setup: write the initial TurnState into the sample's store. Under stop_after_delivery it also writes the
    Delivered record and sets the sample's delivery predicate, `delivered` (Benchmark.delivered), which the rule
    needs."""
    rules = SwarmConfig.model_validate(config)
    if rules.stop_after_delivery is not None and delivered is None:
        raise ValueError("stop_after_delivery needs the benchmark's delivery predicate (Benchmark.delivered)")

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        st = store_as(TurnState)
        for key, value in initial_state(rules, agents, roles, domain).items():
            setattr(st, key, value)
        PREDICATE.set(None if rules.stop_after_delivery is None else delivered)
        if rules.stop_after_delivery is not None:
            record = store_as(Delivered)
            record.after, record.team = rules.stop_after_delivery, team_delivery(rules, agents, domain)
        return state

    return solve


def _window(st: TurnState, agent: str) -> Window:
    start, end = st.windows.get(agent) or [1, None]
    return Window(start=start, end=end)


# ------------------------------------------------------------------------------------------------ engagement

def _bump(counts: dict[str, int], agent: str, by: int = 1) -> dict[str, int]:
    return {**counts, agent: counts.get(agent, 0) + by}


def record_board_action(agent: str, kind: str, senders: list[str] | None = None,
                        round_number: int | None = None, target: str | None = None) -> None:
    """Count one board action in TurnState: kind is "check", "read", "post" or "oversight". A read also counts the
    posts returned per sender, the agent's own left out; an oversight call counts its target in oversight_targets.
    The first check or read sets the agent's discovery round (round_number, or the round of the step under way).
    Called by the board tools and agent_actions."""
    st = store_as(TurnState)
    field = KIND_FIELDS[kind]
    setattr(st, field, _bump(getattr(st, field), agent))
    if kind == "oversight" and target is not None:
        st.oversight_targets = {**st.oversight_targets, agent: _bump(st.oversight_targets.get(agent, {}), target)}
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
    setattr(st, field, _bump(getattr(st, field), agent))


# ------------------------------------------------------------------------------------------------ after each activation

def _out_of_context(agent: str) -> bool:
    """Whether the agent's last generation stopped at the context limit.

    The live AgentState is reachable through ORBIT's session registry, which the orchestrator installs for the whole
    sample (orbit/solvers/orchestrator.py solve, session_registry_scope).
    """
    try:
        output = current_session_registry().scheduled(agent).state.output
    except (RuntimeError, KeyError):
        return False
    return output is not None and output.stop_reason == "model_length"


def _activation_messages(agent: str, result) -> list:
    """The messages the activation added to the agent's conversation, from ORBIT's session registry."""
    try:
        messages = current_session_registry().scheduled(agent).state.messages
    except (RuntimeError, KeyError):
        return []
    return list(messages[result.messages_before:result.messages_after])


def free_cap_of(st: TurnState, agent: str | None) -> int:
    """The agent's K: its free_caps entry, else the team's free_cap."""
    return st.free_caps.get(agent, st.free_cap) if agent is not None else st.free_cap


def last_call_refused(messages: list) -> bool:
    """Whether the last tool call in the activation's messages failed: its result is an error (a tool the agent
    does not hold, a budget refusal, a bad argument) or agent_actions' "no such agent". False when no call or no
    result is found."""
    last = next((m for m in reversed(messages) if isinstance(m, ChatMessageAssistant) and m.tool_calls), None)
    if last is None:
        return False
    call = last.tool_calls[-1]
    result = next((m for m in messages if isinstance(m, ChatMessageTool) and m.tool_call_id == call.id), None)
    if result is None:
        return False
    if result.error is not None:
        return True
    return call.function == OVERSIGHT_TOOL and result.text == load_prompts()["oversight_output"]["no_such_agent"]


def step_charge(st: TurnState, tools: list[str], submitted: bool, agent: str | None = None,
                refused: bool = False) -> str:
    """What a completed step counts as: "free" (it ended with a board action that succeeded), "refused" (it ended
    with a board action that failed; charged like work), "submit" (it ended with submit or team_submit) or "work"
    (charged against steps_per_agent).

    With K 0 (the agent's free_caps entry, else free_cap) there are no free board actions and every step is
    charged."""
    if not free_cap_of(st, agent):
        return "work"
    last = tools[-1] if tools else None
    if last in st.board_actions:
        return "refused" if refused else "free"
    if submitted or last in SUBMIT_TOOLS:
        return "submit"
    return "work"


CHARGED = ("work", "refused")       # the step_charge values charged against steps_per_agent


def _stop_reason(st: TurnState, agent: str, submitted: bool, used: int) -> str | None:
    if submitted:
        return "submitted"
    if _out_of_context(agent):
        return "context"
    if not st.pooled and used >= st.cap:
        return "cap"
    return None


def _pooled_steps(st: TurnState, active: list[str], count: int, revision: int) -> list[dict]:
    """count new working agent-steps over the active agents, in new rounds after the last one.

    A step goes only to an agent whose window holds its round; when no agent is eligible the rest is dropped. In
    sequence each is a single-agent step `r<n>-<agent>-v<revision>`. Under concurrent rounds a new round is one
    concurrent step `r<n>-v<revision>` with up to every eligible agent.
    """
    steps: list[dict] = []
    left = count
    while left > 0 and active:
        st.rounds += 1
        eligible = [agent for agent in active if _window(st, agent).contains(st.rounds)]
        if not eligible:
            break
        order = round_order(eligible, st.turn_order, st.seed, st.rounds)[:left]
        left -= len(order)
        if st.concurrent:
            steps.append(concurrent_step(f"r{st.rounds}-v{revision}", order, st.max_concurrency))
        else:
            steps += [{"id": f"r{st.rounds}-{agent}-v{revision}", "agents": [agent]} for agent in order]
    return steps


def _follow_up(st: TurnState, keep: list[dict], step: ScheduleStep, agent: str) -> bool:
    """Put the agent's follow-up step at the head of the plan, unless it has used the round's K. Returns whether.

    Under concurrent rounds a regulator with a larger K holds the later follow-ups `r<r>-free<k>` alone."""
    k = free_index(step.id) + 1
    if k > free_cap_of(st, agent):
        return False
    if st.concurrent:
        step_id = f"r{step_round(step.id)}-free{k}"
        if keep and keep[0]["id"] == step_id:
            keep[0] = {**keep[0], "agents": [*keep[0]["agents"], agent]}
        else:
            keep.insert(0, concurrent_step(step_id, [agent], st.max_concurrency))
    else:
        base = FREE_ID.sub("", step.id)
        keep.insert(0, {"id": f"{base}-free{k}", "agents": [agent]})
    return True


def _halt(st: TurnState, keep: list[dict]) -> list[dict]:
    """After a team submission: keep the steps of the submission's round, drop every later one."""
    submitted_round = st.team_submission.get("round") if st.team_submission else None
    if submitted_round is None:
        return []
    if st.halted_after_round is None:
        st.halted_after_round = submitted_round
        transcript().info({HALT_TAG: {"round": submitted_round, "agent": st.team_submission.get("agent"),
                                      "dropped": [s["id"] for s in keep if step_round(s["id"]) > submitted_round]}})
    return [s for s in keep if step_round(s["id"]) <= submitted_round]


# ------------------------------------------------------------------------------------------------ stop after delivery

def _delivery() -> Delivered | None:
    """The Delivered record when stop_after_delivery is on in this sample, else None, with nothing written."""
    return store_as(Delivered) if PREDICATE.get() is not None else None


def _spent(record: Delivered, agent: str, used: int) -> bool:
    """Whether an agent with its own deliverable has taken its N working steps since the deliverable was seen."""
    seen = record.seen.get(agent)
    return not record.team and seen is not None and used >= seen["turn"] + record.after


def _holds(agent: str, steps: list[dict]) -> bool:
    return any(agent in s["agents"] for s in steps)


def _round_over(runtime, step: ScheduleStep | None, agent: str, keep: list[dict]) -> bool:
    """Whether this activation closes its round: the last activation of its step to complete (ORBIT calls back in
    activation order once a concurrent step has finished; StepRecord.activations, through `_current` as in
    running_step), with no step of the round left in the plan."""
    if step is None:
        return False
    current = getattr(runtime, "_current", None)
    done = [a.agent for a in current.activations if a.status == "completed"] if current is not None else []
    if done and done[-1] != agent:
        return False
    return not any(step_round(s["id"]) == step_round(step.id) for s in keep)


async def _look(st: TurnState, record: Delivered, round_number: int) -> None:
    """Await the delivery predicate once, unless the team's deliverable or every active agent's was seen, and record
    each first sighting: the round and the agent's working steps by then. A team's sighting counts for every agent
    of the roster and sets its last round, N rounds on."""
    active = [a for a in st.agents if a not in st.ended]
    unseen = [a for a in active if a not in record.seen]
    if not unseen or (record.team and record.halt_round is not None):
        return
    record.checks += 1
    try:
        found = set(await PREDICATE.get()())
    except Exception as error:  # noqa: BLE001 - a failed check shows nothing; the run goes on
        record.errors = [*record.errors, f"round {round_number}: {type(error).__name__}: {error}"[:500]]
        return
    if record.team:
        shown = [a for a in st.agents if a not in record.seen] if found else []
    else:
        shown = [a for a in unseen if a in found]
    if not shown:
        return
    record.seen = {**record.seen, **{a: {"round": round_number, "turn": st.used.get(a, 0)} for a in shown}}
    if record.team:
        record.halt_round = round_number + record.after
    transcript().info({DELIVERED_TAG: {"kind": "seen", "round": round_number, "agents": shown,
                                       "halt_round": record.halt_round}})


def _end(st: TurnState, agents: list[str], round_number: int) -> None:
    """End these agents "delivered"."""
    st.ended = {**st.ended, **dict.fromkeys(agents, DELIVERED)}
    transcript().info({DELIVERED_TAG: {"kind": "ended", "round": round_number, "agents": agents}})


async def _after_delivery(st: TurnState, record: Delivered, runtime, step: ScheduleStep | None, agent: str,
                          keep: list[dict]) -> tuple[list[dict], list[str]]:
    """The rule at the end of an activation, on the amended plan `keep`. When the activation closes a round it looks
    for deliverables. A team's steps after its last round are cut at every activation, and the agents they belonged
    to are kept in `cut`. At the end of the team's last round (or of the plan) the active agents with a step cut are
    ended; an agent with its own deliverable is ended once its N working steps are spent and it still holds a step.
    Returns the plan without the ended agents' steps, which go to nobody, and the agents it ended."""
    over = _round_over(runtime, step, agent, keep)
    round_number = step_round(step.id) if step is not None else 0
    if over:
        await _look(st, record, round_number)
    if record.team and record.halt_round is not None:
        late = [s for s in keep if step_round(s["id"]) > record.halt_round]
        if late:
            record.cut = sorted({*record.cut, *(a for s in late for a in s["agents"])})
            keep = [s for s in keep if step_round(s["id"]) <= record.halt_round]
    active = [a for a in st.agents if a not in st.ended]
    ended: list[str] = []
    if over and record.team:
        if record.halt_round is not None and (round_number >= record.halt_round or not keep):
            ended = [a for a in active if a in record.cut]
    elif over:
        ended = [a for a in active if _spent(record, a, st.used.get(a, 0)) and _holds(a, keep)]
    if not ended:
        return keep, ended
    _end(st, ended, round_number)
    keep = [{**s, "agents": [a for a in s["agents"] if a not in ended]} for s in keep]
    return [s for s in keep if s["agents"]], ended


def after_activation(runtime, result, global_turn: int) -> Awaitable[None] | None:
    """ORBIT calls this after every activation of the plan (ScheduleConfig.after_activation).

    Charges the step by what it did (step_charge), records why the agent stopped, and amends the pending plan: a
    stopped agent's steps are removed and, with pooling, its round steps go to the active agents in new rounds at the
    end; a free step gets its follow-up step while the round's K lasts; a team submission cuts every step after its
    round. The timeline marks an agent's first activation ("entered") and the activation after which its window
    holds no more steps ("left").

    Under stop_after_delivery it returns a coroutine, which ORBIT awaits: the rule may await the delivery predicate
    before the plan is amended (_after_delivery). Without the rule it returns None, as before.
    """
    st = store_as(TurnState)
    agent = result.agent_name
    step = running_step(runtime)
    activation = st.activations.get(agent, 0) + 1
    tools = [call["tool"] for call in result.tool_calls]
    round_number = step_round(step.id) if step is not None else 0
    messages = _activation_messages(agent, result)
    st.history_marks = {**st.history_marks,
                        agent: [*st.history_marks.get(agent, []), [result.messages_after, round_number]]}
    charge = step_charge(st, tools, result.submitted, agent, last_call_refused(messages))
    working, free = charge in CHARGED, charge == "free"
    used = st.used.get(agent, 0) + (1 if working else 0)
    st.activations = {**st.activations, agent: activation}
    st.used = {**st.used, agent: used}
    st.work_steps = {**st.work_steps, agent: used}
    if free:
        st.free_steps = _bump(st.free_steps, agent)
    pending = list(runtime.pending_steps)
    delivery = _delivery()
    reason = _stop_reason(st, agent, result.submitted, used)
    if reason is None and delivery is not None and agent not in st.ended and _spent(delivery, agent, used) \
            and any(agent in s.agents for s in pending):
        _end(st, [agent], round_number)
    elif reason and agent not in st.ended:
        st.ended = {**st.ended, agent: reason}
    left = _window(st, agent).end is not None and not any(agent in s.agents for s in pending)
    st.timeline = [*st.timeline, {"turn": global_turn, "agent": agent, "activation": activation, "working": working,
                                  "free": free, "charge": charge, "step": step.id if step is not None else None,
                                  "used": used, "tools": tools, "ended": st.ended.get(agent),
                                  "entered": activation == 1, "left": left}]

    active = [name for name in st.agents if name not in st.ended]
    keep, dropped = [], 0
    for s in pending:
        agents = [name for name in s.agents if name in active]
        if is_round_step(s.id):     # an agent ended "delivered" leaves its steps to nobody
            dropped += sum(1 for name in s.agents if name not in active and st.ended.get(name) != DELIVERED)
        if len(agents) == len(s.agents):
            keep.append(s.model_dump(mode="json"))
        elif agents:
            keep.append(s.model_copy(update={"agents": tuple(agents)}).model_dump(mode="json"))
    before = [s.model_dump(mode="json") for s in pending]
    halted = st.team_submission is not None
    if step is not None and free and agent in active:
        _follow_up(st, keep, step, agent)
    if halted:
        keep = _halt(st, keep)
    elif st.pooled and dropped:
        keep += _pooled_steps(st, active, dropped, runtime.revision)
    if delivery is not None and not halted:     # team_submit ends the run itself; the rule stays out of it
        return _amend_after_delivery(st, delivery, runtime, step, agent, keep, before)
    _amend(st, runtime, agent, keep, before, halted)


def _amend(st: TurnState, runtime, agent: str, keep: list[dict], before: list[dict], halted: bool,
           delivered: bool = False) -> None:
    if keep == before:
        return
    why = "team_submit" if halted else DELIVERED if delivered else \
        st.ended.get(agent) or ("free step" if len(keep) > len(before) else "stopped")
    runtime.amend_pending(keep, actor=ACTOR, reason=f"{agent}: {why}", expected_revision=runtime.revision)


async def _amend_after_delivery(st: TurnState, record: Delivered, runtime, step: ScheduleStep | None, agent: str,
                                keep: list[dict], before: list[dict]) -> None:
    keep, ended = await _after_delivery(st, record, runtime, step, agent, keep)
    _amend(st, runtime, agent, keep, before, False, bool(ended))


# ------------------------------------------------------------------------------------------------ the model filter

class TurnRulesFilter(ModelFilter):
    """Shapes each provider request to the turn rules; see the module docstring."""

    name = TURN_RULES_NAME
    priority = 90       # after prompt filters (10)

    def __init__(self, config: SwarmConfig) -> None:
        self.config = config
        self.prompts = load_prompts()

    def _state(self, agent: str) -> tuple[TurnState, int, bool]:
        """(state, completed activations, team): team is whether the agent is on a board."""
        st = store_as(TurnState)
        n = st.activations.get(agent, 0)
        team = len(st.agents) > 1 if st.agents else self.config.board
        return st, n, team

    def _continue(self, messages: list) -> tuple[str, str] | None:
        """The continue prompt when the input would end with a model turn, which Gemini rejects."""
        last = next((m for m in reversed(messages) if m.role != "system"), None)
        if last is not None and last.role == "assistant":
            return "continue", self.prompts["continue"]
        return None

    def _notice(self, st: TurnState, agent: str, team: bool) -> tuple[str, str] | None:
        """The board notice when the board holds posts by others above the sequence the agent saw at its last call.

        counts gives each such sender's new posts (above that sequence) and total posts. The board is read through
        ORBIT's read_messages, so a concurrent round sees its snapshot."""
        if self.config.notice == "none" or not team:
            return None
        try:
            posts = [m for m in read_messages(BOARD_ID, agent_id=agent) if m.sender != agent]
        except (PermissionError, RuntimeError, ValueError):
            return None
        seen = st.notice_seen.get(agent, 0)
        top = max((m.sequence for m in posts), default=0)
        if top <= seen:
            return None
        st.notice_seen = {**st.notice_seen, agent: top}
        counts: dict[str, tuple[int, int]] = {}
        for sender in sorted({m.sender for m in posts if m.sequence > seen}):
            mine = [m for m in posts if m.sender == sender]
            counts[sender] = (sum(1 for m in mine if m.sequence > seen), len(mine))
        text = notice_text(self.config.notice, counts)
        return ("notice", text) if text else None

    async def on_input(self, messages, tools, tool_choice, config, ctx):
        agent = ctx.agent_name
        st, n, team = self._state(agent)
        offered = list(tools)
        tools = [t for t in offered if t.name not in CHANNEL_TOOLS]
        withheld = sorted({t.name for t in offered} - {t.name for t in tools})
        if withheld:
            transcript().info({TURN_RULES_TAG: {"kind": "withheld_tools", "activation": n + 1, "agent": agent,
                                                "tools": withheld}})
        messages = list(messages)
        for added in (self._notice(st, agent, team), self._continue(messages)):
            if added is None:
                continue
            kind, text = added
            tag = {"kind": kind, "activation": n + 1}
            messages = [*messages, ChatMessageUser(content=text, metadata={TURN_RULES_TAG: tag})]
            transcript().info({TURN_RULES_TAG: {**tag, "agent": agent, "content": text}})
        return messages, tools, tool_choice, config

    async def on_output(self, output, ctx):
        return output


class TurnRulesDefense(DefenseBase):
    """Passive defense that carries TurnRulesFilter. It allows every message and tool call."""

    def __init__(self, config: DefenseConfig) -> None:
        super().__init__(config)
        self.rules = SwarmConfig.model_validate(config.config)

    def get_model_filters(self) -> list[ModelFilter]:
        return [TurnRulesFilter(self.rules)]

    async def apply_pre_deployment(self, agents, state) -> None:
        return None

    async def monitor_message(self, message, agent_name, turn) -> DefenseVerdict:
        return DefenseVerdict(allow=True, reason="passive_turn_rules")

    async def on_tool_call(self, tool_name, tool_args, agent_name) -> DefenseVerdict:
        return DefenseVerdict(allow=True, reason="passive_turn_rules")


try:
    register_defense(TURN_RULES_TYPE, TurnRulesDefense)
except ValueError:
    pass            # already registered in this process


def turn_rules_defense(config: SwarmConfig) -> DefenseConfig:
    """The DefenseConfig that installs the filter on every agent.

    A target naming no agent builds no DefenseBoundary, while ORBIT collects the model filters of every defense for
    every agent (orbit/solvers/orchestrator.py _build_attacks_and_defenses).
    """
    return DefenseConfig(name=TURN_RULES_NAME, defense_type=TURN_RULES_TYPE, target_agents=[FILTER_ONLY_TARGET],
                         config=config.model_dump(mode="json"))
