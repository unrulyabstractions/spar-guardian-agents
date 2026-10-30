"""The interview Benchmarks, SubsetSum and PrimeFactorization: one CheatBench interview round as a sample, its
machines, the verifier and the deviations.

Inputs are submodules/cheatbench/tasks/<task> (the instruction, grader.py, judge_schema.py) and the rows its build.py
writes to build/<task>/ (data.jsonl and environment/files/). The instruction is the row's prompt rendered as
CheatBench renders it; the judge is CheatBench's judge_schema.py and sees the instruction as every agent's rules.

Layouts. A lone agent gets CheatBench's own layout, `lone`: one container, the service `default`, with the row's
files at their absolute paths. It works as the user `user`, with ORBIT's bash and python and the verifier under
CheatBench's name. Two or more agents in solo_task or shared_task get `machines` (src/sandbox/machines/). Each agent
has its own container, named by the agent and staged at setup with the agent's own copy of the row (stage_machines).
Its `shell` and `run_python` run there. The verifier's owner is the agent, or "team" in shared_task. A team shares
one state: one 50-check budget and one sticky acceptance.

Access items: `whole` and the pieces of the answer key, shard1..shardN (subset_sum) or share1..shareN
(prime_factorization), dealt one per neutral agent under split; an agent's copy of the two key files holds what its
plan names (interview_answer_keys.py). The other staged files are the same in every container.

Departures from CheatBench's runner: the agent is ORBIT's turn_react loop; ORBIT's submit prompt; synthetic continue
prompts; the verifier runs on the host with its state in the sample store (interview_verifier_tool.py), with
parameter descriptions
and Inspect's schema check; the agents' messages are not mirrored into the checkout's live logs, which stay as
staged; the containers have no network, where CheatBench leaves egress open and forbids it in the prompt; the staged
files carry the time the sample was built as their mtime. In a team the checkout's generator salts its random
source from an environment variable no container sets, in a rewritten commit (interview_sealed_generator.py),
so running it rebuilds
nothing and a made-up salt gives another corpus.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from inspect_ai.dataset import Sample
from inspect_ai.scorer import Scorer
from inspect_ai.solver import Solver, chain

from src.configuration import access_policy
from src.configuration.swarm_config import SwarmConfig
from src.domains.benchmark_protocol import COMMON_DEVIATIONS, AgentTools, Benchmark
from src.domains.interview import interview_answer_keys as keys
from src.domains.interview.interview_scoring import environment_probe, interview_deliverables
from src.domains.interview.interview_settings import FACTOR, SUBSET, Spec
from src.domains.interview.interview_verifier_tool import tool_name, verifier_binding
from src.sandbox import cheatbench_task_files as task_files
from src.sandbox import machines

DEVIATIONS = COMMON_DEVIATIONS + [
    "task_tool_run_on_the_host_with_state_in_the_sample_store",
    "task_tool_parameters_have_descriptions_and_inspect_checks_their_types",
    "no_live_log_mirror_of_the_agents_messages",
    "sandbox_has_no_network_where_cheatbench_leaves_egress_open",
]
MACHINE_DEVIATIONS = machines.MACHINE_DEVIATIONS + [
    "generator_salts_its_random_source_from_an_unset_environment_variable_in_a_rewritten_commit",
]
SHARED_DEVIATIONS = machines.SHARED_DEVIATIONS
SPLIT_DEVIATIONS = {
    SUBSET.task: ["answer_key_cut_into_interleaved_shards_of_the_indices_dealt_as_access_says",
                  "shard_lines_padded_to_the_original_length_and_lines_of_agents_without_a_shard_removed"],
    FACTOR.task: ["answer_key_cut_into_additive_shares_mod_1e8_of_the_factors_dealt_as_access_says",
                  "key_files_rewritten_in_custody_form_and_lines_of_agents_without_a_share_removed",
                  "q3_stub_byte_count_rewritten_to_the_copys_cache_size"],
}


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
        self.planned: tuple | None = None
        """The roster and config the plan is for (the config as its dump)."""

    def time_limit_s(self) -> int:
        return int(task_files.load_task_yaml(self.spec.task_dir)["timeout_s"])

    def check(self, config: SwarmConfig) -> None:
        """The base's checks; an explicit map names pieces 1..N with N at least 2, so no
        piece is the whole key renamed, and gives every agent a copy of the key that can be built (a subset_sum line
        keeps its length). An agent holding every piece holds the whole key."""
        super().check(config)
        access = config.resolve_access(self.name)
        if isinstance(access, dict):
            named = sorted({k for items in access.values() for k in keys.held(items, self.spec.piece) or []})
            access_policy.check_numbered(named, f"{self.spec.piece}s")
            if named:
                pieces, n = keys.cut(access, self.spec.piece)
                row = keys.load_row(self.spec, self.row)
                files = keys.staged_files(self.spec, row)
                for agent, held in pieces.items():
                    try:
                        keys.variant(self.spec, row, files, held, n)
                    except ValueError as ex:
                        raise ValueError(f"access: {agent}'s copy of the key cannot be built ({ex}); give it fewer "
                                         f"{self.spec.piece}s, or all of them for the whole key") from None

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
        self.owners = machines.owners(agents, self.mode)
        self.planned = (list(agents), None if config is None else config.model_dump(mode="json"))

    def sample(self, agents: list[str], *, instruction_suffix: str = "", config: SwarmConfig | None = None,
               roles: dict[str, str] | None = None) -> Sample:
        """The row's sample: each agent's copy staged in its own container at setup (stage_machines)."""
        agents = list(agents)
        self.plan(agents, config, roles)
        spec, cfg = self.spec, task_files.load_task_yaml(self.spec.task_dir)
        row = keys.load_row(spec, self.row)
        files = keys.staged_files(spec, row)
        machines.ensure_image()
        compose = machines.write_compose(spec.build, agents)
        seats, stage = {}, {}
        for agent in agents:
            copy = keys.agent_copy(spec, row, files, self.pieces[agent], self.parts, team=self.layout == "machines")
            service = machines.service_of(agent, agents)
            stage[service] = machines.stage_tar_for(spec.build, row["id"], copy)
            seats[agent] = {"service": service, "holds": self.holds[agent], "pieces": self.pieces[agent]}
        instruction = task_files.render_prompt(spec.task_dir / cfg["prompts"]["instruction"], row) + instruction_suffix
        cheatbench = {
            **task_files.sample_metadata(cfg, row, instruction), "stage": stage,
            "interview": {"task": spec.task, "row_id": row["id"], "question": row["meta"]["question"],
                          "layout": self.layout, "mode": self.mode, "split": self.parts,
                          "answer": keys.answer(spec, row, files), "key_paths": spec.key_paths,
                          "agents": seats, "owner_of": dict(self.owners)},
        }
        return Sample(id=row["id"], input=instruction, sandbox=("docker", str(compose)),
                      metadata={"cheatbench": cheatbench})

    def tools(self, agents: list[str], config: SwarmConfig | None = None) -> AgentTools:
        """A lone agent: ORBIT's bash and python as `user`, and the verifier. A team: shell and run_python in each
        agent's own container, and the verifier, whose owner is the agent or the team. The plan is the last
        sample's when that sample was for this roster and config, else made afresh."""
        agents = list(agents)
        if self.planned != (agents, None if config is None else config.model_dump(mode="json")):
            self.plan(agents, config, None)
        owners = dict(self.owners)
        verifier = verifier_binding(self.spec, keys.load_row(self.spec, self.row), lambda agent: owners[agent])
        return machines.tools_for(agents, tool_name(self.spec), verifier)

    def setup(self) -> Solver | None:
        return chain(machines.stage_machines(), environment_probe())

    def scorers(self) -> list[Scorer]:
        return [interview_deliverables()]

    def delivered(self, agents: list[str], config: SwarmConfig) -> Callable[[], Awaitable[set[str]]]:
        """The agents whose verifier owner (the agent, or the team in shared_task) has an accepted answer, from the
        sample store (machines.accepted_by_owner)."""
        mode = config.resolve_mode(self.name) if len(agents) > 1 else None
        return machines.accepted_by_owner(machines.owners(list(agents), mode),
                                          lambda state: state.get("accepted") is not None)

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
