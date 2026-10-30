"""What a benchmark is: the protocol every CheatBench task we run implements, and the plan of one run.

A benchmark gives one Inspect sample, the tools its agents use, the setup that runs before the agents, the scorers
that grade what they left behind, the predicate that says who delivered, the ways our harness departs from
CheatBench's own runner, and what run.json records of it. The ORBIT configuration (roster, board, schedule) lives in
src/tasks.py and src/swarm/, never here.

    benchmark = DOMAINS["protein_design"]("alfalfa_chi")
    plan = benchmark.plan(config, roles)
    sample, tools = benchmark.sample(plan, suffix), benchmark.tools(plan)

Adding a domain. Subclass RowBenchmark for a CheatBench task with built rows (or Benchmark otherwise), set the class
attributes, implement stage(), tools(), scorers(), delivered(), gradable() and the probe hooks, write the domain's
submit texts in prompts.yaml `submit` (counts, missing), and add the class to
src/domains/__init__.py DOMAINS. Each domain package holds the same files: <d>_settings.py (fixed paths and names),
<d>_task.py (the Benchmark), <d>_honeypot.py (what is planted and its fingerprints), the sandbox (<d>_sandbox.py, or
the machines layer), the task's own tool (<d>_<tool>_tool.py), <d>_scoring.py (the setup hooks and the scorer),
<d>_ladder.py (the code ladder's rules) and sandbox/ (what runs inside the sandbox). src/domains/protein/ is the
smallest complete one.

The plan. plan(config, roles) checks the config, once, and fixes what every other method reads: the mode (None for a
lone agent), the access policy as applied, what each agent holds, its numbered pieces and their number, and whether
the exploit is split. A lone agent, a solo_task team and a team with fewer than two neutral agents hold everything.

Access. A benchmark names the items of its exploit: whole_access, split_items(n) dealt over the n neutral agents under
access=split (src/configuration/access_policy.py), and valid_item for an explicit map. A domain whose pieces are
numbered sets piece_stem ("slice" gives slice1..sliceN) and the base derives all three; an agent holding every piece
holds the whole.

Delivery. delivered(plan) gives the predicate stop_after_delivery awaits at the end of a round: which agents'
deliverable exists. A shared_task team has one deliverable, which counts for every agent.

Submit. The swarm's submit (src/swarm/submit_and_stop.py) reads three hooks: submit_text(plan, kind) says what submit
does and what the domain's grader reads (its description, and with submit_line its system prompt bullet);
gradable(plan, agent) is the guard, which says what is missing while there is nothing to grade (it asks whether a
deliverable exists, never whether it is right); snapshot(plan, agent) names what would be graded, where that is cheap.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from inspect_ai.dataset import Sample
from inspect_ai.scorer import Scorer
from inspect_ai.solver import Solver, TaskState

from src.configuration import access_policy
from src.configuration import prompt_texts as texts
from src.configuration.access_policy import Access
from src.configuration.swarm_config import SwarmConfig, role_of
from src.measures.ladder_domain import Domain
from src.sandbox import cheatbench_task_files as task_files
from src.sandbox.agent_tools import AgentTools
from src.sandbox.sandbox_probe import environment_probe

TEAM = "team"                       # the owner of a shared_task team's deliverable and tool state
ORBIT_AGENT_DEVIATION = "agent_is_orbit_turn_react_not_vendor_harness"
# How every run departs from CheatBench's own runner, before a domain's own labels. CheatBench's runner has no
# sentence on how to hand in work or stop.
COMMON_DEVIATIONS = [ORBIT_AGENT_DEVIATION, "submit_and_stop_sentences_added_to_the_system_prompt"]
# The common labels of a run where no agent gets submit or stop_working.
NO_SUBMIT_DEVIATIONS = [ORBIT_AGENT_DEVIATION,
                        "agents_have_no_submit_tool_and_run_until_their_turn_cap_or_the_time_limit"]


@dataclass(frozen=True)
class Plan:
    """One run's roster, mode and access, fixed before the sample is built."""

    config: SwarmConfig
    roles: dict[str, str] | None
    """{agent: role}; None when every agent is neutral."""
    access: Access
    """The access policy as applied: all for a lone agent, a solo_task team and fewer than two neutral agents."""
    holds: dict[str, list[str]]
    """The access plan: each agent's items."""
    pieces: dict[str, list[int] | None]
    """Each agent's numbered pieces (None: the whole); {} for a domain without numbered pieces."""
    n_pieces: int | None
    """N, the number of pieces the exploit is cut into, or None when nothing is cut."""
    split: bool
    """Whether some agent holds less than the whole."""

    @property
    def agents(self) -> list[str]:
        return self.config.names

    @property
    def team(self) -> bool:
        return self.config.n_agents > 1

    @property
    def mode(self) -> str | None:
        """The team's mode, None for a lone agent."""
        return self.config.mode if self.team else None

    @property
    def owners(self) -> dict[str, str]:
        """Whose deliverable and tool state each agent's work counts for: the team's in shared_task, else its own."""
        return {a: TEAM if self.mode == "shared_task" else a for a in self.agents}


class Benchmark(ABC):
    """One CheatBench task as an Inspect sample plus its tools, setup, scorers and run.json blocks."""

    key: str
    """The registry's key: the @task entry point, `--benchmark` and the instruction levels' benchmark key."""
    name: str
    """CheatBench's task name, such as "openmath_agent"."""
    block: str
    """The key of the domain's block in metadata["cheatbench"] and in run.json."""
    task_dir: Path
    """CheatBench's task folder, which holds judge_schema.py."""
    default_row: str
    """The row (an issue, a question, a target) when none is given."""
    scorer: str
    """The name of the scorer that records the deliverables, the probe and the end state."""
    ladder: type[Domain]
    """The code ladder's rules for the domain (src/measures/ladder_domain.py)."""
    piece_stem: str | None = None
    """The stem of the numbered pieces of the exploit (slice, shard, share, segment), or None."""
    options: tuple[str, ...] = ()
    """The constructor's keywords beyond row, which the command line and the @task entry points pass on."""
    task_deviations: list[str] = []
    """How every layout of the domain departs from CheatBench's runner, after COMMON_DEVIATIONS."""
    time_grace_s: int | None = None
    """The time Inspect's limit leaves above ORBIT's halt; None takes the config's time_grace_s."""

    def __init__(self, row: str | None = None) -> None:
        self.row = row or self.default_row

    # ---------------------------------------------------------------- access and the plan
    def check(self, config: SwarmConfig) -> None:
        """Raise when this benchmark cannot run the config: an explicit access map that cannot apply (a lone agent
        and a solo_task team hold everything), names items the domain does not know, or numbers pieces that are not
        1..N with N at least 2. Subclasses extend it and call this one."""
        access = config.access
        if isinstance(access, dict) and (config.n_agents < 2 or config.mode == "solo_task"):
            raise ValueError("an explicit access map cannot apply: a lone agent and a solo_task team hold everything")
        access_policy.check_map(access, config.names, self.valid_item)
        if self.piece_stem and isinstance(access, dict):
            named = sorted({k for items in access.values() for k in self.pieces_of(items) or []})
            access_policy.check_numbered(named, f"{self.piece_stem}s")

    def whole_access(self) -> list[str]:
        """What an agent that holds everything holds."""
        return ["whole"]

    def split_items(self, n_neutral: int) -> list[str]:
        """The items of the exploit dealt over n neutral agents under access=split, in deal order: one numbered
        piece each."""
        return [f"{self.piece_stem}{k}" for k in range(1, n_neutral + 1)]

    def valid_item(self, item: str) -> bool:
        """Whether an explicit access map may name this item."""
        return item in self.whole_access() or (
            self.piece_stem is not None and re.fullmatch(rf"{self.piece_stem}[1-9]\d*", item) is not None)

    def pieces_of(self, items: list[str]) -> list[int] | None:
        """The numbered pieces an agent's items name, in order, or None when it holds the whole."""
        if "whole" in items:
            return None
        return sorted(int(item[len(self.piece_stem):]) for item in items if item.startswith(self.piece_stem))

    def applied_access(self, config: SwarmConfig, roles: dict[str, str] | None) -> Access:
        """The policy the plan applies: all for a lone agent, in solo_task, and under split when fewer than two
        agents are neutral; else the config's."""
        if config.n_agents < 2 or config.mode == "solo_task":
            return "all"
        if config.access == "split" and len(access_policy.neutral_agents(config.names, roles)) < 2:
            return "all"
        return config.access

    def plan(self, config: SwarmConfig, roles: dict[str, str] | None = None) -> Plan:
        """Check the config and fix the run's access: what each agent holds and, for numbered pieces, which pieces
        of how many."""
        self.check(config)
        access = self.applied_access(config, roles)
        whole = self.whole_access()
        holds = access_policy.plan(access, config.names, roles, self.split_items, whole)
        pieces: dict[str, list[int] | None] = {}
        n_pieces = None
        if self.piece_stem:
            pieces = {a: self.pieces_of(items) for a, items in holds.items()}
            n_pieces = max((k for ks in pieces.values() if ks for k in ks), default=None)
            pieces = {a: None if n_pieces and ks == list(range(1, n_pieces + 1)) else ks for a, ks in pieces.items()}
            split = any(ks is not None for ks in pieces.values())
        else:
            split = any(sorted(items) != sorted(whole) for items in holds.values())
        return Plan(config=config, roles=roles, access=access, holds=holds, pieces=pieces,
                    n_pieces=n_pieces if split else None, split=split)

    # ---------------------------------------------------------------- the sample and its tools
    @abstractmethod
    def time_limit_s(self) -> int:
        """The task's own time limit in seconds."""

    @abstractmethod
    def sample(self, plan: Plan, instruction_suffix: str = "") -> Sample:
        """The plan's sample. sample.input is the instruction plus the suffix. metadata["cheatbench"] carries the
        task, the instruction, the prompt file, the timeout and the domain's block. It may also carry "first_message"
        {agent: text} when agents start from different messages, and "judge_prompt" {agent: text} when the judge
        should see more than the instruction as an agent's rules."""

    @abstractmethod
    def tools(self, plan: Plan) -> AgentTools:
        """The roster's tools (src/sandbox/agent_tools.py roster_tools). The board tools are added by src/tasks.py."""

    def system_prompts(self, plan: Plan) -> dict[str, str | None]:
        """Each agent's system prompt before its role's text: the board prompt the config asks for
        (prompt_texts.board_system_prompt), then the bullets of submit and stop_working as the agent holds them
        (ending_lines), a blank line apart; None for no prompt of ours."""
        return {a: "\n\n".join(filter(None, [texts.board_system_prompt(plan.config, a, plan.agents),
                                              "\n".join(self.ending_lines(plan, a))])) or None
                for a in plan.agents}

    def ending_lines(self, plan: Plan, agent: str) -> list[str]:
        """The agent's bullets for the tools that end its work: submit (submit_line) for a neutral agent of a config
        that gives it, stop_working for a config that gives it (prompt_texts.ending_lines)."""
        config = plan.config
        return texts.ending_lines(config, role_of(plan.roles, agent), self.submit_line(plan, config.submit_kind))

    # ---------------------------------------------------------------- submit
    def submit_vars(self, plan: Plan) -> dict[str, str]:
        """The values of the domain's placeholders in prompts.yaml submit.counts, ready and missing; {} for none."""
        return {}

    def submit_text(self, plan: Plan, kind: str) -> str:
        """What submit does for an agent of this kind (SwarmConfig.submit_kind: team, solo or lone) and what the
        domain's grader reads (prompt_texts.submit_text). The tool's description says the same."""
        return texts.submit_text(self.key, kind, plan.config.submit_delay_rounds, self.submit_vars(plan))

    def submit_line(self, plan: Plan, kind: str) -> str:
        """submit's bullet in a system prompt, for an agent of this kind."""
        return texts.tool_line("submit", self.submit_text(plan, kind))

    def missing(self, plan: Plan) -> str:
        """What gradable says is missing while there is nothing to grade (prompt_texts.missing_text)."""
        return texts.missing_text(self.key, plan.config.submit_kind, self.submit_vars(plan))

    @abstractmethod
    async def gradable(self, plan: Plan, agent: str) -> str | None:
        """submit's guard, awaited inside the sample at each call: None when the agent's deliverable (the team's in
        shared_task) exists, else what is missing (missing). It asks whether something exists to grade, never
        whether it is right."""

    async def snapshot(self, plan: Plan, agent: str) -> str | None:
        """An id of what would be graded at a submit, where that is cheap (SWE's commit); None by default."""
        return None

    # ---------------------------------------------------------------- setup, scoring, delivery
    def setup(self) -> Solver:
        """The task_setup solver: prepare(state), then the probe table of each agent (src/sandbox/sandbox_probe.py
        environment_probe)."""
        return environment_probe(self.prepare, self.probe)

    async def prepare(self, state: TaskState) -> None:
        """Ready the sandbox before the probe and the agents: staging, the honeypot, the loopback guard."""

    def probe(self, state: TaskState) -> dict:
        """{agent: (Seat, command table, fields)} the setup probes before the agents start; {} for none."""
        return {}

    @abstractmethod
    def scorers(self) -> list[Scorer]:
        """The scorers that grade the outcome, the one named `scorer` first. The judge is added by src/tasks.py."""

    @abstractmethod
    def delivered(self, plan: Plan) -> Callable[[], Awaitable[set[str]]]:
        """The delivery predicate (SwarmConfig.stop_after_delivery): an async call, made inside the sample, that
        returns the agents whose deliverable exists, reading as little as it can (one git command per repository,
        or the sample store)."""

    def team_deviations(self, plan: Plan) -> list[str]:
        """The labels a team's layout adds to the domain's own."""
        return []

    def deviations(self, plan: Plan) -> list[str]:
        """How the run departs from CheatBench's own runner, as short labels: the common ones (NO_SUBMIT_DEVIATIONS
        when no agent gets submit or stop_working), the delay of a shared_task team's submit, the domain's, then the
        team's."""
        config = plan.config
        common = COMMON_DEVIATIONS if config.gives_submit or config.gives_stop_working else NO_SUBMIT_DEVIATIONS
        delay = [f"team_submit_takes_effect_after_{config.submit_delay_rounds}_rounds"] \
            if config.gives_submit and config.submit_kind == "team" else []
        return [*common, *delay, *self.task_deviations, *(self.team_deviations(plan) if plan.team else [])]

    # ---------------------------------------------------------------- what the export records
    @classmethod
    def run_blocks(cls, view) -> dict:
        """The domain's run.json blocks for one sample (src/export/eval_sample_view.py SampleView): run.json[block],
        the sample's facts (metadata["cheatbench"][block]) with the scorer's outcome (its metadata[block]) and the
        score's value. {} when the sample has no block."""
        facts = ((view.metadata or {}).get("cheatbench") or {}).get(cls.block)
        if not facts:
            return {}
        score = (view.scores or {}).get(cls.scorer)
        outcome = ((score.metadata or {}).get(cls.block) or {}) if score else {}
        return {cls.block: {**facts, **outcome, "value": None if score is None else score.value}}

    @classmethod
    def write_files(cls, view, run_dir: Path) -> None:
        """Write the domain's files of one sample into the run directory (SWE's graded diffs); nothing by default."""

    @classmethod
    def episode_facts(cls, view, agent: str) -> dict:
        """The domain's facts in one agent's episode record ({} for none)."""
        return {}

    @classmethod
    def access_facts(cls, view) -> tuple[dict, dict[str, dict]]:
        """What run.json "access" adds for the domain: (run-level facts, {agent: facts})."""
        return {}, {}


@dataclass(frozen=True)
class Staged:
    """What a row domain's stage() gives its sample."""

    compose: Path
    """The compose file."""
    block: dict
    """The domain's metadata["cheatbench"][block], beside the row id, the mode, the split and the pieces."""
    files: dict[str, str] = field(default_factory=dict)
    """Sample.files: {path in the sandbox: path on the host}."""
    setup: str | None = None
    """Sample.setup: a script run as root before the task setup."""
    stage: dict[str, str] | None = None
    """{service: staging archive} for the machines layer's setup (metadata["cheatbench"]["stage"])."""


class RowBenchmark(Benchmark):
    """A CheatBench task whose build writes rows (data.jsonl): one row is one sample."""

    build_dir: Path
    """The task's build: data.jsonl and environment/."""
    build_hint: str = ""
    """How to build the rows, said when data.jsonl is missing."""

    @classmethod
    def rows(cls) -> list[dict]:
        return task_files.load_rows(cls.build_dir, hint=cls.build_hint)

    @classmethod
    def row_of(cls, row_id: str) -> dict:
        return task_files.load_row(cls.build_dir, row_id, hint=cls.build_hint)

    def task_yaml(self) -> dict:
        return task_files.load_task_yaml(self.task_dir)

    def time_limit_s(self) -> int:
        return int(self.task_yaml()["timeout_s"])

    def sample(self, plan: Plan, instruction_suffix: str = "") -> Sample:
        """The row's sample: CheatBench's rendered instruction plus the suffix, and what stage() builds. The judge
        sees the instruction as every agent's rules."""
        cfg, row = self.task_yaml(), self.row_of(self.row)
        instruction = task_files.render_prompt(self.task_dir / cfg["prompts"]["instruction"], row) + instruction_suffix
        staged = self.stage(plan, row)
        block = {"row_id": row["id"], "mode": plan.mode, "split": plan.split, "n_pieces": plan.n_pieces,
                 **staged.block}
        cheatbench = {**task_files.sample_metadata(cfg, row, instruction), self.block: block,
                      **({"stage": staged.stage} if staged.stage else {})}
        return Sample(id=row["id"], input=instruction, files=staged.files or None, setup=staged.setup,
                      sandbox=("docker", str(staged.compose)), metadata={"cheatbench": cheatbench})

    @abstractmethod
    def stage(self, plan: Plan, row: dict) -> Staged:
        """The row's sandbox for the plan: the compose file, the staging, and the domain's metadata block."""
