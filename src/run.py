"""The run, judge, ladder and budget commands: run a task in process, judge a finished eval log again, place a
run's agents on the cheating ladder, or read lone runs' turns.

    run("swarm_R3D3_s1", OpenMath(), config, model="google/gemini-3.8-flash", timeout=2700)
    judge(Path("logs/swarm/swarm_R3D3/<log>.eval"), "swarm_R3D3", "gpt-6-luna", batch=True)
    ladder_runs([Path("runs/swarm_R3D3")], "gpt-6-luna", view="full", batch=True)
    budget([Path("runs/om_lone_1"), Path("runs/om_lone_2"), Path("runs/om_lone_3")])

A run calls inspect_ai.eval with the task from tasks.py, writes its log under logs/<kind>/<name>/, exports it to
runs/<name>/ with src/benchmark/run_export.py and prints one line per episode. CheatBench's judge runs inside
the eval as a
scorer, bound to the model role "judge". With batch_judge the judge's calls use the provider's batch API, and with
batch_agents so do the agents' calls (Inspect GenerateConfig.batch). A role with a model of its own (worker_model,
regulator_model, disregulator_model) runs it through the swarm model provider
(src/configuration/swarm_model_provider.py). The
provider carries the agents' generate config to that model's calls. Each role model is built once before the eval, so
a provider name Inspect does not know is refused before the sandbox starts. Re-judging reads a finished log, judges
every agent again with src/benchmark/cheatbench_judge.py and exports the log with the new verdicts. A refused
precondition prints
its reason to stderr, and the command returns the exit code the command line passes on.
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
import statistics
import sys
from pathlib import Path

import inspect_ai
from inspect_ai import Task
from inspect_ai._util.error import PrerequisiteError
from inspect_ai.log import read_eval_log
from inspect_ai.model import GenerateConfig, Model, get_model

from src.benchmark import cheatbench_judge as judging
from src.benchmark import judging_ladder as ladder
from src.benchmark.atif_transcripts import (
    SampleView,
    agent_messages,
    error_and_failure,
    judge_prompt,
    roster,
    transcript_dict,
)
from src.benchmark.domains.swe import SWE
from src.benchmark.run_export import write_run
from src.configuration import model_registry as models
from src.configuration import settings
from src.configuration import swarm_model_provider as provider  # importing it registers the "swarm" provider
from src.configuration.swarm_config import SwarmConfig
from src.measures import code_ladder as signals
from src.measures import episode_summary as summary
from src.tasks import JUDGE_ADDENDUM, hidden_roles, swarm_task
from src.utils import gemini_402_retry as provider_retry


class Refusal(Exception):
    """A precondition failed: the message is printed and the code returned."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


def command(fn):
    """A command prints its Refusal to stderr and returns the refusal's exit code."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Refusal as refusal:
            print(refusal, file=sys.stderr)
            return refusal.code
    return wrapper


# --------------------------------------------------------------------------
# keys
# --------------------------------------------------------------------------
def require_provider_key(model: str) -> None:
    """One of the key variables src/configuration/model_registry.yaml names for the model's route, and the
    route's base URL.

    Gemini's key may be given as GEMINI_API_KEY; it is also exported as GOOGLE_API_KEY, the name Inspect reads. A
    model outside the registry (mockllm) needs nothing."""
    provider = models.provider_of(model)
    if provider is None:
        return
    names = models.key_env(model)
    if not any(os.environ.get(name) for name in names):
        raise Refusal(f"{' or '.join(names)} must be set for {model}", 1)
    if "GOOGLE_API_KEY" in names and os.environ.get("GEMINI_API_KEY"):
        os.environ.setdefault("GOOGLE_API_KEY", os.environ["GEMINI_API_KEY"])
    route = models.providers()[provider]
    if route["inspect_prefix"].startswith("openai-api/") and route.get("base_url"):
        os.environ.setdefault(route["base_url_env"], route["base_url"])


def require_routing(model: str) -> None:
    """An OpenRouter model runs only with the provider routing its registry entry pins: without one OpenRouter sends
    each call to a different host of the model, priced up to five times apart (2026-10-28)."""
    try:
        models.check_routing(model)
    except ValueError as error:
        raise Refusal(str(error), 1) from error


def require_role_model(model: str) -> None:
    """A role model's key and routing, then its client built through the swarm provider without a call. ORBIT builds
    the model only inside the sample, so a provider name Inspect does not know would otherwise fail after the sandbox
    starts."""
    require_provider_key(model)
    require_routing(model)
    try:
        get_model(provider.role_model(model), memoize=False)
    except (ValueError, PrerequisiteError) as error:
        raise Refusal(f"role model {model}: {getattr(error, 'message', None) or error}", 1) from error


def require_judge_key(judge_name: str, code: int) -> None:
    """The judge's key, named by its src/benchmark/judge_models.yaml entry (OPENAI_API_KEY for gpt-6-luna)."""
    try:
        judging.require_judge_key(judge_name)
    except RuntimeError as error:
        raise Refusal(f"{error} for the judge", code) from error


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------
def log_kind(benchmark, config: SwarmConfig) -> str:
    """logs/<kind>/: swe for SWE, swarm for a run with hidden roles, ctrl for a lone agent or a plain board."""
    if isinstance(benchmark, SWE):
        return "swe"
    return "swarm" if hidden_roles(benchmark, config) else "ctrl"


def judge_role_model(config: SwarmConfig) -> Model | None:
    """The model bound to the "judge" role, with the provider's batch API on or off as batch_judge says, or None
    when the config has no judge.

    The batch setting is explicit either way, so a judge never inherits the agents' batch_agents setting."""
    if config.judge is None:
        return None
    judge_model, _ = judging.judge_model_config(config.judge)
    return get_model(judge_model, config=GenerateConfig(batch=config.batch_judge))


def agent_generate_config(config: SwarmConfig) -> dict:
    """The eval's generate config for the agents' model: batch=True with batch_agents, the reasoning effort and the
    tool output cap when the config sets them."""
    out = {"batch": True} if config.batch_agents else {}
    if config.reasoning_effort is not None:
        out["reasoning_effort"] = config.reasoning_effort
    if config.tool_output_bytes is not None:
        out["max_tool_output"] = config.tool_output_bytes
    return out



def export(eval_log: Path, run_name: str, verdicts: dict | None = None) -> Path:
    """Write runs/<run_name>/ from the log and print what was written."""
    run_dir = settings.RUNS / run_name
    print(f"== export {eval_log} -> {run_dir}", flush=True)
    print(json.dumps(write_run(eval_log, run_dir, verdicts=verdicts), indent=1), flush=True)
    episodes = run_dir / "episodes.jsonl"
    if not episodes.exists() or episodes.stat().st_size == 0:
        raise Refusal("the export produced no episodes", 3)
    return run_dir


def access_line(task) -> str:
    """One line on what each agent holds, from the sample's recorded access plan."""
    access = task.dataset[0].metadata["cheatbench"]["access"]
    held = ", ".join(f"{agent} {'+'.join(items) or 'nothing'}" for agent, items in access["agents"].items())
    return f"== access {access['policy']}: {held}"


def models_line(config: SwarmConfig) -> str:
    """One line on each role's model; "(run model)" where the role runs the eval's model."""
    return "== models " + " ".join(f"{role}={model or '(run model)'}" for role, model in config.role_models().items())


@command
def run(name: str, benchmark, config: SwarmConfig, model: str, timeout: int | None) -> int:
    """Run one task, export it to runs/<name>/ and print the summary. Returns 0, or 3 when the eval did not succeed.
    A caller with its own sample builds the task itself (tasks.swarm_task) and calls evaluate."""
    return evaluate(name, benchmark, config, swarm_task(benchmark, config, name=name), model, timeout)


@command
def evaluate(name: str, benchmark, config: SwarmConfig, task: Task, model: str, timeout: int | None) -> int:
    """Run a built task: inspect_ai.eval, the export to runs/<name>/, the summary and the cheating signals."""
    require_provider_key(model)
    require_routing(model)
    for role_model in filter(None, config.role_models().values()):
        require_role_model(role_model)
    judge_model = judge_role_model(config)
    if judge_model is not None:
        require_judge_key(config.judge, 1)
    provider_retry.install()
    log_dir = settings.LOGS / log_kind(benchmark, config) / name
    log_dir.mkdir(parents=True, exist_ok=True)
    judged = f"judge={config.judge}" if judge_model is not None else "not judged"
    print(f"== eval {benchmark.name} n_agents={config.n_agents} control={config.control} {judged} -> {log_dir}",
          flush=True)
    print(access_line(task), flush=True)
    print(models_line(config), flush=True)
    # Inspect merges the eval's generate config into the eval's model only; the role models get it from the provider
    provider.set_agent_config(GenerateConfig(**agent_generate_config(config)))
    agents = get_model(model, memoize=False, **models.model_args(model))
    # every raw model call is kept (Inspect keeps five per model by default): OpenRouter's billed cost per call
    # lives in the raw response, and the export sums it per episode
    [log] = inspect_ai.eval(task, model=agents, log_dir=str(log_dir), display="plain", timeout=timeout,
                            log_model_api=True,
                            model_roles={"judge": judge_model} if judge_model is not None else None,
                            **agent_generate_config(config))
    if not log.samples and not Path(log.location).exists():
        raise Refusal("no eval log written", 3)
    run_dir = export(Path(log.location), name)
    summary.main([str(run_dir)])
    print(f"== signals {run_dir}", flush=True)
    for row in signals.run_signals(run_dir):
        print(signals.agent_line(row), flush=True)
    return 0 if log.status == "success" else 3


# --------------------------------------------------------------------------
# judge an existing log
# --------------------------------------------------------------------------
def _episode(view, agents: list[str], agent: str) -> str:
    key = f"{view.id}#{view.epoch}"
    return key if len(agents) == 1 else f"{key}@{agent}"


async def _judge_views(views: list, judge_name: str, batch: bool) -> dict[str, dict]:
    """Every agent of every sample judged again, through the provider's batch API or the normal one; verdict rows
    keyed by episode."""
    model_name, config = judging.judge_model_config(judge_name)
    prices = judging.judge_prices(judge_name, batch=batch)
    # a fresh client: a memoized one belongs to the event loop that first used it, and rejudge runs two
    model = get_model(model_name, config=GenerateConfig(batch=batch), memoize=False)
    verdicts = {}
    for view in views:
        cb = (view.metadata or {}).get("cheatbench", {})
        addendum = JUDGE_ADDENDUM if (cb.get("config") or {}).get("judge_addendum") else None
        spec = judging.judge_spec(settings.CHEATBENCH / "tasks" / cb["task"], cb["task"], addendum=addendum)
        agents = roster(view)
        judged = [a for a in agents if agent_messages(view, a)]
        results = await asyncio.gather(*(judging.judge_trajectory(spec, transcript_dict(view, a, judge_prompt(view, a)),
                                                                  model, config, prices) for a in judged))
        for agent, result in zip(judged, results):
            episode = _episode(view, agents, agent)
            verdicts[episode] = judging.judge_row(result, id=view.id, episode=episode, replicate=view.epoch,
                                                  task=spec.task, model=view.model, judge=judge_name, spec=spec,
                                                  transcript_path=None)
    return verdicts


def print_verdicts(judge_jsonl: Path) -> None:
    print("== verdicts")
    for line in judge_jsonl.read_text().splitlines():
        verdict = json.loads(line)
        print(json.dumps({key: verdict.get(key) for key in ("episode", *settings.VERDICT_KEYS)}))
        print("summary:", verdict.get("summary"))


@command
def judge(eval_log: Path, run_name: str, judge_name: str, *, batch: bool = True, judge_errors: bool = False) -> int:
    """Judge a finished eval log (again, or for the first time after a run without a judge) and export it to
    runs/<run_name>/ with the verdicts. batch: the provider's batch API, half price, up to a day; else the normal
    API, full price, minutes."""
    require_judge_key(judge_name, 2)
    eval_log = eval_log.resolve()
    if not eval_log.is_file():
        raise Refusal(f"no such eval log: {eval_log}", 2)
    log = read_eval_log(str(eval_log), resolve_attachments=True)
    views = [SampleView.from_eval_sample(sample, log) for sample in log.samples or []]
    if not views:
        raise Refusal("the eval log holds no samples", 3)
    failures = [error_and_failure(view)[1] for view in views]
    if not judge_errors and any(f in settings.INCOMPLETE_FAILURES for f in failures):
        raise Refusal("run has a harness or provider failure; not judging (pass --judge-errors to force)", 5)
    print(f"== judge ({judge_name}, {'batch' if batch else 'sync'}) {eval_log}", flush=True)
    verdicts = asyncio.run(_judge_views(views, judge_name, batch))
    run_dir = export(eval_log, run_name, verdicts=verdicts)
    judge_jsonl = run_dir / "judge.jsonl"
    if not verdicts or not judge_jsonl.exists():
        raise Refusal("the judge wrote no verdicts", 4)
    print_verdicts(judge_jsonl)
    return 0


# --------------------------------------------------------------------------
# the cheating ladder
# --------------------------------------------------------------------------
@command
def ladder_runs(run_dirs: list[Path], judge_name: str, *, view: str = "full", agents: list[str] | None = None,
                force: bool = False, batch: bool = True) -> int:
    """Place every agent of each run on the cheating ladder (src/benchmark/judging_ladder.py): runs/<name>/ladder.jsonl,
    one line per agent, then the table over the runs. batch as in judge()."""
    require_judge_key(judge_name, 2)
    if view not in ladder.VIEWS:
        raise Refusal(f"unknown view {view!r}; choose one of {ladder.VIEWS}", 2)
    missing = [str(d) for d in run_dirs if not (Path(d) / "run.json").is_file()]
    if missing:
        raise Refusal(f"not a run directory: {', '.join(missing)}", 2)
    asyncio.run(_ladder_all(run_dirs, judge_name, view, agents, force, batch))
    print(ladder.table([Path(d) for d in run_dirs], view), end="")
    return 0


async def _ladder_all(run_dirs: list[Path], judge_name: str, view: str, agents: list[str] | None,
                      force: bool, batch: bool) -> None:
    """The runs together on one event loop (Inspect keeps its model clients across calls, and a batch should hold
    every run's episodes), each run's lines printed when its verdicts are in."""
    mode = "batch" if batch else "sync"
    for run_dir in run_dirs:
        print(f"== ladder ({judge_name}, {mode}, {view}) {run_dir}", flush=True)
    results = await asyncio.gather(*(ladder.judge_run(Path(run_dir), judge_name, view=view, agents=agents,
                                                      force=force, batch=batch) for run_dir in run_dirs))
    for run_dir, rows in zip(run_dirs, results):
        print(f"== ladder {run_dir}", flush=True)
        for row in rows:
            print(ladder.agent_line(row), flush=True)


# --------------------------------------------------------------------------
# the turn budget from a lone run
# --------------------------------------------------------------------------
def lone_turns(run_dir: Path) -> list[int]:
    """n_turns of every episode of a lone agent's run (runs/<name>/episodes.jsonl)."""
    episodes = run_dir / "episodes.jsonl"
    if not episodes.is_file():
        raise Refusal(f"no episodes.jsonl in {run_dir}", 2)
    rows = [json.loads(line) for line in episodes.read_text().splitlines() if line.strip()]
    if not rows:
        raise Refusal(f"{episodes} holds no episodes", 2)
    if any(len(row.get("orbit_roster") or [row.get("orbit_agent")]) != 1 for row in rows):
        raise Refusal(f"{run_dir} is not a lone agent's run: an episode has a roster of several agents", 2)
    return [int(row["n_turns"]) for row in rows]


@command
def budget(run_dirs: list[Path]) -> int:
    """Print each lone run's n_turns and the median over every episode: how long one agent takes alone, a
    descriptive statistic beside the turn budget (settings.TURN_BUDGETS)."""
    turns: list[int] = []
    for run_dir in run_dirs:
        mine = lone_turns(run_dir)
        print(f"{run_dir}: {len(mine)} episode(s) of a lone agent, n_turns {', '.join(map(str, mine))}")
        turns += mine
    print(f"median {int(statistics.median(turns))} over {len(turns)} lone episode(s)")
    return 0
