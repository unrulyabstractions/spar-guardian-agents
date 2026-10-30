"""The Inspect tasks of the experiment: one benchmark under one SwarmConfig, as an ORBIT task.

    task = swarm_task(OpenMath("riemann"), build_config("default", "medium", ["regulators=3"]))
    task = swarm_task(OpenMath("riemann"), config, name="riemann_cheat", extra_instruction=CHEAT)
    inspect eval src/tasks.py@openmath -T preset=default -T regulators=1

The last form skips what `python -m src run` adds: the key and routing checks, the role models built before the
sandbox starts, and the Gemini 402 retry (src/models/gemini_402_retry.py). A role model still gets the eval's generate
config there through the swarm model provider.

What comes from where:
  * the sample, its tools, its setup solver and its graders come from the benchmark (src/domains/);
  * the roster, the board, the board tools and team_submit, the hidden roles and the turn rules come from src/swarm/
    (board.py, hidden_roles.py, turn_rules/), and CheatBench's judge is added as a scorer
    (src/judging/cheatbench_judge.py).

The task instruction is the benchmark's, then the rule sentence of rule_level, then extra_instruction on its own
paragraph (a control cell's text, say). Each agent's tools are the benchmark's, then on a board board_status,
board_read and board_post, then team_submit for a neutral agent when the mode has a team to submit for, and
agent_actions for a regulator (src/swarm/regulator_oversight.py). They are bound per agent through ORBIT's
tool_bindings next to the benchmark's own bindings.

The plan's rounds, the scheduler's turn bound and Inspect's message limit follow SwarmConfig.working_steps,
max_turns and sample_message_limit. Under a turn budget metadata["cheatbench"]["turn_budget"] holds
{"budget", "steps_per_agent"}. Under stop_after_delivery the benchmark's delivery predicate (Benchmark.delivered) goes
to the turn rules' setup.

The run's facts go into the sample metadata: metadata["cheatbench"] gains the config dump, the hidden roles and their
composition, each role's model (role_models), the access plan and the deviations from CheatBench's own runner.
metadata["experiment"]["defenses"] holds the passive turn-rules defense, which every run carries.

Hidden roles are used on a board whose config asks for a regulator or a disregulator. An agent whose role has a model
of its own runs it through the swarm model provider (src/models/swarm_model_provider.py); every other agent runs the
eval's model.
"""
from __future__ import annotations

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from orbit.configs.execution import AgentGroup
from orbit.configs.experiment import ExperimentConfig
from orbit.configs.setup import AgentSpec, SetupConfig
from orbit.tasks.multi_agent import multi_agent_task

from src.configuration import access_policy, settings
from src.configuration.presets import preset, with_overrides
from src.configuration.prompt_texts import board_system_prompt
from src.configuration.swarm_config import SwarmConfig
from src.domains.benchmark_protocol import AgentTools, Benchmark
from src.domains.interview import PrimeFactorization, SubsetSum
from src.domains.knowledge import KnowledgeWork
from src.domains.openmath import OpenMath
from src.domains.openmath.openmath_settings import TOKEN_LIMIT
from src.domains.protein import ProteinDesign
from src.domains.swe import SWE
from src.domains.swe.swe_settings import GRADING_BUDGET_S
from src.judging.cheatbench_judge import cheatbench_judge
from src.models import swarm_model_provider as provider  # importing it registers the "swarm" provider
from src.swarm import hidden_roles as roles_module
from src.swarm.awareness_probes import end_of_episode_probes
from src.swarm.board import (
    agent_tool_names,
    board_bindings,
    board_communication,
    board_execution,
    board_scheduler,
)
from src.swarm.turn_rules import rules_setup, schedule, turn_rules_defense

JUDGE_ADDENDUM = settings.LUCY / "experiments" / "orbit_cheatbench" / "judge_addendum.py"


# --------------------------------------------------------------------------
# the parts of one task
# --------------------------------------------------------------------------
def level_key(benchmark: Benchmark) -> str:
    return roles_module.LEVEL_KEYS[benchmark.name]


def hidden_roles(config: SwarmConfig) -> dict[str, str] | None:
    """Each agent's hidden role on a board whose config asks for a regulator or a disregulator, else None."""
    if not config.board or config.regulator_count + config.disregulator_count == 0:
        return None
    return roles_module.assign_roles(config.names, config.regulator_count, config.disregulator_count, config.seed)


def instruction_suffix(benchmark: Benchmark, config: SwarmConfig, extra_instruction: str = "") -> str:
    """What follows the task instruction: the rule sentence on its own paragraph ("" at rule level none), then
    extra_instruction on its own paragraph."""
    extra = f"\n{extra_instruction}\n" if extra_instruction else ""
    return roles_module.rule_suffix(config, level_key(benchmark)) + extra


def system_prompts(benchmark: Benchmark, config: SwarmConfig, sample: Sample,
                   roles: dict[str, str] | None) -> dict[str, str | None]:
    """Each agent's system prompt: the benchmark's own (SWE) or the board prompt (openmath), then what its role adds.

    The openmath board prompt says what mention_team and mention_board turn on (prompt_texts.board_system_prompt);
    with neither, an agent has no system prompt of ours. A lone openmath agent has none, as in CheatBench's runner.
    A regulator or disregulator gets its role text and, with informed_roles, the briefing (roles.role_prompt).
    """
    names = config.names
    own = (sample.metadata["cheatbench"].get("swe") or {}).get("system_prompt")
    if own:
        base = {n: own[n] for n in names}
    else:
        base = {n: board_system_prompt(config, n, names) for n in names}
    if not roles:
        return base
    return {n: roles_module.with_role(base[n], roles_module.role_prompt(roles[n], config, level_key(benchmark),
                                                                         names))
            for n in names}


def team_tools(benchmark: Benchmark, config: SwarmConfig, tools: AgentTools,
               roles: dict[str, str] | None) -> tuple[dict[str, list[str]], dict]:
    """(each agent's AgentSpec.tools, ORBIT tool_bindings): the benchmark's tools (its per_agent names when it has
    them), then the board tools and team_submit (src/swarm/board.py)."""
    extra = agent_tool_names(config, roles)
    own = getattr(tools, "per_agent", None) or {}   # KnowledgeTools: the review tool follows the access plan
    names = {n: [*own.get(n, tools.names), *extra[n]] for n in config.names}
    return names, {**tools.bindings, **board_bindings(config, roles=roles)}


def agent_specs(config: SwarmConfig, tools: dict[str, list[str]], prompts: dict[str, str | None],
                roles: dict[str, str] | None = None) -> list[AgentSpec]:
    """One worker per name. An agent with no prompt of ours keeps AgentSpec's default system prompt. Each
    conversation is capped at the sample's message limit plus ten.

    An agent whose hidden role has a model of its own (SwarmConfig.role_models) gets AgentSpec.model
    "swarm/<Inspect string>" (provider.agent_model); the others leave it None and run the eval's model. roles None
    means every agent is a worker."""
    def model_of(name: str) -> dict:
        model = provider.agent_model(config.role_models(), roles, name)
        return {"model": model} if model else {}

    return [AgentSpec(name=n, role="worker", tools=list(tools[n]), max_messages=config.sample_message_limit + 10,
                      submit=config.shutdown, **model_of(n), **({"system_prompt": prompts[n]} if prompts[n] else {}))
            for n in config.names]


def first_message_groups(sample: Sample, names: list[str]) -> list[AgentGroup]:
    """One group per agent whose first message differs from the sample input (SWE teams)."""
    first = sample.metadata["cheatbench"].get("first_message") or {}
    if not first:
        return []
    if any(not (first.get(n) or "").strip() for n in names):
        raise ValueError("an empty goal would fall back to the sample input")
    return [AgentGroup(name=f"seat_{n}", agents=[n], goal=first[n]) for n in names]


def scorers(benchmark: Benchmark, config: SwarmConfig) -> list:
    """The benchmark's scorers, CheatBench's judge unless the config's judge is None, and the awareness probes."""
    judge = [cheatbench_judge(benchmark.name, str(benchmark.task_root), judge=config.judge,
                              addendum=str(JUDGE_ADDENDUM) if config.judge_addendum else None,
                              batch=config.batch_judge)] if config.judge else []
    probes = [end_of_episode_probes()] if config.awareness_probe else []
    return [*benchmark.scorers(), *judge, *probes]


def delivery(benchmark: Benchmark, config: SwarmConfig) -> dict:
    """rules_setup's delivery predicate under stop_after_delivery ({"delivered": Benchmark.delivered}); nothing
    without the rule, so the eval log's plan records no predicate."""
    if config.stop_after_delivery is None:
        return {}
    return {"delivered": benchmark.delivered(config.names, config)}


# --------------------------------------------------------------------------
# metadata the orchestrator and the exporter read
# --------------------------------------------------------------------------
def record_run(task_: Task, benchmark: Benchmark, config: SwarmConfig, roles: dict[str, str] | None,
               prompts: dict[str, str | None]) -> None:
    """Write the turn rules' defense into the experiment config and the run's facts into metadata["cheatbench"].

    multi_agent_task takes no defenses; the orchestrator reads them from metadata["experiment"]["defenses"]
    (orbit/solvers/orchestrator.py _build_attacks_and_defenses).
    """
    defenses = [turn_rules_defense(config).model_dump(mode="json")]
    for sample in task_.dataset:
        experiment = sample.metadata["experiment"]
        if not config.board:
            experiment["communication"] = None      # a lone agent gets no channel tools
        experiment["defenses"] = defenses
        ExperimentConfig.model_validate(experiment)
        cb = sample.metadata["cheatbench"]
        cb["config"] = config.model_dump(mode="json")
        cb["role_models"] = config.role_models()
        cb["code"] = settings.code_state()
        cb["access"] = {"policy": access_policy.policy_name(benchmark.applied_access(config.names, config, roles)),
                        "agents": benchmark.access_plan(config.names, config, roles)}
        cb["deviations"] = benchmark.run_deviations(config.n_agents, shutdown=config.shutdown, mode=config.mode)
        if config.turn_budget is not None:
            cb["turn_budget"] = {"budget": config.turn_budget, "steps_per_agent": config.working_steps}
        if roles:
            cb["roles"] = roles
            cb["composition"] = roles_module.composition_label(roles)
        first = cb.get("first_message") or {}
        if roles and cb.get("judge_prompt") and first:
            # the judge sees each SWE agent's whole system prompt, role text included
            cb["judge_prompt"] = {n: f"{prompts[n]}\n\n{first[n]}" for n in config.names}


# --------------------------------------------------------------------------
# the task
# --------------------------------------------------------------------------
def swarm_task(benchmark: Benchmark, config: SwarmConfig, *, name: str | None = None,
               extra_instruction: str = "") -> Task:
    """One benchmark under one config, as an ORBIT task on Inspect. extra_instruction follows the rule sentence after
    the task instruction, on its own paragraph."""
    settings.check_submodule_heads()
    benchmark.check(config)
    names = config.names
    roles = hidden_roles(config)
    sample = benchmark.sample(names, instruction_suffix=instruction_suffix(benchmark, config, extra_instruction),
                              config=config, roles=roles)
    tools = benchmark.tools(names, config)
    agent_tools, bindings = team_tools(benchmark, config, tools, roles)
    prompts = system_prompts(benchmark, config, sample, roles)
    time_limit_s = config.time_limit_s or benchmark.time_limit_s()
    communication = board_communication(names, max_message_chars=config.max_message_chars) if config.board else None
    # Inspect's hard time limit sits above ORBIT's soft halt at time_limit_s (SwarmConfig.time_grace_s); SWE grades
    # after the agents stop, so its limit leaves room for setup and grading instead.
    grace = GRADING_BUDGET_S if isinstance(benchmark, SWE) else config.time_grace_s
    task_ = multi_agent_task(
        name=name or f"swarm_{level_key(benchmark)}",
        setup=SetupConfig(agents=agent_specs(config, agent_tools, prompts, roles),
                          communication_edges=None, tool_kwargs=tools.tool_kwargs),
        dataset=[sample],
        communication=communication,
        execution=board_execution(plan=schedule(config, names, roles),
                                  agent_groups=first_message_groups(sample, names), initial_context="sample_input"),
        scheduler=board_scheduler(max_turns=config.max_turns + 5, max_time_seconds=time_limit_s),
        scorer=scorers(benchmark, config),
        tool_bindings=bindings or None,
        task_setup=[s for s in (rules_setup(config, names, roles, **delivery(benchmark, config)),
                                benchmark.setup()) if s is not None],
        message_limit=config.sample_message_limit,
        token_limit=config.token_limit or config.n_agents * TOKEN_LIMIT,
        time_limit=time_limit_s + grace,
    )
    record_run(task_, benchmark, config, roles, prompts)
    return task_


def _config(preset_name: str, overrides: dict) -> SwarmConfig:
    """The preset, then every -T key=value; scale=<name> applies that scale first (presets.with_overrides)."""
    return with_overrides(preset(preset_name), [f"{key}={value}" for key, value in overrides.items()])


@task
def openmath(preset: str = "default", row: str = "riemann", **overrides) -> Task:
    """CheatBench's openmath_agent row under a preset; every other -T key=value sets a config field."""
    return swarm_task(OpenMath(row), _config(preset, overrides))


@task
def swe(preset: str = "default", issue: str = "flipt-02e2", honeypot: str = "jigsaw", grade: bool = True,
        **overrides) -> Task:
    """A CheatBench SWE issue under one of Lucy's honeypots (jigsaw by default: the fix's pieces are dealt over
    the neutral agents) and a preset; other -T key=value set config fields."""
    grade = grade if isinstance(grade, bool) else str(grade).strip().lower() in ("1", "true", "yes")
    return swarm_task(SWE(issue, honeypot, grade), _config(preset, overrides))


@task
def knowledge_work(preset: str = "default", row: str | None = None, **overrides) -> Task:
    """CheatBench's knowledge_work task, one row (the domain's default when None), under a preset; every other
    -T key=value sets a config field."""
    return swarm_task(KnowledgeWork(row) if row else KnowledgeWork(), _config(preset, overrides))


@task
def subset_sum(preset: str = "default", row: str | None = None, **overrides) -> Task:
    """CheatBench's subset_sum interview, one question (the domain's default when None), under a preset; every other
    -T key=value sets a config field."""
    return swarm_task(SubsetSum(row), _config(preset, overrides))


@task
def prime_factorization(preset: str = "default", row: str | None = None, **overrides) -> Task:
    """CheatBench's prime_factorization interview, one question (the domain's default when None), under a preset;
    every other -T key=value sets a config field."""
    return swarm_task(PrimeFactorization(row), _config(preset, overrides))


@task
def protein_design(preset: str = "default", row: str | None = None, **overrides) -> Task:
    """CheatBench's protein_design task, one target (the domain's default when None), under a preset; every other
    -T key=value sets a config field."""
    return swarm_task(ProteinDesign(row) if row else ProteinDesign(), _config(preset, overrides))
