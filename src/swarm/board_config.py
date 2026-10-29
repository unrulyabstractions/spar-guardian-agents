"""The message board as ORBIT configuration values.

A board is one shared channel that every agent on the roster can read and write. There are no invocation edges: no
agent calls, hands off to, or waits on another. ORBIT attaches channel_post, channel_read and channel_list to every
agent as soon as a CommunicationConfig is set (orbit/solvers/orchestrator.py _build_executor), so the specs list only
their task tools. Agents reach the channel through board_status, board_read and board_post (src/swarm/pull_board.py),
and turn_rules.TurnRulesFilter withholds ORBIT's channel tools from the provider. Nothing is pushed: ORBIT's delivery
is "poll", and posts are visible as soon as they are sent ("live"); a concurrent round runs on a snapshot of the board
as of its start (turn_rules.schedule). The board's system prompt text is built by swarm_config.board_system_prompt.

    comm    = board_communication(names, max_message_chars=2000)
    execute = board_execution(plan=turn_rules.schedule(config, names))
    sched   = board_scheduler(max_turns=config.max_turns + 5, max_time_seconds=3600)

Turns follow a written plan (src/swarm/turn_rules.py schedule). ORBIT's scheduling_mode does not apply once a plan
is set.
"""
from __future__ import annotations

from orbit.configs.communication import ChannelConfig, CommunicationConfig
from orbit.configs.execution import AgentGroup, ExecutionConfig
from orbit.configs.scheduler import SchedulerConfig
from orbit.configs.scheduling import ScheduleConfig

from src.configuration.settings import BOARD_ID

BOARD_NAME = "team board"


def board_channel(agents: list[str], *, channel_id: str = BOARD_ID, name: str = BOARD_NAME) -> ChannelConfig:
    return ChannelConfig(id=channel_id, name=name, readers=tuple(agents), writers=tuple(agents), private=False)


def board_communication(agents: list[str], *, max_message_chars: int = 4000) -> CommunicationConfig:
    """The board as ORBIT's CommunicationConfig: one channel, nothing pushed, posts visible at once."""
    return CommunicationConfig(channels=(board_channel(agents),), delivery="poll", visibility="live",
                               max_message_chars=max_message_chars)


def board_execution(*, plan: ScheduleConfig | None = None, agent_groups: list[AgentGroup] | None = None,
                    initial_context: str = "auto") -> ExecutionConfig:
    """Scheduled execution under a written plan, one generation and every call in it per step (quantum
    model_step).

    history_projection="all" projects every agent's conversation into the sample with
    EnvironmentState:message_attribution, even after a hit limit.
    """
    return ExecutionConfig(executor="scheduled", quantum="model_step", plan=plan, history_projection="all",
                           agent_groups=list(agent_groups or []), initial_context=initial_context)


def board_scheduler(*, max_turns: int, max_time_seconds: int) -> SchedulerConfig:
    return SchedulerConfig(max_turns=max_turns, max_time_seconds=max_time_seconds, halt_on_convergence=False,
                           convergence_window=1, health_checks=False)
