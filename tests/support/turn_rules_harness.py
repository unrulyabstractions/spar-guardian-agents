"""The turn rules on Inspect's mock provider: a small ORBIT task (agents a01..a0n on one board, each with a private
`work` tool) run under a SwarmConfig with the turn-rules defense, and readers of what the provider received (the mock
sees the input after every model filter) and what the store recorded. No Docker, no credentials, no paid calls.

    sample, recorded = run(pull(steps_per_agent=2), {"a01": [do("A")], "a02": [stop()], "a03": [do("C")]}, tmp_path)
    store(sample, "ended")                                  # {"a02": "stop_working"}
    by_agent(recorded, "a01")[0]["tools"]                   # what the provider was offered
"""
from __future__ import annotations

import os

from inspect_ai import Task
from inspect_ai import eval as inspect_eval
from inspect_ai.dataset import Sample
from inspect_ai.log import read_eval_log
from inspect_ai.model import ChatCompletionChoice, ChatMessageAssistant, ModelOutput, get_model
from inspect_ai.scorer import Score, Target, scorer
from inspect_ai.tool import Tool, tool
from inspect_ai.util import StoreModel, store_as
from orbit import AgentSpec, SetupConfig, ToolFactory, multi_agent_task
from orbit.configs.experiment import ExperimentConfig
from pydantic import Field

from src.configuration.presets import PRESETS
from src.configuration.settings import load_prompts
from src.configuration.swarm_config import SwarmConfig, role_of
from src.swarm import board
from src.swarm import turn_rules as rules
from src.swarm.board import board_communication, board_execution, board_scheduler
from src.swarm.submit_and_stop import Deliverable, ending_tools, submit_bindings
from tests.support.mock_model import MODEL, calls, text

os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")

PROMPTS = load_prompts()
PROBE_ANSWER = "CODING_EVAL: 70\nSOCIAL_EVAL: 20\nREASON: it looked like a work test."


class WorkLog(StoreModel):
    executed: list[dict] = Field(default_factory=list)


@tool
def work(owner: str) -> Tool:
    async def execute(item: str) -> str:
        """Do one unit of work on your part.

        Args:
            item: What to work on.
        """
        log = store_as(WorkLog)
        log.executed = [*log.executed, {"agent": owner, "item": item}]
        return f"{owner} worked on {item}"

    return execute


@scorer(metrics=[])
def nothing():
    async def score(state, target: Target) -> Score:
        return Score(value=0)

    return score


# ------------------------------------------------------------------------------------------------ the scripted model

def do(*items: str) -> ModelOutput:
    return calls(*(("work", {"item": item}) for item in items))


def submit(answer: str = "done") -> ModelOutput:
    return calls(("submit", {"answer": answer}))


def stop() -> ModelOutput:
    return calls(("stop_working", {}))


def status() -> ModelOutput:
    return calls(("board_status", {}))


def read(**args) -> ModelOutput:
    return calls(("board_read", args))


def board_post(message: str) -> ModelOutput:
    return calls(("board_post", {"message": message}))


def out_of_context() -> ModelOutput:
    return ModelOutput(model=MODEL, choices=[ChatCompletionChoice(
        message=ChatMessageAssistant(content="...", model=MODEL, source="generate"), stop_reason="model_length")])


def scripted_model(scripts: dict[str, list[ModelOutput]], recorder: list[dict]):
    """Each agent follows its script, one output per provider call; probe questions get fixed answers."""
    count: dict[str, int] = {}

    def outputs(messages, tools, tool_choice, config) -> ModelOutput:
        last = messages[-1]
        if last.role == "user" and last.text in (PROMPTS["probes"]["reflection"], PROMPTS["probes"]["awareness"]):
            return text("It went fine." if last.text == PROMPTS["probes"]["reflection"] else PROBE_ANSWER)
        system = "\n".join(m.text for m in messages if m.role == "system")
        agent = next(name for name in scripts if f"You are {name}," in system)
        k = count.get(agent, 0)
        count[agent] = k + 1
        recorder.append({
            "agent": agent, "call": k, "tools": sorted(t.name for t in tools), "system": messages[0].text,
            "last": last.text, "tag": (last.metadata or {}).get(rules.TURN_RULES_TAG),
            "roles": [m.role for m in messages if m.role != "system"],
            "texts": [m.text for m in messages if m.role != "system"],
            "added": [((m.metadata or {})[rules.TURN_RULES_TAG]["kind"], m.text) for m in messages
                      if (m.metadata or {}).get(rules.TURN_RULES_TAG)],
            "batch": config.batch,
        })
        script = scripts[agent]
        return script[min(k, len(script) - 1)]

    model = get_model(MODEL, custom_outputs=outputs, memoize=False)

    async def count_text_tokens(text: str) -> int:   # avoids the mock provider's tokenizer download
        return max(1, (len(text) + 3) // 4)

    model.api.count_text_tokens = count_text_tokens
    return model


# ------------------------------------------------------------------------------------------------ the task

async def nothing_missing(agent: str) -> None:
    return None


def build_task(config: SwarmConfig, *, scorer_=None, roles: dict[str, str] | None = None, delivered=None,
               missing=nothing_missing) -> Task:
    """The test task; on a pull board each agent also gets the board tools, and every agent submit and stop_working
    as it holds them, bound as src/tasks.py binds them. `delivered` is the delivery predicate stop_after_delivery
    awaits; `missing` is submit's guard (Benchmark.gradable)."""
    names = config.names
    extra = board.agent_tool_names(config, roles)
    agents = [AgentSpec(name=n, role="engineer", tools=["work", *extra[n], *ending_tools(config, role_of(roles, n))],
                        max_messages=10_000, submit=False,
                        system_prompt=f"You are {n}, an engineer. Call work to make progress.") for n in names]
    task = multi_agent_task(
        name="turn_rules_test",
        setup=SetupConfig(agents=agents),
        tool_bindings={"work": ToolFactory(lambda ctx: work(ctx.agent_name)),
                       **board.board_bindings(config, roles=roles),
                       **submit_bindings(config, Deliverable("hand in the work.", missing, nothing_missing, True))},
        communication=board_communication(names, max_message_chars=config.max_message_chars) if config.board else None,
        execution=board_execution(plan=rules.schedule(config, names, roles), initial_context="sample_input"),
        scheduler=board_scheduler(max_turns=config.max_turns + 5, max_time_seconds=120),
        dataset=[Sample(id="issue", input="Fix the issue together.")],
        scorer=scorer_ or nothing(),
        task_setup=[rules.rules_setup(config, names, roles, **({"delivered": delivered} if delivered else {}))],
        token_limit=1_000_000,
    )
    for sample in task.dataset:
        if not config.board:
            sample.metadata["experiment"]["communication"] = None   # a lone agent gets no channel tools
        sample.metadata["experiment"]["defenses"] = [rules.turn_rules_defense(config).model_dump(mode="json")]
        ExperimentConfig.model_validate(sample.metadata["experiment"])
    return task


def run(config: SwarmConfig, scripts: dict, tmp_path, *, scorer_=None, roles=None, with_log=False, delivered=None,
        missing=nothing_missing, **generate):
    """Run the test task on the scripts: (the sample, the provider calls recorded), and the log with_log."""
    recorder: list[dict] = []
    [log] = inspect_eval(build_task(config, scorer_=scorer_, roles=roles, delivered=delivered, missing=missing),
                         model=scripted_model(scripts, recorder),
                         log_dir=str(tmp_path), display="none", log_realtime=False, max_retries=0, **generate)
    assert log.status == "success", log.error
    log = read_eval_log(log.location, resolve_attachments=True)
    [sample] = log.samples
    assert sample.error is None and sample.limit is None, (sample.error, sample.limit)
    return (sample, recorder, log) if with_log else (sample, recorder)


def pull(**fields) -> SwarmConfig:
    """The default preset (the pull board at the small scale) with fields changed. The turn budget is off unless a
    test sets it, so steps_per_agent sets the rounds."""
    return SwarmConfig.model_validate({**PRESETS["default"].model_dump(), "turn_budget": None, **fields})


def sequential(**fields) -> SwarmConfig:
    """The default preset with sequential single-agent rounds in declaration order and no free board actions, so a
    plan reads as one step per agent per round; the turn budget is off, so steps_per_agent sets the rounds."""
    return pull(**{"concurrent_rounds": False, "turn_order": "round_robin", "free_board_actions": 0, **fields})


# ------------------------------------------------------------------------------------------------ reading a run back

def by_agent(recorder: list[dict], agent: str) -> list[dict]:
    return [r for r in recorder if r["agent"] == agent]


def store(sample, field: str):
    return sample.store.get(f"TurnState:{field}")


def step_ids(sample, agent: str) -> list[str]:
    return [e["step"] for e in store(sample, "timeline") if e["agent"] == agent]


def tool_results(sample) -> list[tuple[str, str]]:
    """(function, result) of every tool call, in order."""
    return [(e.function, str(e.result)) for e in sample.events if e.event == "tool"]


def outcomes(sample, *functions: str) -> list[tuple[str, str, str | None]]:
    """(function, result, error message) of every call to the named tools, in order."""
    return [(e.function, str(e.result), e.error.message if e.error else None) for e in sample.events
            if e.event == "tool" and e.function in functions]
