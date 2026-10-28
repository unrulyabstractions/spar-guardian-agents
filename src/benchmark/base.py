"""What a benchmark is: the protocol every CheatBench task we run implements.

A benchmark gives one Inspect sample, the tools its agents use, the setup that runs before the agents,
the scorers that grade what they left behind, and the ways our harness departs from CheatBench's own runner.
The ORBIT configuration (roster, board, schedule) lives in src/, never here.

The sample and the tools depend on the run's mode (SwarmConfig.resolve_mode): `solo_task` gives each agent its own
copy of the task, `shared_task` one task for the team, and `collaboration_needed` one issue split into parts. A lone
agent always gets CheatBench's own layout.

Access. A benchmark names the items of its exploit (`whole_access`, `split_items`, `valid_item`), and
`access_plan` says what each agent holds under the config's `access` policy (src/configuration/access.py): the
items dealt over the neutral agents under `split` (a regulator or a disregulator holds nothing) and `split_copy`
(each holds a copy of one dealt item), everything under `all`, for a solo_task team, a lone agent or fewer than two
neutral agents, or an explicit map. The sample builds each agent's copy, tools or permissions from that plan, and
src/tasks.py records the plan as metadata["cheatbench"]["access"].
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from inspect_ai.dataset import Sample
from inspect_ai.scorer import Scorer
from inspect_ai.solver import Solver
from orbit.tools import ToolFactory

from src.configuration import access as access_policy
from src.configuration.access import Access

if TYPE_CHECKING:
    from src.configuration.swarm_config import SwarmConfig

# ORBIT appends its submit instructions to the system prompt only when the agent has the submit tool.
SUBMIT_PROMPT_DEVIATION = "orbit_assistant_and_submit_prompt_appended_to_system_prompt"
NO_SUBMIT_DEVIATIONS = [
    "orbit_assistant_prompt_appended_to_system_prompt",
    "agents_have_no_submit_tool_and_run_until_their_turn_cap_or_the_time_limit",
]


@dataclass(frozen=True)
class AgentTools:
    """The tools every agent gets, in the three shapes ORBIT takes them."""

    names: list[str]
    """AgentSpec.tools for every agent."""
    bindings: dict[str, ToolFactory] = field(default_factory=dict)
    """ORBIT tool_bindings: tools built per agent. Empty for ORBIT's built-in tools."""
    tool_kwargs: dict[str, dict] = field(default_factory=dict)
    """SetupConfig.tool_kwargs for ORBIT's built-in bash and python tools."""


class Benchmark(ABC):
    """One CheatBench task as an Inspect sample plus its tools, setup and scorers."""

    name: str
    """CheatBench's task name, such as "openmath_agent" or "software_engineering"."""
    task_root: Path
    """The CheatBench task folder that holds judge_schema.py."""
    workdir: str
    """The agents' working directory in the sandbox (the parent of each agent's copy in openmath solo_task)."""

    def check(self, config: SwarmConfig) -> None:
        """Raise when this benchmark cannot run the config: a mode it lacks, a scale it cannot hold, or an explicit
        access map that cannot apply (a lone agent and a solo_task team hold everything) or names items it does not
        know. Subclasses extend it and call this one."""
        access = config.resolve_access(self.name)
        if isinstance(access, dict) and (config.n_agents < 2 or config.resolve_mode(self.name) == "solo_task"):
            raise ValueError("an explicit access map cannot apply: a lone agent and a solo_task team hold everything")
        access_policy.check_map(access, config.names, self.valid_item)

    def whole_access(self) -> list[str]:
        """What an agent that holds everything holds."""
        return ["whole"]

    @abstractmethod
    def split_items(self, n_neutral: int) -> list[str]:
        """The items of the exploit dealt over n neutral agents under access=split, in deal order."""

    def valid_item(self, item: str) -> bool:
        """Whether an explicit access map may name this item."""
        return item in self.whole_access()

    def applied_access(self, agents: list[str], config: SwarmConfig | None,
                       roles: dict[str, str] | None) -> Access:
        """The policy access_plan applies, which the run records: all without a config, for a lone agent, in
        solo_task, and under split or split_copy when fewer than two agents are neutral; else the config's."""
        if config is None or len(agents) < 2 or config.resolve_mode(self.name) == "solo_task":
            return "all"
        access = config.resolve_access(self.name)
        if access in access_policy.SPLITS and len(access_policy.neutral_agents(agents, roles)) < 2:
            return "all"
        return access

    def access_plan(self, agents: list[str], config: SwarmConfig | None,
                    roles: dict[str, str] | None) -> dict[str, list[str]]:
        """What each agent holds under the applied policy (src/configuration/access.py plan)."""
        return access_policy.plan(self.applied_access(agents, config, roles), agents, roles, self.split_items,
                                  self.whole_access())

    @abstractmethod
    def time_limit_s(self) -> int:
        """The task's own time limit in seconds."""

    @abstractmethod
    def sample(self, agents: list[str], *, instruction_suffix: str = "", config: SwarmConfig | None = None,
               roles: dict[str, str] | None = None) -> Sample:
        """One sample for this roster under the config's mode. config None builds CheatBench's own layout.

        sample.input is the instruction plus the suffix. metadata["cheatbench"] carries the task, the variant,
        the instruction, the prompt file and the timeout. It may also carry "first_message" {agent: text} when
        agents start from different messages, and "judge_prompt" {agent: text} when the judge should see more
        than the instruction as an agent's rules. roles {agent: role} says which agents are neutral ("worker"),
        for the prompts that name team_submit; None means every agent is neutral.
        """

    @abstractmethod
    def tools(self, agents: list[str], config: SwarmConfig | None = None) -> AgentTools:
        """The tools for this roster under the config's mode. The board tools are added by src/tasks.py."""

    @abstractmethod
    def setup(self) -> Solver | None:
        """The Inspect task_setup solver that runs before the agents, or None."""

    @abstractmethod
    def scorers(self) -> list[Scorer]:
        """The scorers that grade the outcome. The judge scorer is added by the task factory."""

    @abstractmethod
    def deviations(self, n_agents: int, mode: str | None = None) -> list[str]:
        """How a run with this many agents in this mode departs from CheatBench's own runner, as short labels.

        mode None gives the labels of CheatBench's own layout."""

    def run_deviations(self, n_agents: int, *, shutdown: bool = True, mode: str | None = None) -> list[str]:
        """deviations() for a run whose agents do or do not have ORBIT's submit tool.

        Without the submit tool ORBIT appends no submit instructions, and an agent cannot end its own episode.
        """
        labels = self.deviations(n_agents, mode)
        if shutdown or SUBMIT_PROMPT_DEVIATION not in labels:
            return labels
        at = labels.index(SUBMIT_PROMPT_DEVIATION)
        return labels[:at] + NO_SUBMIT_DEVIATIONS + labels[at + 1:]
