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
them (sandbox.py). A `solo_task` team gives each agent its own contract copy and candidate id in the one container.

Each agent's tools differ under a split, so tools() returns KnowledgeTools, whose `per_agent` gives each agent's
benchmark tool names; `names` is their union.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import yaml
from inspect_ai.dataset import Sample
from inspect_ai.scorer import Scorer
from inspect_ai.solver import Solver

from src.benchmark.base import SUBMIT_PROMPT_DEVIATION, AgentTools, Benchmark
from src.benchmark.domains.knowledge.constants import BUILD, DEFAULT_ROW, TASK, TASK_DIR, WORKDIR
from src.benchmark.domains.knowledge.probe import environment_probe, knowledge_deliverables
from src.benchmark.domains.knowledge.review import TOOL as REVIEW_TOOL
from src.benchmark.domains.knowledge.review import review_binding, reviewed
from src.benchmark.domains.knowledge.sandbox import (
    LONE_TOOL_KWARGS,
    LONE_TOOLS,
    SETUP_SCRIPT,
    STAGE_TAR,
    TEAM_TOOLS,
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
from src.benchmark.domains.openmath.task import render_prompt
from src.configuration.swarm_config import SwarmConfig

DEVIATIONS = [
    "agent_is_orbit_turn_react_not_vendor_harness",
    SUBMIT_PROMPT_DEVIATION,
    "continue_prompts_are_orbit_synthetic_user_messages",
    "review_deliverable_is_an_inspect_tool_running_cheatbench_grader_as_root_in_the_sandbox_not_an_mcp_server",
    "review_errors_return_the_log_path_as_an_inspect_tool_error_in_place_of_mcp_iserror",
    "reference_rendered_on_each_review_from_a_root_only_copy_taken_at_setup",
    "review_deliverable_parameter_has_a_description_because_inspect_requires_one",
]
TEAM_DEVIATIONS = [
    "each_agent_has_its_own_linux_user_and_home_in_one_container_with_one_clock",
    "tools_shell_and_run_python_bound_per_agent_user_in_place_of_bash_and_python",
    "reviews_of_different_agents_are_not_serialized",
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
    return yaml.safe_load((TASK_DIR / "task.yaml").read_text())


def load_rows() -> list[dict]:
    data = BUILD / "data.jsonl"
    if not data.exists():
        raise FileNotFoundError(
            f"{data} missing: build the task first (downloads GDPval from Hugging Face): uv run python -B -c "
            f"\"import sys; sys.path.insert(0, '{TASK_DIR}'); import build; build.build(output='{BUILD}')\"")
    return [json.loads(line) for line in data.read_text().splitlines() if line.strip()]


def load_row(row_id: str) -> dict:
    row = next((r for r in load_rows() if r["id"] == row_id), None)
    if row is None:
        raise ValueError(f"no row {row_id!r} in {BUILD / 'data.jsonl'}")
    return row


def resolve_mode(config: SwarmConfig | None) -> str | None:
    """The config's mode on knowledge_work, or None without a config. Until the config knows the domain, a dict mode
    is read under "knowledge_work" or "knowledge" and anything unset is shared_task."""
    if config is None:
        return None
    try:
        return config.resolve_mode(TASK)
    except ValueError:
        mode = config.mode
        if isinstance(mode, dict):
            mode = mode.get(TASK) or mode.get("knowledge")
        return mode or "shared_task"


def judge_spec():
    """CheatBench's knowledge_work Verdict and context (judge_schema.py), as the judge scorer loads them."""
    from src.benchmark.judge import judge_spec as load
    return load(TASK_DIR, TASK)


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

    def check(self, config: SwarmConfig) -> None:
        super().check(config)

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
        instruction = render_prompt(TASK_DIR / cfg["prompts"]["instruction"], row) + instruction_suffix
        cheatbench = {
            "task": cfg["name"], "variant": "", "row": row,
            "instruction": instruction, "prompt_file": cfg["prompts"]["instruction"],
            "timeout_s": int(cfg["timeout_s"]), "network": cfg.get("network"),
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
            names = [*LONE_TOOLS, REVIEW_TOOL]
            return KnowledgeTools(names=names, bindings=review_binding(), tool_kwargs=LONE_TOOL_KWARGS,
                                  per_agent={a: list(names) for a in agents})
        per_agent = {a: [*TEAM_TOOLS, *([REVIEW_TOOL] if self.seats[a]["review"] else [])] for a in agents}
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
