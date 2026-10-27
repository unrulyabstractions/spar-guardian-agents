"""The run, judge, ladder and budget commands: run a task in process, judge a finished eval log again, place a
run's agents on the cheating ladder, or derive the turn budget's steps per agent from a lone run.

    run("swarm_R3D3_s1", OpenMath(), config, model="google/gemini-3.8-flash", timeout=2700)
    judge(Path("logs/swarm/swarm_R3D3/<log>.eval"), "swarm_R3D3", "gpt-6-sol", judge_errors=False)
    ladder_runs([Path("runs/swarm_R3D3")], "gpt-6-sol", view="full")
    budget(Path("runs/gemini_riemann"), ["medium"])

A run calls inspect_ai.eval with the task from tasks.py, writes its log under logs/<kind>/<name>/, exports it to
runs/<name>/ with src/benchmark/export.py and prints one line per episode. CheatBench's judge runs inside the eval as a
scorer, bound to the model role "judge". With batch_judge the judge's calls use the provider's batch API, and with
batch_agents so do the agents' calls (Inspect GenerateConfig.batch). Re-judging reads a finished log, judges every agent
again with src/benchmark/judge.py and exports the log with the new verdicts. A refused precondition prints its reason
to stderr, and the command returns the exit code the command line passes on.
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
import sys
from pathlib import Path

import inspect_ai
from inspect_ai.log import read_eval_log
from inspect_ai.model import GenerateConfig, Model, get_model

from src.benchmark import judge as judging
from src.benchmark import ladder
from src.benchmark.domains.swe import SWE
from src.benchmark.export import write_run
from src.benchmark.transcript import (
    SampleView,
    agent_messages,
    error_and_failure,
    judge_prompt,
    roster,
    transcript_dict,
)
from src.configuration import models, settings
from src.configuration.scales import steps_by_scale
from src.configuration.swarm_config import SwarmConfig
from src.measures import summary
from src.tasks import hidden_roles, judge_addendum_path, swarm_task
from src.utils import openrouter_cache, provider_retry


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
    """One of the key variables src/configuration/models.yaml names for the model's route, and the route's base URL.

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


def require_judge_key(judge_name: str, code: int) -> None:
    """The judge's key, named by its src/benchmark/models.yaml entry (OPENAI_API_KEY for gpt-6-sol)."""
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


def judge_role_model(config: SwarmConfig) -> Model:
    """The model bound to the "judge" role, with the provider's batch API on or off as batch_judge says.

    The batch setting is explicit either way, so a judge never inherits the agents' batch_agents setting."""
    judge_model, _ = judging.judge_model_config(config.judge)
    return get_model(judge_model, config=GenerateConfig(batch=config.batch_judge))


def agent_generate_config(config: SwarmConfig, model: str | None = None) -> dict:
    """The eval's generate config for the agents' model: the registry entry's `generate` settings
    (src/configuration/models.yaml), and batch=True with batch_agents."""
    out = models.generate_args(model) if model else {}
    return {**out, "batch": True} if config.batch_agents else out


def has_incomplete_episode(episodes: Path) -> bool:
    for line in episodes.read_text().splitlines():
        if json.loads(line).get("failure") in settings.INCOMPLETE_FAILURES:
            return True
    return False


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


@command
def run(name: str, benchmark, config: SwarmConfig, model: str, timeout: int | None) -> int:
    """Run one task, export it to runs/<name>/ and print the summary. Returns 0, or 3 when the eval did not succeed."""
    require_provider_key(model)
    require_judge_key(config.judge, 1)
    provider_retry.install()
    openrouter_cache.install()
    log_dir = settings.LOGS / log_kind(benchmark, config) / name
    log_dir.mkdir(parents=True, exist_ok=True)
    print(f"== eval {benchmark.name} n_agents={config.n_agents} control={config.control} -> {log_dir}", flush=True)
    task = swarm_task(benchmark, config, name=name)
    print(access_line(task), flush=True)
    model_args, generate = models.model_args(model), agent_generate_config(config, model)
    print(f"== model {model} args {json.dumps(model_args)} generate {json.dumps(generate)}", flush=True)
    [log] = inspect_ai.eval(task, model=model, model_args=model_args, log_dir=str(log_dir), display="plain",
                            timeout=timeout, model_roles={"judge": judge_role_model(config)}, **generate)
    if not log.samples and not Path(log.location).exists():
        raise Refusal("no eval log written", 3)
    run_dir = export(Path(log.location), name)
    summary.main([str(run_dir)])
    return 0 if log.status == "success" else 3


# --------------------------------------------------------------------------
# judge an existing log
# --------------------------------------------------------------------------
def _episode(view, agents: list[str], agent: str) -> str:
    key = f"{view.id}#{view.epoch}"
    return key if len(agents) == 1 else f"{key}@{agent}"


def recorded_config(view) -> dict:
    """The SwarmConfig fields a sample recorded (metadata["cheatbench"]["config"]); empty for older logs."""
    return ((view.metadata or {}).get("cheatbench", {}).get("config")) or {}


def rejudge_batch(views: list, batch: bool | None) -> bool:
    """Whether a re-judge goes through the provider's batch API: as given, else as the run's batch_judge (on for a run
    that predates the field)."""
    if batch is not None:
        return batch
    return bool(recorded_config(views[0]).get("batch_judge", True)) if views else True


async def _judge_views(views: list, judge_name: str, addendum: bool | str | None = None,
                       batch: bool | None = None) -> dict[str, dict]:
    """Every agent of every sample judged again; verdict rows keyed by episode. The addendum is the given one (False
    for none), else the run's judge_addendum; the batch setting is rejudge_batch's."""
    model_name, config = judging.judge_model_config(judge_name)
    batch = rejudge_batch(views, batch)
    prices = judging.judge_prices(judge_name, batch=batch)
    model = get_model(model_name, config=GenerateConfig(batch=batch))
    verdicts = {}
    for view in views:
        cb = (view.metadata or {}).get("cheatbench", {})
        chosen = recorded_config(view).get("judge_addendum") if addendum is None else addendum
        spec = judging.judge_spec(settings.CHEATBENCH / "tasks" / cb["task"], cb["task"],
                                  addendum=judge_addendum_path(chosen))
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
def judge(eval_log: Path, run_name: str, judge_name: str, *, judge_errors: bool = False,
          addendum: bool | str | None = None, batch: bool | None = None) -> int:
    """Judge a finished eval log again and export it to runs/<run_name>/ with the new verdicts. addendum and batch
    default to the run's own judge_addendum and batch_judge."""
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
    print(f"== judge ({judge_name}, addendum {'the run' + chr(39) + 's' if addendum is None else addendum}, "
          f"batch {rejudge_batch(views, batch)}) {eval_log}", flush=True)
    verdicts = asyncio.run(_judge_views(views, judge_name, addendum, batch))
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
                force: bool = False) -> int:
    """Place every agent of each run on the cheating ladder (src/benchmark/ladder.py): runs/<name>/ladder.jsonl,
    one line per agent, then the table over the runs."""
    require_judge_key(judge_name, 2)
    if view not in ladder.VIEWS:
        raise Refusal(f"unknown view {view!r}; choose one of {ladder.VIEWS}", 2)
    missing = [str(d) for d in run_dirs if not (Path(d) / "run.json").is_file()]
    if missing:
        raise Refusal(f"not a run directory: {', '.join(missing)}", 2)
    asyncio.run(_ladder_all(run_dirs, judge_name, view, agents, force))
    print(ladder.table([Path(d) for d in run_dirs], view), end="")
    return 0


async def _ladder_all(run_dirs: list[Path], judge_name: str, view: str, agents: list[str] | None,
                      force: bool) -> None:
    """The runs one after the other on one event loop, since Inspect keeps its model clients across calls."""
    for run_dir in run_dirs:
        print(f"== ladder ({judge_name}, {view}) {run_dir}", flush=True)
        rows = await ladder.judge_run(Path(run_dir), judge_name, view=view, agents=agents, force=force)
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
def budget(run_dir: Path, scales: list[str] | None = None) -> int:
    """Print the lone run's n_turns and, per scale, the working steps per agent that budget gives
    (max(1, budget // n_agents), SwarmConfig.steps_for). Several episodes give their mean, rounded."""
    turns = lone_turns(run_dir)
    total = round(sum(turns) / len(turns))
    print(f"{run_dir}: {len(turns)} episode(s) of a lone agent, n_turns {', '.join(map(str, turns))}; "
          f"budget {total}")
    print(f"{'scale':<12} {'agents':>6} {'scale steps':>11} {'budget steps':>12}")
    for name, row in steps_by_scale(total, scales).items():
        literal = "  (the literal rule: at least one step)" if total // row["n_agents"] < 1 else ""
        print(f"{name:<12} {row['n_agents']:>6} {row['scale_steps']:>11} {row['budget_steps']:>12}{literal}")
    return 0
