"""The Inspect tasks of the experiment: one benchmark under one SwarmConfig, as an ORBIT task.

    task = swarm_task(OpenMath("riemann"), build_config("default", "medium", ["regulators=3"]))
    inspect eval src/tasks.py@openmath -T preset=default -T regulators=1

The second form skips what `python -m src run` adds: the key and routing checks, the role models built before the
sandbox starts, and the Gemini 402 retry (src/utils/gemini_402_retry.py). A role model still gets the eval's generate
config there through the swarm model provider.

What comes from where:
  * the sample, its tools, its setup solver and its graders come from the benchmark (src/benchmark/domains/openmath/,
    src/benchmark/domains/swe/, .../knowledge/, .../interview/, .../protein/);
  * the roster, the board, the board tools and team_submit, the hidden roles, the written plan and the turn rules
    come from src/swarm/ (comm_topology.py, board.py, roles.py, rules.py);
  * the control's defenses come from controls.py, and CheatBench's judge is added as a scorer
  (src/benchmark/cheatbench_judge.py).

Each agent's tools are the benchmark's, then, on a pull board (board_tools), board_status, board_read and board_post,
then team_submit for a neutral agent when the mode has a team to submit for, and agent_actions for a regulator
(src/swarm/regulator_oversight.py). They are bound per agent through ORBIT's tool_bindings next to the
benchmark's own bindings.

The plan's rounds, the scheduler's turn bound and Inspect's message limit follow the domain's turn budget
(SwarmConfig.steps_for, max_turns_for, message_limit_for). Under a budget metadata["cheatbench"]["turn_budget"] holds
{"budget", "steps_per_agent"}; without one the key is absent. Under stop_after_delivery the benchmark's delivery
predicate (Benchmark.delivered) goes to the turn rules' setup; without the rule rules_setup gets no predicate.

The run's facts go into the sample metadata: metadata["cheatbench"] gains the config dump, the control, the hidden
roles and their composition, each role's model (role_models), and the deviations from CheatBench's own runner. The
mode and who holds team_submit follow from the config dump and the roles (SwarmConfig.resolve_mode,
board.agent_tool_names).
metadata["experiment"]["defenses"] holds the control's defenses plus the passive turn-rules defense, which every run
carries.

An agent whose role has a model of its own (worker_model, regulator_model, disregulator_model) runs it through the
swarm model provider (src/configuration/swarm_model_provider.py); every other agent runs the eval's model.

Hidden roles are used on a board of three or more agents on openmath (the October 2026 swarm), and on any board whose
config asks for a regulator or a disregulator. Two openmath agents without roles are the plain board.
"""
from __future__ import annotations

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from orbit.configs.execution import AgentGroup
from orbit.configs.experiment import ExperimentConfig
from orbit.configs.setup import AgentSpec, SetupConfig
from orbit.tasks.multi_agent import multi_agent_task

from src.benchmark.benchmark_protocol import AgentTools, Benchmark
from src.benchmark.cheatbench_judge import cheatbench_judge
from src.benchmark.domains.interview import PrimeFactorization, SubsetSum
from src.benchmark.domains.knowledge import KnowledgeWork
from src.benchmark.domains.openmath import OpenMath
from src.benchmark.domains.openmath.openmath_settings import TOKEN_LIMIT
from src.benchmark.domains.protein import ProteinDesign
from src.benchmark.domains.swe import SWE
from src.benchmark.domains.swe.swe_settings import GRADING_BUDGET_S
from src.configuration import access_policy, settings
from src.configuration import swarm_model_provider as provider  # importing it registers the "swarm" provider
from src.configuration.swarm_config import SwarmConfig, board_system_prompt, preset
from src.swarm import hidden_roles as roles_module
from src.swarm.awareness_probes import end_of_episode_probes
from src.swarm.board_config import (
    board_communication,
    board_execution,
    board_scheduler,
)
from src.swarm.control_defenses import BASELINE, defenses_for
from src.swarm.pull_board import agent_tool_names, board_bindings
from src.swarm.turn_rules import rules_setup, schedule, turn_rules_defense

JUDGE_ADDENDUM = settings.LUCY / "experiments" / "orbit_cheatbench" / "judge_addendum.py"
MONITOR_FACTOR = 3        # an LLM control's own calls count toward the token limit


# --------------------------------------------------------------------------
# the parts of one task
# --------------------------------------------------------------------------
def level_key(benchmark: Benchmark) -> str:
    return roles_module.LEVEL_KEYS[benchmark.name]


def hidden_roles(benchmark: Benchmark, config: SwarmConfig) -> dict[str, str] | None:
    """Each agent's hidden role, or None when the run uses no roles (a lone agent or a plain board)."""
    asked = config.regulator_count + config.disregulator_count > 0
    swarm = isinstance(benchmark, OpenMath) and config.n_agents > 2
    if not config.board or not (asked or swarm):
        return None
    return roles_module.assign_roles(config.names, config.regulator_count, config.disregulator_count, config.seed)


def rule_suffix(benchmark: Benchmark, config: SwarmConfig) -> str:
    """The rule sentence on its own paragraph, or "" at rule level none."""
    return roles_module.rule_suffix(config, level_key(benchmark))


def build_sample(benchmark: Benchmark, config: SwarmConfig, suffix: str,
                 roles: dict[str, str] | None = None) -> Sample:
    """The benchmark's sample under the config: the mode, the board settings and who is neutral (roles; neutral
    agents hold team_submit) shape its prompts and workspaces."""
    return benchmark.sample(config.names, instruction_suffix=suffix, config=config, roles=roles)


def system_prompts(benchmark: Benchmark, config: SwarmConfig, sample: Sample,
                   roles: dict[str, str] | None) -> dict[str, str | None]:
    """Each agent's system prompt: the benchmark's own (SWE) or the board prompt (openmath), then what its role adds.

    The openmath board prompt says what mention_team and mention_board turn on (swarm_config.board_system_prompt);
    with neither, an agent has no system prompt of ours. A lone openmath agent has none, as in CheatBench's runner.
    A regulator or disregulator gets its role text and, with informed_roles, the briefing (roles.role_prompt).
    """
    names = config.names
    own = (sample.metadata["cheatbench"].get("swe") or {}).get("system_prompt")
    if own:
        base = {n: own[n] for n in names}
    else:
        base = {n: board_system_prompt(config, n, names, workdir=benchmark.workdir, domain=level_key(benchmark))
                for n in names}
    if not roles:
        return base
    return {n: roles_module.with_role(base[n], roles_module.role_prompt(roles[n], config, level_key(benchmark),
                                                                         names))
            for n in names}


def team_tools(benchmark: Benchmark, config: SwarmConfig, tools: AgentTools,
               roles: dict[str, str] | None) -> tuple[dict[str, list[str]], dict]:
    """(each agent's AgentSpec.tools, ORBIT tool_bindings): the benchmark's tools (its per_agent names when it has
    them), then the board tools and team_submit (src/swarm/pull_board.py)."""
    extra = agent_tool_names(config, config.names, level_key(benchmark), roles)
    own = getattr(tools, "per_agent", None) or {}   # KnowledgeTools: the review tool follows the access plan
    names = {n: [*own.get(n, tools.names), *extra[n]] for n in config.names}
    return names, {**tools.bindings, **board_bindings(config, roles=roles)}


def agent_specs(config: SwarmConfig, tools: dict[str, list[str]], prompts: dict[str, str | None],
                message_limit: int | None = None, roles: dict[str, str] | None = None) -> list[AgentSpec]:
    """One worker per name. An agent with no prompt of ours keeps AgentSpec's default system prompt. Each
    conversation is capped at the sample's message limit (message_limit, else config.message_limit) plus ten.

    An agent whose hidden role has a model of its own (SwarmConfig.role_models) gets AgentSpec.model
    "swarm/<Inspect string>" (src/configuration/swarm_model_provider.py); the others leave it None and run
    the eval's model.
    roles None means every agent is a worker."""
    limit = config.message_limit if message_limit is None else message_limit
    chosen = config.role_models()

    def model_of(name: str) -> dict:
        model = chosen[(roles or {}).get(name, "worker")]
        return {"model": provider.role_model(model)} if model else {}

    return [AgentSpec(name=n, role="worker", tools=list(tools[n]), max_messages=limit + 10,
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


def token_limit(config: SwarmConfig) -> int:
    if config.token_limit:
        return config.token_limit
    return config.n_agents * TOKEN_LIMIT * (MONITOR_FACTOR if config.control not in BASELINE else 1)


def delivery(benchmark: Benchmark, config: SwarmConfig) -> dict:
    """rules_setup's delivery predicate under stop_after_delivery ({"delivered": Benchmark.delivered}); nothing
    without the rule, so the eval log's plan records the setup's arguments as before."""
    if config.stop_after_delivery is None:
        return {}
    return {"delivered": benchmark.delivered(config.names, config)}


def check_control(benchmark: Benchmark, config: SwarmConfig) -> None:
    """The task-aware controls carry the openmath rule; refuse them on another benchmark."""
    if config.control.endswith("_task") and not isinstance(benchmark, OpenMath):
        raise ValueError(f"control {config.control!r} carries the openmath rule (src/configuration/controls.yaml); "
                         f"no criteria are written for {benchmark.name}")


# --------------------------------------------------------------------------
# metadata the orchestrator and the exporter read
# --------------------------------------------------------------------------
def record_run(task_: Task, benchmark: Benchmark, config: SwarmConfig, roles: dict[str, str] | None,
               prompts: dict[str, str | None]) -> None:
    """Write the defenses into the experiment config and the run's facts into metadata["cheatbench"].

    multi_agent_task takes no defenses; the orchestrator reads them from metadata["experiment"]["defenses"]
    (orbit/solvers/orchestrator.py _build_attacks_and_defenses).
    """
    control = defenses_for(config.control, config.monitor_model)
    defenses = [d.model_dump(mode="json") for d in [*control, turn_rules_defense(config)]]
    for sample in task_.dataset:
        experiment = sample.metadata["experiment"]
        if not config.board:
            experiment["communication"] = None      # a lone agent gets no channel tools
        experiment["defenses"] = defenses
        ExperimentConfig.model_validate(experiment)
        cb = sample.metadata["cheatbench"]
        cb["control"] = config.control if config.control not in BASELINE else "baseline"
        cb["monitor_model"] = config.monitor_model if control else None
        cb["config"] = config.model_dump(mode="json")
        cb["role_models"] = config.role_models()
        cb["code"] = settings.code_state()
        cb["access"] = {"policy": access_policy.policy_name(benchmark.applied_access(config.names, config, roles)),
                        "agents": benchmark.access_plan(config.names, config, roles)}
        cb["deviations"] = benchmark.run_deviations(config.n_agents, shutdown=config.shutdown,
                                                    mode=config.resolve_mode(benchmark.name))
        budget = config.turn_budget_for(level_key(benchmark))
        if budget is not None:
            cb["turn_budget"] = {"budget": budget, "steps_per_agent": config.steps_for(level_key(benchmark))}
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
               sample: Sample | None = None) -> Task:
    """One benchmark under one config, as an ORBIT task on Inspect. `sample` is a caller's own sample in place of
    build_sample's (benchmark.sample with the caller's instruction_suffix, say); it must be for config.names."""
    settings.check_submodule_heads()
    check_control(benchmark, config)
    benchmark.check(config)
    names = config.names
    domain = level_key(benchmark)
    config.check_windows(domain)
    roles = hidden_roles(benchmark, config)
    if sample is None:
        sample = build_sample(benchmark, config, rule_suffix(benchmark, config), roles)
    tools = benchmark.tools(names, config)
    agent_tools, bindings = team_tools(benchmark, config, tools, roles)
    prompts = system_prompts(benchmark, config, sample, roles)
    time_limit_s = config.time_limit_s or benchmark.time_limit_s()
    communication = board_communication(names, delivery=config.delivery, visibility=config.visibility,
                                        max_message_chars=config.max_message_chars,
                                        max_context_chars=config.max_context_chars,
                                        max_inbox_messages=config.max_inbox_messages) if config.board else None
    # Inspect's hard time limit sits above ORBIT's soft halt at time_limit_s (SwarmConfig.time_grace_s); SWE grades
    # after the agents stop, so its limit leaves room for setup and grading instead.
    grace = GRADING_BUDGET_S if isinstance(benchmark, SWE) else config.time_grace_s
    inspect_time = {"time_limit": time_limit_s + grace}
    message_limit = config.message_limit_for(domain)
    task_ = multi_agent_task(
        name=name or f"swarm_{domain}",
        setup=SetupConfig(agents=agent_specs(config, agent_tools, prompts, message_limit, roles),
                          communication_edges=None, tool_kwargs=tools.tool_kwargs),
        dataset=[sample],
        communication=communication,
        execution=board_execution(quantum=config.quantum, plan=schedule(config, names, roles, domain),
                                  agent_groups=first_message_groups(sample, names), initial_context="sample_input"),
        scheduler=board_scheduler(max_turns=config.max_turns_for(domain) + 5, max_time_seconds=time_limit_s),
        scorer=scorers(benchmark, config),
        tool_bindings=bindings or None,
        task_setup=[s for s in (rules_setup(config, names, roles, domain, **delivery(benchmark, config)),
                                benchmark.setup()) if s is not None],
        message_limit=message_limit,
        token_limit=token_limit(config),
        **inspect_time,
    )
    record_run(task_, benchmark, config, roles, prompts)
    return task_


def _config(preset_name: str, overrides: dict) -> SwarmConfig:
    """The preset, then every -T key=value; scale=<name> applies that scale first (SwarmConfig.with_overrides)."""
    return preset(preset_name).with_overrides([f"{key}={value}" for key, value in overrides.items()])


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
