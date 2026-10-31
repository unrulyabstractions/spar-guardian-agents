"""The trusted callback ORBIT runs after every activation of the plan (ScheduleConfig.after_activation).

It charges the step by what it did and marks why an agent stopped. It then amends the pending plan: a follow-up
step after a free step, the stopped agents' steps removed or pooled, every step after the team's hand-in dropped, and
every step after the round under way dropped once no worker is left (workers_gone), so no regulator holds a run open.
Under stop_after_delivery it hands the amended plan to the rule in _turn_delivery first.

    schedule = ScheduleConfig(steps=steps, after_activation=f"{turn_activation.__name__}.after_activation")
    step_charge(st, ["board_read"], agent="k7q2m")    # "free" when the agent's K is above 0
    step_charge(st, ["submit", "submit"], "k7q2m", ending="submit")   # the hand-in went through; the second was refused
"""
from __future__ import annotations

from collections.abc import Awaitable

from inspect_ai.log import transcript
from inspect_ai.model import ChatMessageAssistant, ChatMessageTool
from inspect_ai.util import store_as
from orbit.configs.scheduling import ScheduleStep

from src.configuration.settings import load_prompts
from src.configuration.swarm_config import Window
from src.swarm.turn_rules._turn_delivery import after_delivery, delivery, end, spent
from src.swarm.turn_rules._turn_state import (
    ACTOR,
    BOARD_ACTIONS,
    DELIVERED,
    ENDING_TOOLS,
    HALT_TAG,
    OVERSIGHT_TOOL,
    STOP_WORKING,
    SUBMIT,
    Delivered,
    TeamSubmission,
    TurnState,
    bump,
    live_state,
    running_step,
)
from src.swarm.turn_rules._turn_steps import (
    concurrent_step,
    follow_up_id,
    free_index,
    is_round_step,
    round_order,
    single_step,
    step_id,
    step_round,
)

CHARGED = ("work", "refused")       # the step_charge values charged against the working steps
HANDED_OFF = (DELIVERED, SUBMIT)    # TurnState.ended reasons whose steps go to nobody: the agent's own work is done


def _window(st: TurnState, agent: str) -> Window:
    start, end = st.windows.get(agent) or [1, None]
    return Window(start=start, end=end)


def _out_of_context(agent: str) -> bool:
    """Whether the agent's last generation stopped at the context limit."""
    state = live_state(agent)
    return state is not None and state.output is not None and state.output.stop_reason == "model_length"


def _activation_messages(agent: str, result) -> list:
    """The messages the activation added to the agent's conversation, from its live state."""
    state = live_state(agent)
    return list(state.messages[result.messages_before:result.messages_after]) if state is not None else []


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


def ending_of(st: TurnState, submission: TeamSubmission, agent: str, round_number: int) -> str | None:
    """"stop" or "submit" when the agent's stop_working or submit went through in the step just completed, read from
    the records: its turns ended by its own call (TurnState.ended), or the team's hand-in names it in this round. An
    agent takes no later step in the round of its hand-in, since that step is never free. None otherwise."""
    ended = st.ended.get(agent)
    if ended == STOP_WORKING:
        return "stop"
    if ended == SUBMIT or (submission.agent == agent and submission.round == round_number):
        return "submit"
    return None


def step_charge(st: TurnState, tools: list[str], agent: str, refused: bool = False, ending: str | None = None) -> str:
    """What a completed step counts as. "submit" or "stop" when the agent's submit or stop_working went through in it
    (ending, from ending_of), whatever else the step called: neither charged nor free. Otherwise by its last tool
    call: "free" (a board action that succeeded), "refused" (a board action, submit or stop_working that failed or
    was refused; charged like work) or "work" (charged against the working steps).

    With K 0 (the agent's free_caps entry) there are no free board actions, and every step that did not end the
    agent's work is charged."""
    if ending is not None:
        return ending
    if not st.free_caps.get(agent, 0):
        return "work"
    last = tools[-1] if tools else None
    if last in (*BOARD_ACTIONS, *ENDING_TOOLS) and refused:
        return "refused"
    return "free" if last in BOARD_ACTIONS else "work"


def _stop_reason(st: TurnState, agent: str, used: int) -> str | None:
    """Why after_activation stops an agent: its last generation hit the context limit, or it used its cap without
    pooling. submit and stop_working end their caller themselves (submit_and_stop.end_turns)."""
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
            steps.append(concurrent_step(step_id(st.rounds, revision=revision), order, st.max_concurrency))
        else:
            steps += [single_step(step_id(st.rounds, agent, revision), agent) for agent in order]
    return steps


def _follow_up(st: TurnState, keep: list[dict], step: ScheduleStep, agent: str) -> bool:
    """Put the agent's follow-up step at the head of the plan, unless it has used the round's K. Returns whether.

    Under concurrent rounds a regulator with a larger K holds the later follow-ups `r<r>-free<k>` alone."""
    k = free_index(step.id) + 1
    if k > st.free_caps.get(agent, 0):
        return False
    if st.concurrent:
        follow_up = follow_up_id(step_id(step_round(step.id)), k)
        if keep and keep[0]["id"] == follow_up:
            keep[0] = {**keep[0], "agents": [*keep[0]["agents"], agent]}
        else:
            keep.insert(0, concurrent_step(follow_up, [agent], st.max_concurrency))
    else:
        keep.insert(0, single_step(follow_up_id(step.id, k), agent))
    return True


def _halt(st: TurnState, submission: TeamSubmission, keep: list[dict]) -> list[dict]:
    """After the team's hand-in: keep the steps up to its last round (ends_after_round), drop every later one."""
    last = submission.ends_after_round
    if last is None:
        return []
    if st.halted_after_round is None:
        st.halted_after_round = last
        transcript().info({HALT_TAG: {"round": submission.round, "ends_after_round": last, "agent": submission.agent,
                                      "dropped": [s["id"] for s in keep if step_round(s["id"]) > last]}})
    return [s for s in keep if step_round(s["id"]) <= last]


def workers_gone(st: TurnState) -> bool:
    """Whether every worker's turns have ended, by a stop, its own hand-in or a limit, while another agent's have not:
    the run is over then. A regulator cannot stop and a disregulator cannot hand in, so neither keeps it going. False
    with no worker, where only the plan's end stops the run, and once every agent has ended, which empties the plan."""
    return bool(st.workers) and set(st.workers) <= st.ended.keys() and len(st.ended) < len(st.agents)


def _without_workers(st: TurnState, keep: list[dict], step: ScheduleStep | None) -> list[dict]:
    """The amended plan, last of all: once no worker is left (workers_gone) the steps up to the round under way, which
    finishes, and no later one."""
    if not workers_gone(st):
        return keep
    last = step_round(step.id) if step is not None else 0
    if st.halted_after_round != last:
        st.halted_after_round = last
        transcript().info({HALT_TAG: {"round": last, "ends_after_round": last, "agent": None,
                                      "dropped": [s["id"] for s in keep if step_round(s["id"]) > last]}})
    return [s for s in keep if step_round(s["id"]) <= last]


def after_activation(runtime, result, global_turn: int) -> Awaitable[None] | None:
    """ORBIT calls this after every activation of the plan (ScheduleConfig.after_activation).

    Charges the step by what it did (step_charge), records why the agent stopped, and amends the pending plan: a
    stopped agent's steps (TurnState.ended, which submit and stop_working also write) are removed and, with pooling,
    its round steps go to the active agents in new rounds at the end, unless its own work was done (HANDED_OFF:
    delivered, or its own hand-in in solo_task, where each agent is graded alone); a free step gets its follow-up
    step while the round's K lasts; the team's hand-in (TeamSubmission) cuts every step after its last round, and
    pooling stops; once no worker is left (workers_gone) every step after the round under way goes, whatever a hand-in
    left running.
    The timeline marks an agent's first activation ("entered") and the activation after which its window holds no
    more steps ("left").

    Under stop_after_delivery it returns a coroutine, which ORBIT awaits: the rule may await the delivery predicate
    before the plan is amended (after_delivery). Without the rule it returns None.
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
    submission = store_as(TeamSubmission)
    charge = step_charge(st, tools, agent, last_call_refused(messages), ending_of(st, submission, agent, round_number))
    working, free = charge in CHARGED, charge == "free"
    used = st.work_steps.get(agent, 0) + (1 if working else 0)
    st.activations = {**st.activations, agent: activation}
    st.work_steps = {**st.work_steps, agent: used}
    if free:
        st.free_steps = bump(st.free_steps, agent)
    pending = list(runtime.pending_steps)
    record = delivery()
    reason = _stop_reason(st, agent, used)
    if reason is None and record is not None and agent not in st.ended and spent(record, agent, used) \
            and any(agent in s.agents for s in pending):
        end(st, [agent], round_number)
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
        if is_round_step(s.id):     # an agent ended "delivered" or by its own hand-in leaves its steps to nobody
            dropped += sum(1 for name in s.agents if name not in active and st.ended.get(name) not in HANDED_OFF)
        if len(agents) == len(s.agents):
            keep.append(s.model_dump(mode="json"))
        elif agents:
            keep.append(s.model_copy(update={"agents": tuple(agents)}).model_dump(mode="json"))
    before = [s.model_dump(mode="json") for s in pending]
    halted = submission.agent is not None
    if step is not None and free and agent in active:
        _follow_up(st, keep, step, agent)
    if halted:
        keep = _halt(st, submission, keep)
    elif st.pooled and dropped and not workers_gone(st):
        keep += _pooled_steps(st, active, dropped, runtime.revision)
    if record is not None and not halted:     # the team's hand-in ends the run itself; the rule stays out of it
        return _amend_after_delivery(st, record, runtime, step, agent, keep, before)
    _amend(st, runtime, agent, _without_workers(st, keep, step), before, halted)


def _amend(st: TurnState, runtime, agent: str, keep: list[dict], before: list[dict], halted: bool,
           delivered: bool = False) -> None:
    if keep == before:
        return
    why = SUBMIT if halted else DELIVERED if delivered else \
        st.ended.get(agent) or ("free step" if len(keep) > len(before) else "stopped")
    runtime.amend_pending(keep, actor=ACTOR, reason=f"{agent}: {why}", expected_revision=runtime.revision)


async def _amend_after_delivery(st: TurnState, record: Delivered, runtime, step: ScheduleStep | None, agent: str,
                                keep: list[dict], before: list[dict]) -> None:
    keep, ended = await after_delivery(st, record, runtime, step, agent, keep)
    _amend(st, runtime, agent, _without_workers(st, keep, step), before, False, bool(ended))
