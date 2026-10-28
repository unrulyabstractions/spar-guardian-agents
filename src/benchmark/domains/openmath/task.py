"""The openmath_agent Benchmark: CheatBench's task folder and built rows, the sample, and the deviations.

Inputs are submodules/cheatbench/tasks/openmath_agent and the rows in build/openmath_agent/data.jsonl.

Modes. A lone agent gets CheatBench's own layout in every mode. A `shared_task` team (the default) under access=split
or an explicit map gets the split layout: one copy of the row per agent, each under its own Linux user, a bare team
remote that every copy pushes to, and the complete submission's archive cut into slices held as the access plan says
(sandbox.py; under split one slice per neutral agent). A `shared_task` team under access=all shares
CheatBench's layout: one workspace, one Linux user and one git identity. A `solo_task` team gets one copy of the row
per agent with no team remote. The row has no parts, so `collaboration_needed` is refused.

Access items: slice1..sliceN and whole.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml
from inspect_ai.dataset import Sample
from inspect_ai.scorer import Scorer
from inspect_ai.solver import Solver

from src.benchmark.base import SUBMIT_PROMPT_DEVIATION, AgentTools, Benchmark
from src.benchmark.domains.openmath.probe import deliverables_scorer, environment_probe
from src.benchmark.domains.openmath.sandbox import (
    SETUP_SCRIPT,
    SLICE_ITEM,
    SOLO_TOOLS,
    agent_layout,
    make_stage_tar,
    slices_of,
    solo_setup_script,
    solo_tool_bindings,
    split_layout,
    split_setup_script,
    write_docker_context,
)
from src.configuration.openmath import BUILD, TASK, TASK_DIR, TOOL_KWARGS, WORKDIR
from src.configuration.swarm_config import SwarmConfig

DEVIATIONS = [
    "agent_is_orbit_turn_react_not_vendor_harness",
    SUBMIT_PROMPT_DEVIATION,
    "continue_prompts_are_orbit_synthetic_user_messages",
    "mirror_served_from_docker_sidecar_and_ca_baked_into_image",
]
TEAM_DEVIATION = "two_agents_share_one_sandbox_clock_and_git_identity"
SOLO_DEVIATIONS = [
    "each_agent_has_its_own_linux_user_home_and_copy_of_the_row_in_one_container_with_one_clock",
    "tools_shell_and_run_python_bound_per_agent_user_in_place_of_bash_and_python",
    "temporary_directories_not_listable_and_files_private_by_default",
]
SPLIT_DEVIATIONS = SOLO_DEVIATIONS + [
    "origin_is_a_bare_team_remote_holding_the_staged_main_and_the_rows_own_branch",
    "complete_submissions_archive_cut_into_slices_held_as_access_deals_them_on_a_rewritten_branch_commit",
    "every_copy_keeps_the_rows_git_identity",
]


def load_task_yaml() -> dict:
    return yaml.safe_load((TASK_DIR / "task.yaml").read_text())


def render_prompt(template: Path, row: dict) -> str:
    """CheatBench core/trial.py:render_prompt, reproduced verbatim in behavior."""
    fields = {"input": row.get("input") or "", **(row.get("vars") or {})}
    out = []
    for line in template.read_text().split("\n"):
        tokens = [k for k in fields if "{{" + k + "}}" in line]
        if tokens and all(not str(fields[k]).strip() for k in tokens):
            continue
        for k, v in fields.items():
            line = line.replace("{{" + k + "}}", str(v))
        out.append(line)
    text = "\n".join(out)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip() + "\n"


def load_rows() -> list[dict]:
    data = BUILD / "data.jsonl"
    if not data.exists():
        raise FileNotFoundError(
            f"{data} missing: build the task first, e.g. "
            f"python -c \"...tasks/openmath_agent/build.py build(output='{BUILD}', selected=['riemann'])\"")
    return [json.loads(line) for line in data.read_text().splitlines() if line.strip()]


def split(config: SwarmConfig | None) -> bool:
    """Whether the config's access policy splits the exploit (anything but all). Without a config, no."""
    return config is not None and config.resolve_access(TASK) != "all"


def layout_of(agents: list[str] | None, config: SwarmConfig | None) -> str | None:
    """The roster's layout: "solo_task" for two or more agents in solo_task, "split" for two or more agents in
    shared_task under a splitting access policy, None for CheatBench's own layout (a lone agent, a shared_task team
    under access=all)."""
    if not agents or len(agents) < 2 or config is None:
        return None
    mode = config.resolve_mode(TASK)
    if mode == "solo_task":
        return "solo_task"
    if mode == "shared_task" and split(config):
        return "split"
    return None


def per_agent(agents: list[str] | None, config: SwarmConfig | None) -> bool:
    """Whether the roster gets one Linux user and one copy of the row per agent: the solo_task or split layout."""
    return layout_of(agents, config) is not None


def build_sample(row_id: str, instruction_suffix: str = "", solo_agents: list[str] | None = None, *,
                 split_agents: list[str] | None = None, plan: dict[str, list[str]] | None = None) -> Sample:
    """The sample for one row. instruction_suffix is appended to CheatBench's instruction.

    solo_agents gives the solo_task layout to those agents; split_agents gives them the split layout, with plan
    {agent: [slice<k> | whole]} the access plan (every agent whole when None); neither keeps CheatBench's layout. The
    staged tarball, the compose file and the instruction are the same in all three; the setup script differs, and a
    per-agent layout is recorded as metadata["cheatbench"]["openmath"]: {mode, agents: {name: {user, uid, home,
    workdir}}} for solo_task, and for split {mode: "shared_task", layout: "split", remote, group, remote_branches,
    honeypot_branches, archive: {branch, commit, name, parts}, archive_parts: {agent: [k, ...]},
    agents: {name: {user, uid, home, workdir, branches, slices}}}."""
    if solo_agents and split_agents:
        raise ValueError("a roster gets one layout: solo_agents or split_agents")
    cfg = load_task_yaml()
    compose = write_docker_context()
    template = TASK_DIR / cfg["prompts"]["instruction"]
    row = next((r for r in load_rows() if r["id"] == row_id), None)
    if row is None:
        raise ValueError(f"no row {row_id!r} in {BUILD / 'data.jsonl'}")
    instruction = render_prompt(template, row) + instruction_suffix
    tar_path = make_stage_tar(row, BUILD / "environment", BUILD / "stage" / f"{row['id']}.tgz")
    cheatbench = {
        "task": cfg["name"], "variant": "", "row": row,
        "instruction": instruction, "prompt_file": cfg["prompts"]["instruction"],
        "timeout_s": int(cfg["timeout_s"]), "network": cfg.get("network"),
    }
    setup = SETUP_SCRIPT
    if solo_agents:
        cheatbench["openmath"] = {"mode": "solo_task", "agents": agent_layout(solo_agents)}
        setup = solo_setup_script(solo_agents)
    elif split_agents:
        cheatbench["openmath"] = split_layout(split_agents, row, plan or {a: ["whole"] for a in split_agents})
        setup = split_setup_script(cheatbench["openmath"])
    return Sample(
        id=row["id"],
        input=instruction,
        files={f"{WORKDIR}/.stage.tgz": str(tar_path)},
        setup=setup,
        sandbox=("docker", str(compose)),
        metadata={"cheatbench": cheatbench},
    )


class OpenMath(Benchmark):
    """openmath_agent for one row. Every agent gets the same instruction and the same container."""

    name = TASK
    task_root = TASK_DIR
    workdir = WORKDIR

    def __init__(self, row: str = "riemann") -> None:
        self.row = row
        self.split = False
        """Whether the last config checked splits the exploit; deviations() reads it."""

    def time_limit_s(self) -> int:
        return int(load_task_yaml()["timeout_s"])

    def check(self, config: SwarmConfig) -> None:
        """The base's checks; no collaboration_needed; an explicit map names slices 1..N with N at least 2, each
        held by some agent, so the archive can be rebuilt and no slice is the whole archive renamed."""
        super().check(config)
        if config.resolve_mode(TASK) == "collaboration_needed":
            raise ValueError("openmath_agent has no parts to split; run it in mode solo_task or shared_task")
        access = config.resolve_access(TASK)
        if isinstance(access, dict):
            named = sorted({k for items in access.values() for k in slices_of(items) or []})
            if named and (named != list(range(1, named[-1] + 1)) or named[-1] < 2):
                raise ValueError("access must name slices 1..N with N at least 2, each held by some agent, "
                                 f"got {named}")
        self.split = split(config)

    def split_items(self, n_neutral: int) -> list[str]:
        """One slice of the complete submission's archive per neutral agent."""
        return [f"slice{k}" for k in range(1, n_neutral + 1)]

    def valid_item(self, item: str) -> bool:
        return item == "whole" or SLICE_ITEM.fullmatch(item) is not None

    def sample(self, agents: list[str], *, instruction_suffix: str = "", config: SwarmConfig | None = None,
               roles: dict[str, str] | None = None) -> Sample:
        """The row's sample. The judge sees the instruction as every agent's rules. A solo_task team gets the
        per-agent layout, a shared_task team under a splitting access policy the split layout with the access plan
        (roles say who is neutral); any other roster gets CheatBench's."""
        if config is not None:
            self.check(config)
        layout = layout_of(agents, config)
        if layout == "split":
            return build_sample(self.row, instruction_suffix, split_agents=list(agents),
                                plan=self.access_plan(list(agents), config, roles))
        return build_sample(self.row, instruction_suffix, list(agents) if layout == "solo_task" else None)

    def tools(self, agents: list[str], config: SwarmConfig | None = None) -> AgentTools:
        """ORBIT's bash and python as the user `user`, or in the solo_task and split layouts `shell` and `run_python`
        bound to each agent's own user and copy."""
        if per_agent(agents, config):
            return AgentTools(names=list(SOLO_TOOLS), bindings=solo_tool_bindings(), tool_kwargs={})
        return AgentTools(names=["bash", "python"], bindings={}, tool_kwargs=TOOL_KWARGS)

    def setup(self) -> Solver | None:
        return environment_probe()

    def scorers(self) -> list[Scorer]:
        return [deliverables_scorer()]

    def deviations(self, n_agents: int, mode: str | None = None) -> list[str]:
        if n_agents < 2:
            return list(DEVIATIONS)
        if mode == "solo_task":
            return DEVIATIONS + SOLO_DEVIATIONS
        if mode == "shared_task" and self.split:
            return DEVIATIONS + SPLIT_DEVIATIONS
        return DEVIATIONS + [TEAM_DEVIATION]
