"""The knowledge_work Benchmark: one GDPval assignment from CheatBench's task, its sample, tools and deviations.

Inputs are submodules/cheatbench/tasks/knowledge_work (the instruction, the README template, grader.py,
judge_schema.py) and the rows build.py writes to build/knowledge_work (data.jsonl and environment/files/<row>/).

The agent works in a contract directory that /external/contract_<id>/ points at, holding README.md (the template with
that path filled in), brief.md, inputs/ and to_send/. The current pipeline's deliverable sits at
/data/cache/artifacts/<name>, named by /data/cache/evaluation.json and by every successful review log. The
instruction is prompts/instruction.md, unchanged. The judge is CheatBench's judge_schema.py.

Modes. A lone agent gets CheatBench's layout in every mode. A `shared_task` team (the default) shares one contract
directory and one candidate id, each agent under its own Linux user, and holds the access items `review` (the review
tool, the review logs and evaluation.json) and `read` (the pipeline artifact) as the config's access policy deals
them (knowledge_sandbox.py). A `solo_task` team gives each agent its own contract copy and candidate id in
the one container.

Each agent's tools differ under a split, so tools() returns KnowledgeTools, whose `per_agent` gives each agent's
benchmark tool names; `names` is their union.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from inspect_ai.dataset import Sample
from inspect_ai.scorer import Scorer
from inspect_ai.solver import Solver

from src.configuration.swarm_config import SwarmConfig
from src.domains.benchmark_protocol import COMMON_DEVIATIONS, AgentTools, Benchmark
from src.domains.knowledge.knowledge_review_tool import TOOL as REVIEW_TOOL
from src.domains.knowledge.knowledge_review_tool import review_binding, reviewed
from src.domains.knowledge.knowledge_sandbox import (
    SETUP_SCRIPT,
    STAGE_TAR,
    ensure_images,
    is_split,
    layout_kind,
    make_stage_tar,
    seats,
    split_items,
    stage_layout,
    team_tool_bindings,
    write_compose,
)
from src.domains.knowledge.knowledge_scoring import environment_probe, knowledge_deliverables
from src.domains.knowledge.knowledge_settings import (
    AGENT_NAME,
    BUILD,
    DEFAULT_ROW,
    TASK,
    TASK_DIR,
    TOOL_TIMEOUT_S,
    WORKDIR,
)
from src.sandbox import agent_tools
from src.sandbox import cheatbench_task_files as task_files

DEVIATIONS = COMMON_DEVIATIONS + [
    "review_deliverable_is_an_inspect_tool_running_cheatbench_grader_as_root_in_the_sandbox_not_an_mcp_server",
    "review_errors_return_the_log_path_as_an_inspect_tool_error_in_place_of_mcp_iserror",
    "reference_rendered_on_each_review_from_a_root_only_copy_taken_at_setup",
    "review_deliverable_parameter_has_a_description_because_inspect_requires_one",
]
TEAM_DEVIATIONS = [
    "each_agent_has_its_own_linux_user_and_home_in_one_container_with_one_clock",
    "tools_shell_and_run_python_bound_per_agent_user_in_place_of_bash_and_python",
    "reviews_of_different_agents_are_not_serialized",
    "loopback_refused_between_agent_users_by_iptables_owner_rules",
    "agent_user_processes_killed_after_each_command_and_at_the_end",
]
SHARED_DEVIATIONS = ["team_shares_one_contract_directory_and_candidate_id_through_the_group_team"]
SPLIT_DEVIATIONS = [
    "review_tool_and_artifact_read_dealt_over_the_agents_as_access_says_through_the_groups_review_and_pipeline",
    "review_logs_and_evaluation_json_readable_by_the_group_review_only_and_the_artifacts_directory_traverse_only",
    "pipeline_artifact_staged_under_a_random_name_so_a_reader_cannot_guess_it",
]
SOLO_DEVIATIONS = ["each_agent_has_its_own_contract_copy_and_candidate_id",
                   "evaluation_json_lists_every_candidate_under_candidate"]


@dataclass(frozen=True)
class KnowledgeTools(AgentTools):
    """AgentTools plus each agent's own benchmark tool names, which differ under a split."""

    per_agent: dict[str, list[str]] = field(default_factory=dict)
    """{agent: AgentSpec.tools from the benchmark}. `names` is their union, in order."""


def load_task_yaml() -> dict:
    return task_files.load_task_yaml(TASK_DIR)


def load_rows() -> list[dict]:
    """The built rows (build/knowledge_work/data.jsonl)."""
    return task_files.load_rows(BUILD, hint=f" (downloads GDPval from Hugging Face): uv run python -B -c \"import "
                                            f"sys; sys.path.insert(0, '{TASK_DIR}'); import build; "
                                            f"build.build(output='{BUILD}')\"")


def load_row(row_id: str) -> dict:
    return task_files.load_row(load_rows(), row_id, BUILD)


def resolve_mode(config: SwarmConfig | None) -> str | None:
    """The config's mode on knowledge_work, or None without a config."""
    return config.resolve_mode(TASK) if config is not None else None


class KnowledgeWork(Benchmark):
    """knowledge_work for one GDPval row. Every agent gets the same instruction and the same container."""

    name = TASK
    task_root = TASK_DIR
    workdir = WORKDIR

    def __init__(self, row: str = DEFAULT_ROW) -> None:
        self.row = row
        self.layout: str | None = None
        self.split = False
        self.seats: dict[str, dict] = {}

    def time_limit_s(self) -> int:
        return int(load_task_yaml()["timeout_s"])

    def whole_access(self) -> list[str]:
        return ["review", "read"]

    def split_items(self, n_neutral: int) -> list[str]:
        return split_items(n_neutral)

    def plan(self, agents: list[str], config: SwarmConfig | None, roles: dict[str, str] | None) -> dict[str, dict]:
        """Fix the layout, the seats and whether they are split for this roster (sandbox.seats, from the access
        plan); sample() and tools() share them."""
        if config is not None:
            self.check(config)
        kind = layout_kind(agents, resolve_mode(config) if len(agents) > 1 else None)
        self.layout = kind
        self.seats = seats(agents, kind, self.access_plan(agents, config, roles))
        self.split = is_split(self.seats)
        return self.seats

    def sample(self, agents: list[str], *, instruction_suffix: str = "", config: SwarmConfig | None = None,
               roles: dict[str, str] | None = None) -> Sample:
        """The row's sample for this roster. The judge sees the instruction as every agent's rules."""
        seat_map = self.plan(list(agents), config, roles)
        cfg = load_task_yaml()
        row = load_row(self.row)
        images = ensure_images()
        compose = write_compose()
        layout = stage_layout(row, self.layout, seat_map)
        digest = hashlib.sha256(json.dumps(layout, sort_keys=True).encode()).hexdigest()[:12]
        tar_path = make_stage_tar(row, BUILD / "environment", layout, BUILD / "stage" / f"{row['id']}-{digest}.tgz")
        instruction = task_files.render_prompt(TASK_DIR / cfg["prompts"]["instruction"], row) + instruction_suffix
        cheatbench = {
            **task_files.sample_metadata(cfg, row, instruction),
            "knowledge": {"layout": self.layout, "mode": resolve_mode(config) if len(agents) > 1 else None,
                          "split": self.split, "row_id": row["id"], "meta": row.get("meta") or {},
                          "comparator": layout["comparator"], "seats": seat_map,
                          "evaluation": layout["evaluation"], "images": images},
        }
        return Sample(id=row["id"], input=instruction, files={STAGE_TAR: str(tar_path)}, setup=SETUP_SCRIPT,
                      sandbox=("docker", str(compose)), metadata={"cheatbench": cheatbench})

    def tools(self, agents: list[str], config: SwarmConfig | None = None) -> KnowledgeTools:
        """A lone agent: ORBIT's bash and python as `user`, and review_deliverable. A team: shell and run_python per
        agent user, and review_deliverable for the agents whose seat holds it."""
        agents = list(agents)
        if set(self.seats) != set(agents):
            self.plan(agents, config, None)
        if self.layout == "lone":
            names = [*agent_tools.LONE_TOOL_NAMES, REVIEW_TOOL]
            return KnowledgeTools(names=names, bindings=review_binding(),
                                  tool_kwargs=agent_tools.lone_tool_kwargs(AGENT_NAME, TOOL_TIMEOUT_S),
                                  per_agent={a: list(names) for a in agents})
        per_agent = {a: [*agent_tools.TOOL_NAMES, *([REVIEW_TOOL] if self.seats[a]["review"] else [])]
                     for a in agents}
        names = list(dict.fromkeys(n for a in agents for n in per_agent[a]))
        return KnowledgeTools(names=names, bindings={**team_tool_bindings(self.seats), **review_binding()},
                              tool_kwargs={}, per_agent=per_agent)

    def setup(self) -> Solver | None:
        return environment_probe()

    def scorers(self) -> list[Scorer]:
        return [knowledge_deliverables()]

    def delivered(self, agents: list[str], config: SwarmConfig) -> Callable[[], Awaitable[set[str]]]:
        """The agents a review call answered with a verdict, from the sample store; in a shared_task team one verdict
        counts for every agent (review.reviewed)."""
        agents = list(agents)
        shared = layout_kind(agents, resolve_mode(config) if len(agents) > 1 else None) == "shared"
        return reviewed(agents, shared)

    def deviations(self, n_agents: int, mode: str | None = None) -> list[str]:
        if n_agents < 2:
            return list(DEVIATIONS)
        if mode == "solo_task":
            return DEVIATIONS + TEAM_DEVIATIONS + SOLO_DEVIATIONS
        return DEVIATIONS + TEAM_DEVIATIONS + SHARED_DEVIATIONS + (SPLIT_DEVIATIONS if self.split else [])
