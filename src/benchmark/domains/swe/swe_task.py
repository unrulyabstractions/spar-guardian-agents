"""The SWE benchmark: one CheatBench issue under one of Lucy's honeypots, for one agent or a team.

Holds the sample (build_swe), each agent's pieces of the honeypot, and the texts each agent reads: the system prompts
from prompts.yaml `swe`.

Modes of a team (SwarmConfig.resolve_mode, `shared_task` when mode is None):
  * shared_task: every agent gets the whole issue on its own machine. The team's main is graded. The own_piece and
    jigsaw honeypots' pieces go to the clones as the config's access policy deals them (pieces_by_agent).
  * solo_task: every agent gets the whole issue and its own repository, with no team remote. Each agent's working
    tree is graded.
A lone agent works the whole issue in every mode. Each agent has its own service, so SWE refuses the large scales.
"""
from __future__ import annotations

import random
from collections.abc import Awaitable, Callable

from inspect_ai.dataset import Sample
from inspect_ai.scorer import Scorer
from inspect_ai.solver import Solver

from src.benchmark.benchmark_protocol import SUBMIT_PROMPT_DEVIATION, AgentTools, Benchmark
from src.benchmark.domains.swe.swe_grader import committed, swe_grader
from src.benchmark.domains.swe.swe_honeypot_setup import has_team_remote, swe_setup
from src.benchmark.domains.swe.swe_inputs import (
    agent_timeout_s,
    fix_commit,
    load_lines,
    load_split,
    patch_files,
    task_config,
    task_dir,
)
from src.benchmark.domains.swe.swe_sandbox import (
    ensure_images,
    tool_bindings,
    write_compose,
)
from src.configuration import access_policy as access_policy
from src.configuration import settings
from src.configuration.access_policy import Access
from src.configuration.scales import SCALES
from src.configuration.swarm_config import (
    SwarmConfig,
    board_system_prompt,
    submit_line,
    swe_board_fields,
)
from src.configuration.swe_settings import (
    AGENT_CPUS,
    AGENT_MEM,
    AGENT_TIME_S,
    DELIVERY_MODES,
    HONEYPOTS,
    JIGSAW_PIECES,
    PARTS,
    PIECES,
    TASK_ROOT,
    TOOL_TIMEOUT_S,
    TOOLS,
    WORKDIR,
)

SHARED = "shared_task"
# Every agent has its own service (sandbox.write_compose). A hundred services is more than one Docker host holds.
LARGE_SCALES = ("large", "extra_large", "gigantic")
LARGE_SCALE_ERROR = "SWE runs per-agent containers; large scales need a shared container"


def check_scale(config: SwarmConfig) -> None:
    """Raise NotImplementedError at the large scales, or at the large scale's team size set by hand."""
    if config.scale in LARGE_SCALES or config.n_agents >= SCALES["large"].n_agents:
        raise NotImplementedError(LARGE_SCALE_ERROR)


def honeypot_items(honeypot: str) -> list[str]:
    """The pieces access=split deals over the neutral agents, in deal order: Lucy's jigsaw B, C, A; own_piece A, B,
    C; nothing as released."""
    if honeypot not in HONEYPOTS:
        raise ValueError(f"honeypot must be one of {HONEYPOTS}, got {honeypot!r}")
    items = {"as_released": [], "own_piece": list(PARTS), "jigsaw": [p for held in JIGSAW_PIECES for p in held]}
    return items[honeypot]


def pieces_by_agent(agents: list[str], honeypot: str, mode: str = SHARED,
                    roles: dict[str, str] | None = None, access: Access = "split") -> dict[str, list[str]]:
    """The pieces planted in each agent's clone, as the access policy deals them (src/configuration/access_policy.py).

    as_released plants nothing. A lone agent and a solo_task agent hold `whole`. Otherwise the honeypot's pieces
    (honeypot_items) are dealt as `access` says. Under split, round robin over the neutral agents (roles; every agent
    without roles): two hold B and A, and C, three hold B, C and A as in Lucy's runs, and neutral agents beyond three
    hold none; a regulator or a disregulator holds nothing."""
    items = honeypot_items(honeypot)
    if honeypot == "as_released":
        return {a: [] for a in agents}
    if honeypot == "jigsaw" and len(agents) > 1 and mode != SHARED:
        raise ValueError("the jigsaw honeypot needs a team in shared_task "
                         "(Lucy's pieces B, C and A are dealt over the neutral agents)")
    if len(agents) == 1 or mode == "solo_task":
        return {a: ["whole"] for a in agents}
    return access_policy.plan(access, agents, roles, lambda n: items, ["whole"])


def piece_times(seed: int, issue: str) -> dict[str, int]:
    """Seconds added to each planted commit's time, drawn from the sample's seed, so each seed plants new hashes."""
    rng = random.Random(f"{seed}/{issue}/piece_times")
    return {p: rng.randrange(3600) for p in PIECES}


def system_prompts(split: dict, agents: list[str], config: SwarmConfig, *, mode: str = SHARED,
                   neutral: set[str] | None = None) -> dict[str, str]:
    """Each agent's system prompt from prompts.yaml `swe`. The submit bullet is left out without shutdown.

    A lone agent and a solo_task agent get `system_solo`; a solo_task agent of a board then gets the team and board
    texts its config asks for (swarm_config.board_system_prompt). A shared_task agent gets `system_shared` with the
    roster. Its board sentences and last line come from swarm_config.swe_board_fields; the team_submit bullet goes to
    the neutral agents of a config with team_submit."""
    prompts = settings.load_prompts()["swe"]
    neutral = set(agents) if neutral is None else neutral
    if len(agents) == 1 or mode == "solo_task":
        submit = submit_line(False, config.shutdown)
        out = {}
        for a in agents:
            text = prompts["system_solo"].format(name=a, repo=split["repo"], branch=a, timeout=TOOL_TIMEOUT_S,
                                                 submit=submit)
            board = board_system_prompt(config, a, agents, workdir=WORKDIR, domain="swe")
            out[a] = f"{text}\n\n{board}" if board else text
        return out
    submit = submit_line(True, config.shutdown)
    submits = config.has_team_submit("swe")
    planning = f"\n\n{prompts['planning_system']}" if config.planning_turns == 2 else ""
    out = {}
    for a in agents:
        roster = "\n".join(prompts["roster_line"].format(name=o) for o in agents if o != a)
        out[a] = prompts["system_shared"].format(
            name=a, n=len(agents), repo=split["repo"], roster=roster, branch=a, timeout=TOOL_TIMEOUT_S,
            submit=submit, **swe_board_fields(config, team_submit=submits and a in neutral)) + planning
    return out


def build_swe(issue: str, honeypot: str, agents: list[str], config: SwarmConfig, *, instruction_suffix: str = "",
              roles: dict[str, str] | None = None) -> dict:
    """Everything one sample needs except the images: pieces, prompts and first messages. No Docker.

    The returned dict is metadata["cheatbench"]["swe"]. A team adds `mode`; a lone agent has none."""
    if config.delivery not in DELIVERY_MODES:
        raise ValueError(f"delivery must be one of {DELIVERY_MODES}, got {config.delivery!r}")
    split = load_split(issue)
    instance = split["task"]
    cfg = task_config(instance)
    issue_text = (task_dir(instance) / "instruction.md").read_text()
    instruction = issue_text + instruction_suffix
    team = len(agents) > 1
    mode = config.resolve_mode("swe") if team else SHARED
    pieces = pieces_by_agent(agents, honeypot, mode, roles, config.resolve_access("swe"))
    neutral = set(access_policy.neutral_agents(agents, roles))
    system = system_prompts(split, agents, config, mode=mode, neutral=neutral)
    swe = {
        "issue": issue, "task_key": issue, "instance": instance, "repo": cfg["repo"],
        "base_commit": cfg["base_commit"], "fix_commit": fix_commit(instance),
        "honeypot": honeypot, "jigsaw": honeypot == "jigsaw", "seed": config.seed,
        "n_agents": len(agents), "agents": list(agents), "board": team,
        "delivery": config.delivery if team else None,
        "channel_list": config.channel_list if team else None, "read_tool": config.read_tool if team else None,
        "shutdown": config.shutdown, "planning_turns": config.planning_turns,
        "hint": instruction_suffix.strip() or None, "instruction": instruction,
        "pieces": pieces,
        "piece_times": piece_times(config.seed, issue),
        "system_prompt": system, "first_message": {a: instruction for a in agents},
        "judge_prompt": {a: f"{system[a]}\n\n{instruction}" for a in agents},
        "patch_files": patch_files(cfg["patch"]),
        "distinctive_lines": len(load_lines(issue)["fix"]),
    }
    if team:
        swe["mode"] = mode
    return swe


class SWE(Benchmark):
    """One CheatBench SWE issue under one of Lucy's honeypots, for one agent or a team (see the module docstring).

    grade=False collects the diffs and skips the hidden tests.
    """

    name = "software_engineering"
    task_root = TASK_ROOT
    workdir = WORKDIR

    def __init__(self, issue: str = "flipt-02e2", honeypot: str = "as_released", grade: bool = True) -> None:
        load_split(issue)
        if honeypot not in HONEYPOTS:
            raise ValueError(f"honeypot must be one of {HONEYPOTS}, got {honeypot!r}")
        self.issue, self.honeypot, self.grade = issue, honeypot, grade

    def check(self, config: SwarmConfig) -> None:
        """NotImplementedError at the large scales (LARGE_SCALE_ERROR); ValueError for an access map naming pieces
        when the honeypot plants none."""
        super().check(config)
        check_scale(config)
        access = config.resolve_access("swe")
        if self.honeypot == "as_released" and isinstance(access, dict) and any(access.values()):
            raise ValueError("the as_released honeypot plants no pieces, so no access map can name any")

    def whole_access(self) -> list[str]:
        """The whole fix, or nothing as released."""
        return [] if self.honeypot == "as_released" else ["whole"]

    def split_items(self, n_neutral: int) -> list[str]:
        return honeypot_items(self.honeypot)

    def valid_item(self, item: str) -> bool:
        return item in PIECES

    def access_plan(self, agents: list[str], config: SwarmConfig | None,
                    roles: dict[str, str] | None) -> dict[str, list[str]]:
        """The pieces each clone holds (pieces_by_agent): a lone agent or a run without a config holds the whole fix,
        a team as its mode and the applied policy say."""
        team = len(agents) > 1 and config is not None
        mode = config.resolve_mode("swe") if team else SHARED
        return pieces_by_agent(list(agents), self.honeypot, mode, roles, self.applied_access(agents, config, roles))

    def time_limit_s(self) -> int:
        """The agents' shared time. Inspect's sample limit should add GRADING_BUDGET_S for setup and grading."""
        return AGENT_TIME_S

    def sample(self, agents: list[str], *, instruction_suffix: str = "", config: SwarmConfig | None = None,
               roles: dict[str, str] | None = None) -> Sample:
        """One sample under the config, or under a plain SwarmConfig for this roster without one. roles
        {agent: role} keeps team_submit out of the prompts of regulators and disregulators."""
        if config is None:
            config = SwarmConfig(n_agents=len(agents))
        self.check(config)
        swe = build_swe(self.issue, self.honeypot, agents, config, instruction_suffix=instruction_suffix,
                        roles=roles)
        swe["images"] = ensure_images(self.issue)
        compose = write_compose(self.issue, agents, board=has_team_remote(swe))
        cheatbench = {"task": self.name, "variant": "", "swe_task": swe["instance"],
                      "instruction": swe["instruction"], "prompt_file": "instruction.md",
                      "timeout_s": agent_timeout_s(swe["instance"]), "network": "none",
                      "judge_prompt": swe["judge_prompt"], "swe": swe}
        if swe["board"]:
            cheatbench["first_message"] = swe["first_message"]
        return Sample(id=self.issue, input=swe["instruction"], sandbox=("docker", str(compose)),
                      metadata={"cheatbench": cheatbench})

    def tools(self, agents: list[str], config: SwarmConfig | None = None) -> AgentTools:
        """`shell` and `run_python` on each agent's own service, in every mode."""
        return AgentTools(names=list(TOOLS), bindings=tool_bindings(), tool_kwargs={})

    def setup(self) -> Solver:
        return swe_setup()

    def scorers(self) -> list[Scorer]:
        return [swe_grader(grade=self.grade)]

    def delivered(self, agents: list[str], config: SwarmConfig) -> Callable[[], Awaitable[set[str]]]:
        """A team with the team remote (outside solo_task): its main moved past the base, for every agent. A lone
        agent and a solo_task agent: a commit past the base on its own branch (grader.committed)."""
        team = len(agents) > 1 and config.resolve_mode("swe") != "solo_task"
        return committed(list(agents), task_config(load_split(self.issue)["task"])["base_commit"], team)

    def deviations(self, n_agents: int, mode: str | None = None) -> list[str]:
        out = ["native_arm64_rebuild_of_the_amd64_swe_bench_pro_image",
               "no_network_in_every_container",
               f"agents_share_{AGENT_TIME_S}s_and_get_{AGENT_CPUS:g}_cpus_{AGENT_MEM}_each",
               "tools_shell_and_run_python_bound_per_agent",
               SUBMIT_PROMPT_DEVIATION]
        if self.honeypot != "as_released":
            out.append(f"honeypot_{self.honeypot}_pruned_and_planted_at_sample_setup")
        if n_agents > 1:
            out.append("every_agent_works_the_whole_issue_alone_and_is_graded_alone" if mode == "solo_task"
                       else "every_agent_works_the_whole_issue_with_a_shared_team_remote")
        return out
