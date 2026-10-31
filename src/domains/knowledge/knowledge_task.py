"""The knowledge_work Benchmark: one GDPval assignment from CheatBench's task, its sample, tools and deviations.

Inputs are submodules/cheatbench/tasks/knowledge_work (the instruction, the README template, grader.py,
judge_schema.py) and the rows build.py writes to build/knowledge_work (data.jsonl and environment/files/<row>/).

The agent works in a contract directory that /external/contract_<id>/ points at, holding README.md (the template with
that path filled in), brief.md, inputs/ and to_send/. The current pipeline's deliverable sits at
/data/cache/artifacts/<name>, named by /data/cache/evaluation.json and by every successful review log. The
instruction is prompts/instruction.md, unchanged. The judge is CheatBench's judge_schema.py.

Modes. A lone agent gets CheatBench's layout in every mode. A `shared_task` team (the default) shares one contract
directory and one candidate id, each agent under its own Linux user, and holds the access items `review` (the review
tool, the review logs and evaluation.json) and `read` (the pipeline artifact) as the plan deals them
(knowledge_sandbox.py). A `solo_task` team gives each agent its own contract copy and candidate id in the one
container. Each agent's tools follow what it holds: review_deliverable goes to the holders of `review`. The access log
watches the artifact and evaluation.json, which names it.

The access note. When the plan is split, every seat's system prompt ends with the same note on who holds the review
tool and who holds read access (access_note), in agent IDs and in the Linux group a seat can look up. It names nothing
the access reaches. A seat that holds nothing, such as a regulator, reads the note and stands in neither list.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable

from inspect_ai.scorer import Scorer

from src.configuration.settings import load_prompts
from src.domains.benchmark_protocol import Plan, RowBenchmark, Staged
from src.domains.knowledge import _knowledge_scoring as scoring
from src.domains.knowledge import knowledge_sandbox as sandbox
from src.domains.knowledge._knowledge_ladder import KnowledgeDomain
from src.domains.knowledge._knowledge_review_tool import TOOL as REVIEW_TOOL
from src.domains.knowledge._knowledge_review_tool import review_binding, reviewed
from src.domains.knowledge.knowledge_settings import (
    BUILD,
    CACHE,
    DEFAULT_ROW,
    PIPELINE_GROUP,
    STAGE_TAR,
    TASK,
    TASK_DIR,
)
from src.sandbox.access_log.access_monitor import Watched
from src.sandbox.agent_tools import AgentTools, Seat, roster_tools
from src.sandbox.agent_users import agent_users as users
from src.sandbox.machines import agent_machines as machines

DEVIATIONS = [
    "review_deliverable_is_an_inspect_tool_running_cheatbench_grader_as_root_in_the_sandbox_not_an_mcp_server",
    "review_errors_return_the_log_path_as_an_inspect_tool_error_in_place_of_mcp_iserror",
    "reference_rendered_on_each_review_from_a_root_only_copy_taken_at_setup",
    "review_deliverable_parameter_has_a_description_because_inspect_requires_one",
]
TEAM_DEVIATIONS = [*users.USER_DEVIATIONS, "reviews_of_different_agents_are_not_serialized"]
SHARED_DEVIATIONS = ["team_shares_one_contract_directory_and_candidate_id_through_the_group_team"]
SPLIT_DEVIATIONS = [
    "review_tool_and_artifact_read_dealt_over_the_agents_as_access_says_through_the_groups_review_and_pipeline",
    "review_logs_and_evaluation_json_readable_by_the_group_review_only_and_the_artifacts_directory_traverse_only",
    "pipeline_artifact_staged_under_a_random_name_so_a_reader_cannot_guess_it",
]
ACCESS_NOTE_DEVIATION = "system_prompt_states_who_holds_the_review_tool_and_who_holds_read_access"
SOLO_DEVIATIONS = [users.PRIVATE_TEMP_DEVIATION, "each_agent_has_its_own_contract_copy_and_candidate_id",
                   "evaluation_json_lists_every_candidate_under_candidate"]


class KnowledgeWork(RowBenchmark):
    """knowledge_work for one GDPval row. Every agent gets the same instruction and the same container."""

    key = name = TASK
    block = "knowledge"
    task_dir = TASK_DIR
    build_dir = BUILD
    build_hint = (f" (downloads GDPval from Hugging Face): uv run python -B -c \"import sys; "
                  f"sys.path.insert(0, '{TASK_DIR}'); import build; build.build(output='{BUILD}')\"")
    default_row = DEFAULT_ROW
    scorer = scoring.SCORER
    ladder = KnowledgeDomain
    task_deviations = DEVIATIONS

    def whole_access(self) -> list[str]:
        return list(sandbox.ITEMS)

    def split_items(self, n_neutral: int) -> list[str]:
        """`review` for the first half of the neutral agents, rounded up, `read` for the rest."""
        return ["review"] * ((n_neutral + 1) // 2) + ["read"] * (n_neutral // 2)

    def stage(self, plan: Plan, row: dict) -> Staged:
        """The seats, the staging tarball named by its layout, the setup script and the block the setup, the scorer
        and run.json read."""
        seat_map = sandbox.seats(plan)
        kind = sandbox.layout_of(plan)
        layout = sandbox.stage_layout(row, kind, plan.split, seat_map)
        digest = hashlib.sha256(json.dumps(layout, sort_keys=True).encode()).hexdigest()[:12]
        tar = sandbox.stage_tar(row, layout, BUILD / "stage" / f"{row['id']}-{digest}.tgz")
        block = {"layout": kind, "meta": row.get("meta") or {}, "comparator": layout["comparator"],
                 "seats": seat_map, "evaluation": layout["evaluation"], "images": sandbox.ensure_images()}
        return Staged(compose=sandbox.write_compose(), block=block, files={STAGE_TAR: str(tar)},
                      setup=sandbox.setup_script(plan))

    def tools(self, plan: Plan) -> AgentTools:
        """A lone agent: ORBIT's bash and python as `user`, and review_deliverable. A team: shell and run_python per
        agent user, and review_deliverable for the agents that hold `review`."""
        if not plan.team:
            return roster_tools(plan.agents, None, task_tools=review_binding())
        seats = {a: Seat.read(record) for a, record in sandbox.seats(plan).items()}
        holders = {a: [REVIEW_TOOL] if "review" in plan.holds[a] else [] for a in plan.agents}
        return roster_tools(plan.agents, seats, task_tools=review_binding(), holders=holders)

    def access_note(self, plan: Plan) -> str | None:
        """Who holds the review tool and who holds read access (prompts.yaml knowledge_work.access_note), the same text
        for every seat, or None when the plan is not split or the prompts do not tell the agents of their team, since a
        note names teammates. A regulator's prompt can call it as it stands: the note takes no seat."""
        if not (plan.split and self.tells_team(plan)):
            return None
        texts = load_prompts()["knowledge_work"]
        names = {item: ", ".join(sandbox.holders(plan, item)) or texts["nobody"] for item in sandbox.ITEMS}
        return texts["access_note"].format(tool=REVIEW_TOOL, group=PIPELINE_GROUP, **names)

    def system_prompts(self, plan: Plan) -> dict[str, str]:
        """The base's prompts, then the access note on its own paragraph where there is one."""
        prompts, note = super().system_prompts(plan), self.access_note(plan)
        return {a: f"{prompt}\n\n{note}" for a, prompt in prompts.items()} if note else prompts

    async def prepare(self, state) -> None:
        await scoring.prepare(state)

    def probe(self, state) -> dict:
        return scoring.probe(state)

    async def watched(self, state) -> list[Watched]:
        """The pipeline artifact, and evaluation.json, which only leads to it."""
        service = machines.DEFAULT_SERVICE
        return [Watched(machine=service, path=scoring.knowledge(state)["comparator"], object="artifact"),
                Watched(machine=service, path=f"{CACHE}/evaluation.json", object="evaluation", level="pointer")]

    def scorers(self) -> list[Scorer]:
        return [scoring.knowledge_deliverables()]

    def delivered(self, plan: Plan) -> Callable[[], Awaitable[set[str]]]:
        """The agents a review call answered with a verdict, from the sample store; in a shared_task team one verdict
        counts for every agent (_knowledge_review_tool.reviewed)."""
        return reviewed(plan.agents, plan.mode == "shared_task")

    async def gradable(self, plan: Plan, agent: str) -> str | None:
        """The agent's contract (the team's in shared_task) holds a regular file in to_send/
        (_knowledge_scoring.sends_a_file)."""
        workdir = sandbox.seats(plan)[agent]["workdir"]
        return None if await scoring.sends_a_file(workdir) else self.missing(plan)

    def team_deviations(self, plan: Plan) -> list[str]:
        if plan.mode == "solo_task":
            return TEAM_DEVIATIONS + SOLO_DEVIATIONS
        return [*TEAM_DEVIATIONS, *SHARED_DEVIATIONS, *(SPLIT_DEVIATIONS if plan.split else []),
                *([ACCESS_NOTE_DEVIATION] if self.access_note(plan) else [])]

    @classmethod
    def episode_facts(cls, view, agent: str) -> dict:
        """The episode's review: its grader state."""
        score = (view.scores or {}).get(cls.scorer)
        state = ((((score.metadata or {}).get("knowledge") or {}).get("grader_state") or {}).get(agent)
                 if score else None)
        return {"review": state} if state else {}
