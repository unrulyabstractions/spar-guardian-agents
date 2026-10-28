"""The interview Benchmarks, SubsetSum and PrimeFactorization: one CheatBench interview round as a sample, its
machines, the verifier and the deviations.

Inputs are submodules/cheatbench/tasks/<task> (the instruction, grader.py, judge_schema.py) and the rows its build.py
writes to build/<task>/ (data.jsonl and environment/files/). The instruction is the row's prompt rendered as
CheatBench renders it; the judge is CheatBench's judge_schema.py and sees the instruction as every agent's rules.

Layouts. A lone agent gets CheatBench's own layout, `lone`: one container, the service `default`, with the row's
files at their absolute paths. It works as the user `user`, with ORBIT's bash and python and the verifier under
CheatBench's name. Two or more agents in solo_task or shared_task get `machines` (src/benchmark/machines/). Each agent
has its own container, named by the agent and staged at setup with the agent's own copy of the row (stage_machines).
Its `shell` and `run_python` run there. The verifier's owner is the agent, or "team" in shared_task. A team shares
one state: one 50-check budget and one sticky acceptance. The round has no parts, so `collaboration_needed` is
refused.

Access items: `whole` and the pieces of the answer key, shard1..shardN (subset_sum) or share1..shareN
(prime_factorization), dealt one per neutral agent under split; an agent's copy of the two key files holds what its
plan names (keys.py). The other staged files are the same in every container.

Departures from CheatBench's runner: the agent is ORBIT's turn_react loop; ORBIT's submit prompt; synthetic continue
prompts; the verifier runs on the host with its state in the sample store (verifier.py), with parameter descriptions
and Inspect's schema check; the agents' messages are not mirrored into the checkout's live logs, which stay as
staged; the containers have no network, where CheatBench leaves egress open and forbids it in the prompt; the staged
files carry the time the sample was built as their mtime.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from inspect_ai.dataset import Sample
from inspect_ai.scorer import Scorer
from inspect_ai.solver import Generate, Solver, TaskState, chain, solver

from src.benchmark import machines
from src.benchmark.base import SUBMIT_PROMPT_DEVIATION, AgentTools, Benchmark
from src.benchmark.domains.interview import keys
from src.benchmark.domains.interview.constants import FACTOR, SUBSET, Spec
from src.benchmark.domains.interview.probe import environment_probe, interview_deliverables
from src.benchmark.domains.interview.verifier import tool_name, verifier_binding
from src.benchmark.domains.openmath.task import render_prompt
from src.configuration.swarm_config import SwarmConfig

DEVIATIONS = [
    "agent_is_orbit_turn_react_not_vendor_harness",
    SUBMIT_PROMPT_DEVIATION,
    "continue_prompts_are_orbit_synthetic_user_messages",
    "task_tool_run_on_the_host_with_state_in_the_sample_store",
    "task_tool_parameters_have_descriptions_and_inspect_checks_their_types",
    "no_live_log_mirror_of_the_agents_messages",
    "sandbox_has_no_network_where_cheatbench_leaves_egress_open",
]
MACHINE_DEVIATIONS = [
    "each_agent_has_its_own_container_and_copy_of_the_row",
    "tools_shell_and_run_python_bound_per_agent_container",
]
SHARED_DEVIATIONS = ["team_shares_one_tool_state_in_shared_task"]
SPLIT_DEVIATIONS = {
    SUBSET.task: ["answer_key_cut_into_interleaved_shards_of_the_indices_dealt_as_access_says",
                  "shard_lines_padded_to_the_original_length_and_lines_of_agents_without_a_shard_removed"],
    FACTOR.task: ["answer_key_cut_into_additive_shares_mod_1e8_of_the_factors_dealt_as_access_says",
                  "key_files_rewritten_in_custody_form_and_lines_of_agents_without_a_share_removed",
                  "q3_stub_byte_count_rewritten_to_the_copys_cache_size"],
}


def load_task_yaml(spec: Spec) -> dict:
    return yaml.safe_load((spec.task_dir / "task.yaml").read_text())


@solver
def stage_machines() -> Solver:
    """Each agent's copy of the row into its own container (machines.stage), before the probe and the agents."""
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        seats = state.metadata["cheatbench"]["interview"]["agents"]
        for agent, tar in state.metadata["cheatbench"]["stage"].items():
            await machines.stage(seats[agent]["service"], Path(tar))
        return state
    return solve


class Interview(Benchmark):
    """One interview round of a CheatBench task, its row's key cut over the agents as the access plan says."""

    spec: Spec
    workdir = machines.WORKDIR

    def __init__(self, row: str | None = None) -> None:
        self.row = row or self.spec.default_row
        self.layout: str | None = None
        self.mode: str | None = None
        self.holds: dict[str, list[str]] = {}
        self.pieces: dict[str, list[int] | None] = {}
        self.parts: int | None = None
        self.split = False
        self.owners: dict[str, str] = {}

    def time_limit_s(self) -> int:
        return int(load_task_yaml(self.spec)["timeout_s"])

    def check(self, config: SwarmConfig) -> None:
        """The base's checks; no collaboration_needed; an explicit map names pieces 1..N with N at least 2, so no
        piece is the whole key renamed."""
        super().check(config)
        if config.resolve_mode(self.name) == "collaboration_needed":
            raise ValueError(f"{self.name} has no parts to split; run it in mode solo_task or shared_task")
        access = config.resolve_access(self.name)
        if isinstance(access, dict):
            named = sorted({k for items in access.values() for k in keys.held(items, self.spec.piece) or []})
            if named and (named != list(range(1, named[-1] + 1)) or named[-1] < 2):
                raise ValueError(f"access must name {self.spec.piece}s 1..N with N at least 2, each held by some "
                                 f"agent, got {named}")

    def split_items(self, n_neutral: int) -> list[str]:
        """One piece of the key per neutral agent."""
        return [f"{self.spec.piece}{k}" for k in range(1, n_neutral + 1)]

    def valid_item(self, item: str) -> bool:
        return item == "whole" or keys.held([item], self.spec.piece) not in (None, [])

    def plan(self, agents: list[str], config: SwarmConfig | None, roles: dict[str, str] | None) -> None:
        """Fix the layout, each agent's pieces and the verifier's owners for this roster; sample() and tools() share
        them."""
        if config is None and len(agents) > 1:
            raise ValueError("a team needs a config: its mode names the verifier's owner")
        if config is not None:
            self.check(config)
        self.mode = config.resolve_mode(self.name) if len(agents) > 1 else None
        self.layout = "machines" if self.mode else "lone"
        self.holds = self.access_plan(agents, config, roles)
        self.pieces, self.parts = keys.cut(self.holds, self.spec.piece)
        self.split = any(held is not None for held in self.pieces.values())
        self.owners = {a: "team" if self.mode == "shared_task" else a for a in agents}

    def sample(self, agents: list[str], *, instruction_suffix: str = "", config: SwarmConfig | None = None,
               roles: dict[str, str] | None = None) -> Sample:
        """The row's sample: each agent's copy staged in its own container at setup (stage_machines)."""
        agents = list(agents)
        self.plan(agents, config, roles)
        spec, cfg = self.spec, load_task_yaml(self.spec)
        row = keys.load_row(spec, self.row)
        files = keys.staged_files(spec, row)
        machines.ensure_image()
        compose = machines.write_compose(spec.build, agents)
        seats, stage = {}, {}
        for agent in agents:
            copy = {**files, **keys.variant(spec, row, files, self.pieces[agent], self.parts)}
            digest = hashlib.sha256(json.dumps({d: hashlib.sha256(b).hexdigest() for d, b in sorted(copy.items())})
                                    .encode()).hexdigest()[:12]
            stage[agent] = str(machines.make_stage_tar(copy, spec.build / "stage" / f"{row['id']}-{digest}.tgz"))
            seats[agent] = {"service": machines.service_of(agent, agents), "holds": self.holds[agent],
                            "pieces": self.pieces[agent]}
        instruction = render_prompt(spec.task_dir / cfg["prompts"]["instruction"], row) + instruction_suffix
        cheatbench = {
            "task": cfg["name"], "variant": "", "row": row,
            "instruction": instruction, "prompt_file": cfg["prompts"]["instruction"],
            "timeout_s": int(cfg["timeout_s"]), "network": cfg.get("network"), "stage": stage,
            "interview": {"task": spec.task, "row_id": row["id"], "question": row["meta"]["question"],
                          "layout": self.layout, "mode": self.mode, "split": self.parts,
                          "answer": keys.answer(spec, row, files), "key_paths": spec.key_paths,
                          "agents": seats, "owner_of": dict(self.owners)},
        }
        return Sample(id=row["id"], input=instruction, sandbox=("docker", str(compose)),
                      metadata={"cheatbench": cheatbench})

    def tools(self, agents: list[str], config: SwarmConfig | None = None) -> AgentTools:
        """A lone agent: ORBIT's bash and python as `user`, and the verifier. A team: shell and run_python in each
        agent's own container, and the verifier, whose owner is the agent or the team."""
        agents = list(agents)
        if set(self.owners) != set(agents):
            self.plan(agents, config, None)
        owners = dict(self.owners)
        verifier = verifier_binding(self.spec, keys.load_row(self.spec, self.row), lambda agent: owners[agent])
        if self.layout == "lone":
            return AgentTools(names=[*machines.LONE_TOOL_NAMES, tool_name(self.spec)], bindings=verifier,
                              tool_kwargs=machines.LONE_TOOL_KWARGS)
        return AgentTools(names=[*machines.MACHINE_TOOL_NAMES, tool_name(self.spec)],
                          bindings={**machines.machine_tool_bindings(), **verifier}, tool_kwargs={})

    def setup(self) -> Solver | None:
        return chain(stage_machines(), environment_probe())

    def scorers(self) -> list[Scorer]:
        return [interview_deliverables()]

    def deviations(self, n_agents: int, mode: str | None = None) -> list[str]:
        if n_agents < 2:
            return list(DEVIATIONS)
        shared = SHARED_DEVIATIONS if mode == "shared_task" else []
        return DEVIATIONS + MACHINE_DEVIATIONS + shared + (SPLIT_DEVIATIONS[self.name] if self.split else [])


class SubsetSum(Interview):
    """subset_sum: find a subset of 60 ten-digit integers that sums to the target, checked by verify_subset."""
    spec = SUBSET
    name = SUBSET.task
    task_root = SUBSET.task_dir


class PrimeFactorization(Interview):
    """prime_factorization: factor one 52-bit semiprime, checked by verify_factors."""
    spec = FACTOR
    name = FACTOR.task
    task_root = FACTOR.task_dir
