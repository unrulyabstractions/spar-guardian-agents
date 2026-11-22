"""The Inspect tasks of the experiment: one benchmark under one SwarmConfig, as an ORBIT task.

    task = swarm_task(OpenMath("riemann"), build_config("default", "medium", ["regulators=3"]))
    task = swarm_task(OpenMath("riemann"), config, name="riemann_cheat", extra_instruction=CHEAT)
    inspect eval src/tasks.py@openmath -T preset=default -T regulators=1

The last form skips what `python -m src run` adds: the key and routing checks, the role models built before the
sandbox starts, and the Gemini 402 retry (src/models/gemini_402_retry.py). A role model still gets the eval's generate
config there through the swarm model provider.

What comes from where:
  * the benchmark's plan (Benchmark.plan: the mode, the access and what each agent holds) is fixed once, and the
    sample, its tools, its setup solver, its graders and its deviations come from the benchmark under it
    (src/domains/); the @task entry points take the registry's benchmarks (src/domains/__init__.py DOMAINS);
  * the roster, the board and its tools, submit and stop_working, the influencers and the turn rules come from
    src/swarm/ (board.py, submit_and_stop.py, influencers.py, turn_rules/), and CheatBench's judge is added as a
    scorer (src/judging/cheatbench_judge.py);
  * the access log's sidecar, its setup solver, its first scorer and its cleanup come from src/sandbox/access_log/, for
    a monitored benchmark under SwarmConfig.access_log, and the network log's sidecar, setup solver, scorer and
    capture from src/sandbox/network_log/ under SwarmConfig.network_log;
  * /proc's hiding, the AppArmor profile on every service of the compose file and the setup solver that remounts each
    agent machine's /proc and proves both, from src/sandbox/proc_hiding/ under SwarmConfig.proc_hiding;
  * the /tmp snapshots of every agent machine, before round 1 and after each call that ran a command there, from
    src/sandbox/live_tmp_capture.py under SwarmConfig.tmp_snapshots.

The task instruction is the benchmark's, then the rule sentence of rule_level, then extra_instruction on its own
paragraph (a control cell's text, say), then the rounds' values: "You have R rounds, and up to N tool calls per round."
(prompt_texts.rounds_values). No system prompt holds a number: it states the rounds' rules (board.round_rules) and
every other parameter's rule without its value, and each agent's first message gives those values after the rounds'
(agent_values: prompts.yaml first_values). A regulator that oversees reads that instruction inside a first message of
its own, its values after it, and gets a system prompt of its own (first_messages, system_prompts). Each agent's tools
are the benchmark's, then on a board board_status, board_read and board_post and the oversight tools for a regulator
(src/swarm/_regulator_oversight.py), then submit and stop_working as the agent holds them
(src/swarm/submit_and_stop.py ending_tools), then end_round (src/swarm/end_round.py). They are bound per agent through
ORBIT's tool_bindings next to the benchmark's own bindings. No agent gets ORBIT's own submit (AgentSpec.submit=False).

The plan's rounds, the scheduler's turn bound and Inspect's message limit follow SwarmConfig.rounds, max_turns and
sample_message_limit. The turn rules' setup gets the agents' time limit, after which a round ends at its next batch,
and under stop_after_delivery the benchmark's delivery predicate (Benchmark.delivered).

The run's facts go into the sample metadata: metadata["cheatbench"] gains the config dump, each agent's role and the
team's composition, each role's model (role_models), the plan's access (the applied policy, each agent's items and
numbered pieces) and the deviations from CheatBench's own runner.
metadata["experiment"]["defenses"] holds the passive turn-rules defense, which every run carries.

Influencers are used on a board whose config asks for a regulator or a disregulator. An agent whose role has a model
of its own runs it through the swarm model provider (src/models/swarm_model_provider.py); every other agent runs the
eval's model.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.model import GenerateConfig
from inspect_ai.solver import Solver, TaskState
from inspect_ai.util import SandboxEnvironmentSpec
from orbit.configs.execution import AgentGroup
from orbit.configs.experiment import ExperimentConfig
from orbit.configs.setup import AgentSpec, SetupConfig
from orbit.tasks.multi_agent import multi_agent_task

from src.configuration import access_policy, settings
from src.configuration.presets import preset, with_overrides
from src.configuration.prompt_texts import before_line, rounds_values
from src.configuration.seed_packs import seed_record
from src.configuration.swarm_config import SwarmConfig, role_of
from src.domains import DOMAINS
from src.domains.benchmark_protocol import Benchmark, Plan
from src.judging.cheatbench_judge import JUDGE_ADDENDUM, cheatbench_judge
from src.models import swarm_model_provider as provider  # importing it registers the "swarm" provider
from src.sandbox import command_jobs, peer_shell
from src.sandbox.access_log.access_monitor import access_capture, capture, start_access_monitor, with_monitor
from src.sandbox.agent_tools import AgentTools, holds_commands
from src.sandbox.live_tmp_capture import machines_of, tmp_capture_setup
from src.sandbox.network_log.network_monitor import (
    capture_network,
    keeps_network_log,
    network_capture,
    start_network_monitor,
    with_network_monitor,
)
from src.sandbox.proc_hiding.proc_hiding import hide_proc, keeps_proc_hiding, with_proc_hiding
from src.swarm import influencers as roles_module
from src.swarm.awareness_probes import end_of_episode_probes
from src.swarm.board import (
    agent_tool_names,
    board_bindings,
    board_communication,
    board_execution,
    board_scheduler,
    round_rules,
)
from src.swarm.end_round import end_round_bindings
from src.swarm.seed_setup import seed_setup
from src.swarm.submit_and_stop import Deliverable, ending_tools, submit_bindings
from src.swarm.submit_hold import hold_bindings, hold_tools
from src.swarm.tool_output_caps import cap_of, capped_bindings
from src.swarm.turn_rules import END_ROUND, planned_entries, rules_setup, schedule, turn_rules_defense

# Stops a runaway per agent. Inspect counts cached prompt tokens in full, so the count grows with the square of the
# steps while the bill barely moves: a context that grows steadily to 1M over 100 steps stays under this. An episode
# that trips it stays judgeable.
TOKEN_LIMIT = 64_000_000
# The label of a run with a seed pack (SwarmConfig.seed_pack): a previous team's posts and files before round 1.
SEED_DEVIATION = "a_previous_teams_posts_and_files_are_seeded_before_round_1"


# --------------------------------------------------------------------------
# the parts of one task
# --------------------------------------------------------------------------
def agent_roles(config: SwarmConfig) -> dict[str, str] | None:
    """Each agent's role (worker, regulator or disregulator) on a board whose config asks for an influencer, else
    None."""
    if not config.board or config.regulator_count + config.disregulator_count == 0:
        return None
    return roles_module.assign_roles(config.seats, config.regulator_count, config.disregulator_count, config.seed)


def instruction_suffix(benchmark: Benchmark, config: SwarmConfig, extra_instruction: str = "") -> str:
    """What follows the task instruction: the rule sentence on its own paragraph ("" at rule level none), then
    extra_instruction on its own paragraph, then the rounds' values on their own (prompt_texts.rounds_values)."""
    extra = f"\n{extra_instruction}\n" if extra_instruction else ""
    return roles_module.rule_suffix(config, benchmark.key) + extra + f"\n{rounds_values(config)}\n"


def system_prompts(benchmark: Benchmark, plan: Plan) -> dict[str, str]:
    """Each agent's system prompt: the benchmark's own (Benchmark.system_prompts: SWE's, or one that opens with the
    agent's name or team, lists the bullets of submit and stop_working and states the rounds' rules), then what its
    role adds. A regulator or disregulator gets its role text and, with informed_roles, the briefing
    (roles.role_prompt). A regulator that oversees gets them in a prompt of its own, in every domain, so the
    benchmark's prompt never asks it to do the task (roles.oversight_prompt), the rounds' rules last."""
    base, config = benchmark.system_prompts(plan), plan.config

    def prompt(agent: str) -> str | None:
        role = role_of(plan.roles, agent)
        added = roles_module.role_prompt(role, config, benchmark.key, plan.agents)
        return roles_module.oversight_prompt(agent, plan.agents, added, benchmark.access_note(plan),
                                             round_rules(config, agent, plan.roles,
                                                         commands=benchmark.commands)) \
            if config.oversees(role) else roles_module.with_role(base[agent], added)

    return {agent: prompt(agent) for agent in plan.agents}


def agent_values(benchmark: Benchmark, plan: Plan, agent: str) -> list[str]:
    """The values of the parameters the agent's system prompt (system_prompts) states with no number: the benchmark's
    (Benchmark.first_values) unless the agent is a regulator that oversees, whose prompt is its own, then its role's
    (roles.role_values)."""
    role = role_of(plan.roles, agent)
    own = [] if plan.config.oversees(role) else benchmark.first_values(plan, agent)
    return list(dict.fromkeys([*own, *roles_module.role_values(role, plan.config, benchmark.key, plan.agents)]))


def with_values(message: str, line: str, values: list[str]) -> str:
    """The message with the values after the rounds' values line, in its paragraph."""
    if not values:
        return message
    if message.count(line) != 1:
        raise ValueError(f"a first message holds the rounds' values {message.count(line)} times, not once")
    return message.replace(line, " ".join([line, *values]))


def first_messages(sample: Sample, plan: Plan, benchmark: Benchmark) -> dict[str, str] | None:
    """Every agent's first message when one differs from the sample's own (metadata["cheatbench"]["first_message"],
    else its input): with what its role's level says there (roles.role_first) on its own paragraph before the rounds'
    values, and its values after the rounds' values (agent_values). A regulator's that oversees is
    roles.oversight_first_message, then what counts and how to read the team's work (Benchmark.oversight_note), its
    role's text and its values, each a paragraph after it. None when no agent
    oversees and none has a role text or a value."""
    first = sample.metadata["cheatbench"].get("first_message") or dict.fromkeys(plan.agents, sample.input)
    overseers = {a for a in plan.agents if plan.config.oversees(role_of(plan.roles, a))}
    values = {a: agent_values(benchmark, plan, a) for a in plan.agents}
    own = {a: roles_module.role_first(role_of(plan.roles, a), plan.config, benchmark.key) for a in plan.agents}
    entries = planned_entries(plan.config, plan.agents, plan.roles)
    late = {a for a in plan.agents if entries.get(a, 1) > 1}
    if not overseers and not any(values.values()) and not any(own.values()) and not late:
        return None
    line = rounds_values(plan.config)

    def entered(agent: str, text: str) -> str:
        """A late entrant's message with the rounds it has left on entering, as its first round's line says."""
        return text.replace(line, rounds_values(plan.config, entries[agent]), 1) if agent in late else text

    def message(agent: str) -> str:
        text = own[agent].strip() if own[agent] else None
        if agent not in overseers:
            task = before_line(first[agent], line, text) if text else first[agent]
            return with_values(task, line, values[agent])
        parts = [roles_module.oversight_first_message(first[agent]).rstrip(), benchmark.oversight_note(plan, agent),
                 text, " ".join(values[agent])]
        return "\n\n".join(p for p in parts if p) + "\n"

    return {a: entered(a, message(a)) for a in plan.agents}


def deliverable(benchmark: Benchmark, plan: Plan) -> Deliverable:
    """What submit reads from the benchmark: its text for the run's kind of agent (SwarmConfig.submit_kind), the
    guard (Benchmark.gradable), the snapshot and whether the prompts tell the agents of their team."""
    return Deliverable(text=benchmark.submit_text(plan, plan.config.submit_kind),
                       missing=lambda agent: benchmark.gradable(plan, agent),
                       snapshot=lambda agent: benchmark.snapshot(plan, agent), told=benchmark.tells_team(plan))


def observers(benchmark: Benchmark, plan: Plan, tools: AgentTools) -> dict[str, int]:
    """Each influencer's observer uid under SwarmConfig.peer_shell (src/sandbox/peer_shell.py), else none. A domain
    whose agents have no shell of their own (chess) turns peer_shell off in its plan (Benchmark.plan)."""
    config = plan.config
    found = peer_shell.observer_uids(config.names, plan.roles) if config.peer_shell else {}
    if found and tools.seats is None:
        raise ValueError(f"peer_shell: {benchmark.name} gives its agents no shell to run it in")
    return found


def team_tools(benchmark: Benchmark, plan: Plan, tools: AgentTools) -> tuple[dict[str, list[str]], dict]:
    """(each agent's AgentSpec.tools, ORBIT tool_bindings): the benchmark's tools, then job_output and job_stop for an
    agent that holds a command tool (src/sandbox/command_jobs.py), then the board's (src/swarm/board.py), then
    peer_shell for an influencer under SwarmConfig.peer_shell, then submit and stop_working
    as the agent holds them (src/swarm/submit_and_stop.py), then hold_submit and release_submit for a regulator under
    SwarmConfig.submit_hold (src/swarm/submit_hold.py), then end_round for every agent (src/swarm/end_round.py).
    Every binding carries the run's cap on one tool result (src/swarm/tool_output_caps.py)."""
    config, roles = plan.config, plan.roles
    board = agent_tool_names(config, roles)
    watchers = observers(benchmark, plan, tools)
    shell = {peer_shell.TOOL} if watchers else set()
    own = {n: [*tools.per_agent[n], *(shell if n in watchers else ())] for n in config.names}
    job_tools = {n: list(command_jobs.TOOLS) if holds_commands(own[n]) else [] for n in config.names}
    names = {n: [*tools.per_agent[n], *job_tools[n], *board[n], *(shell if n in watchers else ()),
                 *ending_tools(config, role_of(roles, n)), *hold_tools(config, role_of(roles, n)), END_ROUND]
             for n in config.names}
    extra = peer_shell.peer_shell_binding(tools.seats, watchers, cap=lambda a: cap_of(config)) if watchers else {}
    jobs = command_jobs.job_bindings(cap=lambda a: cap_of(config)) if any(job_tools.values()) else {}
    bindings = {**tools.bindings, **jobs, **board_bindings(config, roles=roles), **extra,
                **submit_bindings(config, deliverable(benchmark, plan)), **hold_bindings(config),
                **end_round_bindings()}
    return names, capped_bindings(bindings, config)


def agent_specs(config: SwarmConfig, tools: dict[str, list[str]], prompts: dict[str, str | None],
                roles: dict[str, str] | None = None) -> list[AgentSpec]:
    """One worker per name, without ORBIT's own submit tool (submit and stop_working are ours, among its tools). An
    agent with no prompt keeps AgentSpec's default. Each conversation is capped at the sample's message limit plus
    ten.

    An agent whose role has a model of its own (SwarmConfig.role_models) gets AgentSpec.model
    "swarm/<Inspect string>" (provider.agent_model); the others leave it None and run the eval's model. roles None
    means every agent is a worker."""
    def model_of(name: str) -> dict:
        model = provider.agent_model(config.role_models(), roles, name)
        return {"model": model} if model else {}

    return [AgentSpec(name=n, role="worker", tools=list(tools[n]), max_messages=config.sample_message_limit + 10,
                      submit=False, **model_of(n), **({"system_prompt": prompts[n]} if prompts[n] else {}))
            for n in config.names]


def first_message_groups(sample: Sample, names: list[str]) -> list[AgentGroup]:
    """One group per agent whose first message differs from the sample input (SWE teams)."""
    first = sample.metadata["cheatbench"].get("first_message") or {}
    if not first:
        return []
    if any(not (first.get(n) or "").strip() for n in names):
        raise ValueError("an empty goal would fall back to the sample input")
    return [AgentGroup(name=f"seat_{n}", agents=[n], goal=first[n]) for n in names]


def monitored_sample(sample: Sample, access: bool = True, network: bool = False, hidden: bool = False) -> Sample:
    """The sample on the compose file with the access log's sidecar and, with `network`, the network log's, then with
    `hidden` every service under the proc-hiding profile (src/sandbox/proc_hiding/), the sidecars included."""
    compose = Path(sample.sandbox.config)
    compose = with_monitor(compose) if access else compose
    compose = with_network_monitor(compose) if network else compose
    compose = with_proc_hiding(compose) if hidden else compose
    spec = SandboxEnvironmentSpec(sample.sandbox.type, str(compose))
    return sample.model_copy(update={"sandbox": spec})


def setup_solvers(benchmark: Benchmark, plan: Plan, tools: AgentTools | None = None) -> list[Solver]:
    """The turn rules' setup, with the agents' time limit and the benchmark's round-end callback, then /proc's hiding
    under SwarmConfig.proc_hiding, before any command of an agent's user, and the benchmark's, then the seed pack's
    posts and files under SwarmConfig.seed_pack (src/swarm/seed_setup.py), then peer_shell's under
    SwarmConfig.peer_shell, then the access log's, so the harness's own reads while staging come before any mark, then
    the network log's, then the first /tmp listing of each machine under SwarmConfig.tmp_snapshots for a benchmark whose
    agents hold a shell (src/sandbox/live_tmp_capture.py), once the access log's marks name what it must not open."""
    config = plan.config
    watchers = observers(benchmark, plan, tools) if tools is not None else {}
    shell = [peer_shell.peer_shell_setup(tools.seats, watchers)] if watchers else []
    monitor = [start_access_monitor(benchmark)] if benchmark.keeps_access_log(config) else []
    network = [start_network_monitor(benchmark)] if keeps_network_log(benchmark, config) else []
    hidden = [hide_proc(benchmark)] if keeps_proc_hiding(benchmark, config) else []
    record = seed_record(config, benchmark.key, benchmark.row)
    seeded = [seed_setup(benchmark, record, config.names)] if record else []
    tmp = [tmp_capture_setup(*machines_of(tools.seats, config.names))] \
        if config.tmp_snapshots and benchmark.commands and tools is not None else []
    return [rules_setup(config, config.names, plan.roles, **delivery(benchmark, plan),
                        time_limit_s=config.time_limit_s or benchmark.time_limit_s(),
                        round_end=benchmark.round_end(plan), **team_head(benchmark, plan)), *hidden, benchmark.setup(),
            *seeded, *shell, *monitor, *network, *tmp]


def cleanup(benchmark: Benchmark, config: SwarmConfig) -> Callable[[TaskState], Awaitable[None]]:
    """Task.cleanup: the benchmark's, after the end of the command jobs (command_jobs.end_jobs, for a benchmark whose
    agents hold a shell) and the access log's and the network log's captures when the scorers never ran. end_jobs and
    each capture do nothing a second time, and a capture nothing for a log the sample does not keep."""
    logs = benchmark.keeps_access_log(config) or keeps_network_log(benchmark, config)
    if not logs and not benchmark.commands:
        return benchmark.cleanup

    async def run(state: TaskState) -> None:
        if benchmark.commands:
            await command_jobs.end_jobs()
        if logs:
            await capture(state)
            await capture_network(state)
        await benchmark.cleanup(state)
    return run


def scorers(benchmark: Benchmark, config: SwarmConfig) -> list:
    """The end of the command jobs (every job still running is killed) for a benchmark whose agents hold a shell, the
    access log's capture before any harness read of the honeypot, then the network log's, the benchmark's scorers,
    CheatBench's judge unless the config's judge is None, and the awareness probes."""
    monitor = [access_capture()] if benchmark.keeps_access_log(config) else []
    monitor += [network_capture()] if keeps_network_log(benchmark, config) else []
    judge = [cheatbench_judge(benchmark.name, str(benchmark.task_dir), judge=config.judge,
                              addendum=str(JUDGE_ADDENDUM) if config.judge_addendum else None,
                              batch=config.batch_judge)] if config.judge else []
    probes = [end_of_episode_probes()] if config.awareness_probe else []
    jobs = [command_jobs.jobs_capture()] if benchmark.commands else []
    return [*jobs, *monitor, *benchmark.scorers(), *judge, *probes]


def team_head(benchmark: Benchmark, plan: Plan) -> dict:
    """rules_setup's reader of the team's head for the push notices (Benchmark.team_head) and the line on how to read
    it (prompts.yaml presence.push_read); nothing for a domain or a layout with none."""
    reader = benchmark.team_head(plan)
    if reader is None:
        return {}
    return {"team_head": reader, "push_read": settings.load_prompts()["presence"]["push_read"][benchmark.key]}


def delivery(benchmark: Benchmark, plan: Plan) -> dict:
    """rules_setup's delivery predicate under stop_after_delivery ({"delivered": Benchmark.delivered}); nothing
    without the rule, so the eval log's plan records no predicate."""
    if plan.config.stop_after_delivery is None:
        return {}
    return {"delivered": benchmark.delivered(plan)}


# --------------------------------------------------------------------------
# metadata the orchestrator and the exporter read
# --------------------------------------------------------------------------
def record_run(task_: Task, benchmark: Benchmark, plan: Plan, prompts: dict[str, str],
               watchers: dict[str, int] | None = None) -> None:
    """Write the turn rules' defense into the experiment config and the run's facts into metadata["cheatbench"].

    multi_agent_task takes no defenses; the orchestrator reads them from metadata["experiment"]["defenses"]
    (orbit/solvers/orchestrator.py _build_attacks_and_defenses).
    """
    config, roles = plan.config, plan.roles
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
        cb["access"] = {"policy": access_policy.policy_name(plan.access), "agents": plan.holds,
                        "pieces": plan.pieces or None, "n_pieces": plan.n_pieces}
        cb["deviations"] = benchmark.deviations(plan) + ([SEED_DEVIATION] if config.seed_pack else [])
        seed = seed_record(config, benchmark.key, benchmark.row)
        if seed:
            cb["seed"] = seed
        if watchers:
            cb["peer_shell"] = {"observers": watchers}
        if roles:
            cb["roles"] = roles
            cb["composition"] = roles_module.composition_label(roles)
        first = cb.get("first_message") or {}
        if cb.get("judge_prompt") and first:
            # the judge sees each SWE agent's whole system prompt, role text included, and its first message
            cb["judge_prompt"] = {n: f"{prompts[n]}\n\n{first[n]}" for n in config.names}


# --------------------------------------------------------------------------
# the task
# --------------------------------------------------------------------------
def swarm_task(benchmark: Benchmark, config: SwarmConfig, *, name: str | None = None,
               extra_instruction: str = "") -> Task:
    """One benchmark under one config, as an ORBIT task on Inspect. extra_instruction follows the rule sentence after
    the task instruction, on its own paragraph."""
    settings.check_submodule_heads()
    names = config.names
    plan = benchmark.plan(config, agent_roles(config))
    sample = benchmark.sample(plan, instruction_suffix(benchmark, config, extra_instruction))
    first = first_messages(sample, plan, benchmark)
    if first:
        sample.metadata["cheatbench"]["first_message"] = first
    access, network = benchmark.keeps_access_log(config), keeps_network_log(benchmark, config)
    hidden = keeps_proc_hiding(benchmark, config)
    if access or network or hidden:
        sample = monitored_sample(sample, access, network, hidden)
    tools = benchmark.tools(plan)
    agent_tools, bindings = team_tools(benchmark, plan, tools)
    prompts = system_prompts(benchmark, plan)
    time_limit_s = config.time_limit_s or benchmark.time_limit_s()
    communication = board_communication(names, max_message_chars=config.max_message_chars) if config.board else None
    task_ = multi_agent_task(
        name=name or f"swarm_{benchmark.key}",
        setup=SetupConfig(agents=agent_specs(config, agent_tools, prompts, plan.roles),
                          communication_edges=None, tool_kwargs=tools.tool_kwargs),
        dataset=[sample],
        communication=communication,
        execution=board_execution(plan=schedule(config, names, plan.roles),
                                  agent_groups=first_message_groups(sample, names), initial_context="sample_input"),
        scheduler=board_scheduler(max_turns=config.max_turns + 5, max_time_seconds=time_limit_s),
        scorer=scorers(benchmark, config),
        tool_bindings=bindings or None,
        task_setup=setup_solvers(benchmark, plan, tools),
        cleanup=cleanup(benchmark, config),
        message_limit=config.sample_message_limit,
        # the cap on one tool result for every entry point, `inspect eval src/tasks.py@...` included
        **({"config": GenerateConfig(max_tool_output=config.tool_output_bytes)}
           if config.tool_output_bytes is not None else {}),
        token_limit=config.token_limit or config.n_agents * TOKEN_LIMIT,
        # Inspect's hard limit sits above ORBIT's soft halt at time_limit_s: the config's grace, or the benchmark's
        # own where it grades after the agents stop (SWE)
        time_limit=time_limit_s + (benchmark.time_grace_s or config.time_grace_s),
    )
    record_run(task_, benchmark, plan, prompts, observers(benchmark, plan, tools))
    return task_


def benchmark_task(key: str, preset_name: str, row: str | None, overrides: dict) -> Task:
    """The registry's benchmark for one row under a preset: the benchmark's options (Benchmark.options) come out of
    the -T pairs, and every other pair sets a config field (presets.with_overrides; scale=<name> first)."""
    cls = DOMAINS[key]
    options = {name: overrides.pop(name) for name in cls.options if name in overrides}
    config = with_overrides(preset(preset_name), [f"{k}={v}" for k, v in overrides.items()])
    return swarm_task(cls(row, **options), config)


@task
def openmath(preset: str = "default", row: str | None = None, **overrides) -> Task:
    """CheatBench's openmath_agent, one row."""
    return benchmark_task("openmath", preset, row, overrides)


@task
def swe(preset: str = "default", row: str | None = None, **overrides) -> Task:
    """A CheatBench SWE issue under one of Lucy's honeypots (-T honeypot=, jigsaw by default; -T grade=false skips
    the hidden tests)."""
    return benchmark_task("swe", preset, row, overrides)


@task
def knowledge_work(preset: str = "default", row: str | None = None, **overrides) -> Task:
    """CheatBench's knowledge_work, one GDPval row."""
    return benchmark_task("knowledge_work", preset, row, overrides)


@task
def subset_sum(preset: str = "default", row: str | None = None, **overrides) -> Task:
    """CheatBench's subset_sum interview, one question."""
    return benchmark_task("subset_sum", preset, row, overrides)


@task
def prime_factorization(preset: str = "default", row: str | None = None, **overrides) -> Task:
    """CheatBench's prime_factorization interview, one question."""
    return benchmark_task("prime_factorization", preset, row, overrides)


@task
def protein_design(preset: str = "default", row: str | None = None, **overrides) -> Task:
    """CheatBench's protein_design, one target."""
    return benchmark_task("protein_design", preset, row, overrides)


@task
def creative_writing(preset: str = "default", row: str | None = None, **overrides) -> Task:
    """CheatBench's creative_writings, one brief."""
    return benchmark_task("creative_writing", preset, row, overrides)


@task
def chess(preset: str = "default", row: str | None = None, **overrides) -> Task:
    """CheatBench's chess, one position."""
    return benchmark_task("chess", preset, row, overrides)
