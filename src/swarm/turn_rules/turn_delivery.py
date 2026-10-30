"""The stop-after-delivery rule (SwarmConfig.stop_after_delivery).

The activation that closes a round awaits the benchmark's delivery predicate while some active agent's deliverable
is unseen. An agent alone or in solo_task is ended "delivered" after N more of its working steps once its own
deliverable is seen. A team is ended "delivered" N rounds after the round that showed its deliverable. The record
lives in the store as Delivered.

    record = delivery()
    if record is not None: keep, ended = await after_delivery(st, record, runtime, step, agent, keep)
"""
from __future__ import annotations

from inspect_ai.log import transcript
from inspect_ai.util import store_as
from orbit.configs.scheduling import ScheduleStep

from src.swarm.turn_rules.turn_state import DELIVERED, DELIVERED_TAG, PREDICATE, Delivered, TurnState, running_record
from src.swarm.turn_rules.turn_steps import step_round


def delivery() -> Delivered | None:
    """The Delivered record when stop_after_delivery is on in this sample, else None, with nothing written."""
    return store_as(Delivered) if PREDICATE.get() is not None else None


def spent(record: Delivered, agent: str, used: int) -> bool:
    """Whether an agent with its own deliverable has taken its N working steps since the deliverable was seen."""
    seen = record.seen.get(agent)
    return not record.team and seen is not None and used >= seen["turn"] + record.after


def _holds(agent: str, steps: list[dict]) -> bool:
    return any(agent in s["agents"] for s in steps)


def _round_over(runtime, step: ScheduleStep | None, agent: str, keep: list[dict]) -> bool:
    """Whether this activation closes its round: the last activation of its step to complete (ORBIT calls back in
    activation order once a concurrent step has finished; StepRecord.activations), with no step of the round left in
    the plan."""
    if step is None:
        return False
    current = running_record(runtime)
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
    record.seen = {**record.seen, **{a: {"round": round_number, "turn": st.work_steps.get(a, 0)} for a in shown}}
    if record.team:
        record.halt_round = round_number + record.after
    transcript().info({DELIVERED_TAG: {"kind": "seen", "round": round_number, "agents": shown,
                                       "halt_round": record.halt_round}})


def end(st: TurnState, agents: list[str], round_number: int) -> None:
    """End these agents "delivered"."""
    st.ended = {**st.ended, **dict.fromkeys(agents, DELIVERED)}
    transcript().info({DELIVERED_TAG: {"kind": "ended", "round": round_number, "agents": agents}})


async def after_delivery(st: TurnState, record: Delivered, runtime, step: ScheduleStep | None, agent: str,
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
        ended = [a for a in active if spent(record, a, st.work_steps.get(a, 0)) and _holds(a, keep)]
    if not ended:
        return keep, ended
    end(st, ended, round_number)
    keep = [{**s, "agents": [a for a in s["agents"] if a not in ended]} for s in keep]
    return [s for s in keep if s["agents"]], ended
