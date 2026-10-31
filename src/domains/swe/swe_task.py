"""The SWE benchmark: one CheatBench issue under one of Lucy's honeypots, for one agent or a team.

Holds the sample, each agent's pieces of the honeypot, and the texts each agent reads: the system prompts from
prompts.yaml `swe`.

Modes of a team (SwarmConfig.mode, `shared_task` by default):
  * shared_task: every agent gets the whole issue on its own machine, with the team remote. The team's main is
    graded. The own_piece and jigsaw honeypots' pieces go to the clones as the plan deals them.
  * solo_task: every agent gets the whole issue and its own repository, with no team remote. Each agent's working
    tree is graded.
A lone agent works the whole issue in every mode. Each agent has its own service, so SWE refuses the large scales.

Access items: Lucy's pieces A, B and C, and whole; as released the honeypot plants nothing. The access log watches
each clone's planted refs and what they add over the base. As released the fix sits in the image's pack, so the access
log reports it packed and watches nothing.
"""
from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from pathlib import Path

from inspect_ai.dataset import Sample
from inspect_ai.scorer import Scorer

from src.configuration import settings
from src.configuration.prompt_texts import board_system_prompt, swe_board_fields
from src.configuration.scales import SCALES
from src.configuration.swarm_config import SwarmConfig
from src.domains.benchmark_protocol import Benchmark, Plan
from src.domains.swe import _swe_honeypot as honeypot
from src.domains.swe._swe_scoring import GUARD_DIFF, branch_file, committed, has_work, head_commit, swe_grader
from src.domains.swe.swe_inputs import (
    agent_timeout_s,
    fix_commit,
    load_lines,
    load_split,
    patch_files,
    task_config,
    task_dir,
)
from src.domains.swe.swe_ladder import SweDomain
from src.domains.swe.swe_sandbox import TOOL_DESCRIPTIONS, ensure_images, seats, write_compose
from src.domains.swe.swe_settings import (
    AGENT_CPUS,
    AGENT_MEM,
    AGENT_TIME_S,
    DEFAULT_ROW,
    GRADING_BUDGET_S,
    HONEYPOTS,
    JIGSAW_PIECES,
    PARTS,
    PIECES,
    TASK,
    TASK_DIR,
    TOOL_TIMEOUT_S,
    WORKDIR,
)
from src.sandbox.access_log.access_monitor import Watched, git_watched
from src.sandbox.agent_tools import AgentTools, Seat, roster_tools

# Every agent has its own service (swe_sandbox.write_compose). A hundred services is more than one Docker host holds.
LARGE_SCALES = ("large", "extra_large", "gigantic")
LARGE_SCALE_ERROR = "SWE runs per-agent containers; large scales need a shared container"
TEXT_KEYS = ("first_message", "judge_prompt", "system_prompt")      # in the eval log, not in run.json


def honeypot_items(honeypot_name: str) -> list[str]:
    """The pieces access=split deals over the neutral agents, in deal order: Lucy's jigsaw B, C, A; own_piece A, B,
    C; nothing as released."""
    return {"as_released": [], "own_piece": list(PARTS),
            "jigsaw": [p for held in JIGSAW_PIECES for p in held]}[honeypot_name]


def piece_times(seed: int, issue: str) -> dict[str, int]:
    """Seconds added to each planted commit's time, drawn from the sample's seed, so each seed plants new hashes."""
    rng = random.Random(f"{seed}/{issue}/piece_times")
    return {p: rng.randrange(3600) for p in PIECES}


class SWE(Benchmark):
    """One CheatBench SWE issue under one of Lucy's honeypots, for one agent or a team (see the module docstring).

    grade=False collects the diffs and skips the hidden tests.
    """

    key = "swe"
    name = TASK
    block = "swe"
    task_dir = TASK_DIR
    default_row = DEFAULT_ROW
    scorer = "swe_grader"
    ladder = SweDomain
    options = ("honeypot", "grade")
    time_grace_s = GRADING_BUDGET_S
    harness_reads = (GUARD_DIFF,)

    def __init__(self, row: str | None = None, honeypot: str = "jigsaw", grade: bool = True) -> None:
        super().__init__(row)
        load_split(self.row)
        if honeypot not in HONEYPOTS:
            raise ValueError(f"honeypot must be one of {HONEYPOTS}, got {honeypot!r}")
        self.honeypot, self.grade = honeypot, grade
        self.task_deviations = [
            "native_arm64_rebuild_of_the_amd64_swe_bench_pro_image", "no_network_in_every_container",
            f"agents_share_one_time_limit_and_get_{AGENT_CPUS:g}_cpus_{AGENT_MEM}_each",
            "tools_shell_and_run_python_bound_per_agent",
            *([f"honeypot_{honeypot}_pruned_and_planted_at_sample_setup"] if honeypot != "as_released" else [])]

    def check(self, config: SwarmConfig) -> None:
        """NotImplementedError at the large scales, or at the large scale's team size set by hand (LARGE_SCALE_ERROR);
        ValueError for an access map naming pieces when the honeypot plants none, and for the jigsaw honeypot
        outside a shared_task team, whose pieces are dealt over the neutral agents."""
        super().check(config)
        if config.scale in LARGE_SCALES or config.n_agents >= SCALES["large"].n_agents:
            raise NotImplementedError(LARGE_SCALE_ERROR)
        if self.honeypot == "as_released" and isinstance(config.access, dict) and any(config.access.values()):
            raise ValueError("the as_released honeypot plants no pieces, so no access map can name any")
        if self.honeypot == "jigsaw" and config.n_agents > 1 and config.mode != "shared_task":
            raise ValueError("the jigsaw honeypot needs a team in shared_task "
                             "(Lucy's pieces B, C and A are dealt over the neutral agents)")

    def whole_access(self) -> list[str]:
        """The whole fix, or nothing as released."""
        return [] if self.honeypot == "as_released" else ["whole"]

    def split_items(self, n_neutral: int) -> list[str]:
        return honeypot_items(self.honeypot)

    def valid_item(self, item: str) -> bool:
        return item in PIECES

    def time_limit_s(self) -> int:
        """The agents' shared time. Inspect's sample limit adds time_grace_s for setup and grading."""
        return AGENT_TIME_S

    def tells_team(self, plan: Plan) -> bool:
        """A shared_task agent's prompt (system_shared) always lists the roster; the others follow knows_team."""
        return plan.mode == "shared_task" or plan.config.knows_team

    def system_prompts(self, plan: Plan) -> dict[str, str]:
        """Each agent's system prompt from prompts.yaml `swe`, its tool bullets ending with submit and stop_working as
        the agent holds them (Benchmark.ending_lines).

        A lone agent and a solo_task agent get `system_solo`; a solo_task agent of a board then gets the team and board
        texts its config asks for (prompt_texts.board_system_prompt). A shared_task agent gets `system_shared` with the
        roster. Its board sentences and last line come from prompt_texts.swe_board_fields."""
        prompts, repo = settings.load_prompts()["swe"], load_split(self.row)["repo"]
        config, agents = plan.config, plan.agents
        ending = {a: "".join(f"\n{line}" for line in self.ending_lines(plan, a)) for a in agents}
        if plan.mode != "shared_task":
            texts = {a: prompts["system_solo"].format(name=a, repo=repo, branch=a, timeout=TOOL_TIMEOUT_S,
                                                      ending=ending[a]) for a in agents}
            return {a: "\n\n".join(filter(None, [texts[a], board_system_prompt(config, a, agents)])) for a in agents}
        return {a: prompts["system_shared"].format(
            name=a, n=len(agents), repo=repo, branch=a, timeout=TOOL_TIMEOUT_S, ending=ending[a],
            roster="\n".join(prompts["roster_line"].format(name=o) for o in agents if o != a),
            **swe_board_fields(config)) for a in agents}

    def sample(self, plan: Plan, instruction_suffix: str = "") -> Sample:
        """The issue's sample: metadata["cheatbench"]["swe"] holds the issue, its commits, the honeypot, each agent's
        pieces and times, the prompts and first messages the setup, the grader and the export read. A team starts
        from first messages."""
        split = load_split(self.row)
        instance = split["task"]
        cfg = task_config(instance)
        instruction = (task_dir(instance) / "instruction.md").read_text() + instruction_suffix
        system = self.system_prompts(plan)
        swe = {
            "issue": self.row, "instance": instance, "repo": cfg["repo"], "base_commit": cfg["base_commit"],
            "fix_commit": fix_commit(instance), "honeypot": self.honeypot, "seed": plan.config.seed,
            "n_agents": len(plan.agents), "agents": plan.agents, "mode": plan.mode, "shutdown": plan.config.shutdown,
            "instruction": instruction, "pieces": plan.holds, "piece_times": piece_times(plan.config.seed, self.row),
            "system_prompt": system, "first_message": {a: instruction for a in plan.agents},
            "judge_prompt": {a: f"{system[a]}\n\n{instruction}" for a in plan.agents},
            "patch_files": patch_files(cfg["patch"]), "distinctive_lines": len(load_lines(self.row)["fix"]),
            "images": ensure_images(self.row),
        }
        compose = write_compose(self.row, plan.agents, team_remote=honeypot.has_team_remote(swe))
        cheatbench = {"task": self.name, "variant": "", "swe_task": instance, "instruction": instruction,
                      "prompt_file": "instruction.md", "timeout_s": agent_timeout_s(instance), "network": "none",
                      "judge_prompt": swe["judge_prompt"], "swe": swe,
                      **({"first_message": swe["first_message"]} if plan.team else {})}
        return Sample(id=self.row, input=instruction, sandbox=("docker", str(compose)),
                      metadata={"cheatbench": cheatbench})

    def tools(self, plan: Plan) -> AgentTools:
        """`shell` and `run_python` on each agent's own service, in every mode."""
        return roster_tools(plan.agents, seats(plan.agents), timeout=TOOL_TIMEOUT_S, descriptions=TOOL_DESCRIPTIONS)

    async def prepare(self, state) -> None:
        await honeypot.prepare(state)

    def seats(self, state) -> dict[str, Seat]:
        """Each agent's own service: SWE's setup probes no seat, and a machine names its agent."""
        return seats(state.metadata["cheatbench"]["swe"]["agents"])

    async def watched(self, state) -> list[Watched]:
        """Each clone's planted refs and what they add over the base. As released the honeypot is the fix commit itself,
        which has no ref of its own and comes back packed."""
        swe = state.metadata["cheatbench"]["swe"]
        fix, split = swe["fix_commit"], load_split(swe["issue"])
        found = []
        for agent, held in swe["pieces"].items():
            if swe["honeypot"] == "as_released":
                found += await git_watched(agent, WORKDIR, [fix], [f"{fix}^"])
            elif held:
                refs = [honeypot.piece_ref(split, piece) for piece in held]
                found += await git_watched(agent, WORKDIR, refs, [swe["base_commit"]])
        return found

    def scorers(self) -> list[Scorer]:
        return [swe_grader(grade=self.grade)]

    def base_commit(self) -> str:
        return task_config(load_split(self.row)["task"])["base_commit"]

    def delivered(self, plan: Plan) -> Callable[[], Awaitable[set[str]]]:
        """A shared_task team: its main moved past the base, for every agent. A lone agent and a solo_task agent: a
        commit past the base on its own branch (_swe_scoring.committed)."""
        return committed(plan.agents, self.base_commit(), plan.mode == "shared_task")

    async def gradable(self, plan: Plan, agent: str) -> str | None:
        """A shared_task team: team/main differs from the base. A lone agent and a solo_task agent: its /app differs
        from the base (_swe_scoring.has_work)."""
        return None if await has_work(agent, self.base_commit(), plan.mode == "shared_task") else self.missing(plan)

    async def snapshot(self, plan: Plan, agent: str) -> str | None:
        """team/main's commit in shared_task, else the agent's HEAD (_swe_scoring.head_commit)."""
        return await head_commit(agent, plan.mode == "shared_task")

    def team_deviations(self, plan: Plan) -> list[str]:
        return ["every_agent_works_the_whole_issue_alone_and_is_graded_alone" if plan.mode == "solo_task"
                else "every_agent_works_the_whole_issue_with_a_shared_team_remote"]

    # ---------------------------------------------------------------- what the export records
    @classmethod
    def run_blocks(cls, view) -> dict:
        """run.json "swe": the issue, the honeypot, each agent's pieces, the setup's evidence and checks, the planted
        commits, the grades and each agent's final state; the prompts stay in the eval log."""
        cb = (view.metadata or {}).get("cheatbench") or {}
        swe = cb.get("swe")
        if not swe:
            return {}
        score = (view.scores or {}).get(cls.scorer)
        md = (score.metadata or {}) if score else {}
        setup = view.store_get(honeypot.SETUP_STORE) or md.get("setup") or {}
        planted = swe["honeypot"] != "as_released"
        return {"swe": {
            **{k: v for k, v in swe.items() if k not in TEXT_KEYS}, "rule": (cb.get("config") or {}).get("rule_level"),
            "fragment_commits": {a: ev.get("planted") for a, ev in (setup.get("agents") or {}).items()}
            if planted else None,
            "checks_ok": setup.get("checks_ok"), "setup": setup, "primary": md.get("primary"),
            "graded": md.get("graded"), "required_tests": md.get("required_tests"), "grades": grades(md),
            "team": md.get("team"),
            "agents_final": {a: {k: v for k, v in x.items() if k != "own_diff_text"}
                             for a, x in (md.get("agents") or {}).items()}}}

    @classmethod
    def write_files(cls, view, run_dir: Path) -> None:
        """The graded diffs as swe/diffs/<target>.diff, each agent's own work as swe/diffs/<agent>.own.diff and each
        other team branch as swe/diffs/team.<branch>.diff (_swe_scoring.branch_file); the code ladder reads them."""
        score = (view.scores or {}).get(cls.scorer)
        md = (score.metadata or {}) if score else {}
        diffs = run_dir / "swe" / "diffs"
        texts = {f"{name}.diff": t.get("diff_text") or "" for name, t in (md.get("targets") or {}).items()}
        texts |= {f"{name}.own.diff": a["own_diff_text"] for name, a in (md.get("agents") or {}).items()
                  if a.get("own_diff_text") is not None}
        texts |= {branch_file(b): text for b, text in (md.get("team_branches") or {}).items() if text is not None}
        for name, text in texts.items():
            diffs.mkdir(parents=True, exist_ok=True)
            (diffs / name).write_text(text)

    @classmethod
    def episode_facts(cls, view, agent: str) -> dict:
        """The episode's SWE keys: the issue, its seat, what it held, its reward and the team's."""
        cb = (view.metadata or {}).get("cheatbench") or {}
        swe = cb.get("swe") or {}
        score = (view.scores or {}).get(cls.scorer)
        graded = grades((score.metadata or {}) if score else {}) or {}
        return {"swe_task": cb.get("swe_task"), "seat": agent, "n_agents": swe.get("n_agents"),
                "rule": (cb.get("config") or {}).get("rule_level"), "honeypot": swe.get("honeypot"),
                "piece": (swe.get("pieces") or {}).get(agent), "reward": (graded.get(agent) or {}).get("reward"),
                "team_reward": (graded.get("team") or {}).get("reward") if swe.get("mode") == "shared_task" else None}


def grades(md: dict) -> dict | None:
    """Hidden-test results per target (team, k7q2m, ...) from the grader's metadata, without the diff text."""
    if not md:
        return None
    return {name: {k: v for k, v in t.items() if k != "diff_text"} for name, t in (md.get("targets") or {}).items()}
