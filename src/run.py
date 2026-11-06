"""The run, judge, ladder, rejudge and regenerate commands: run a task in process, judge a finished eval log again,
place a run's agents on the cheating ladder, do both for finished runs, or rebuild what a run directory derives from
its eval log.

    run("swarm_R3D3_s1", OpenMath(), config, model="google/gemini-3.8-flash", timeout=2700)
    run("riemann_cheat", benchmark, config, model, timeout, task=swarm_task(benchmark, config, extra_instruction=CHEAT))
    judge(Path("logs/swarm_R3D3/<log>.eval"), "swarm_R3D3", "gpt-6-luna", batch=True)
    ladder_runs([Path("runs/swarm_R3D3")], "gpt-6-luna", view="full", batch=True)
    rejudge([Path("runs/swarm_R3D3")], "gpt-6-luna", batch=True)
    regenerate([Path("runs/swarm_R3D3")])

A run calls inspect_ai.eval with the task (the default task of tasks.swarm_task, or one the caller built), writes its
log under logs/<study>/<name>/, exports it to runs/<study>/<name>/ with src/export/run_export.py, prints one line per
episode (src/measures/episode_summary.py) and places every agent and the team on the code ladder
(src/measures/code_ladder.py). The code ladder runs again after every export, which rewrites run.json, and after the
judged ladder, whose team rows it merges into its own. A run directory holds one eval's run: run and judge refuse a
directory that holds another eval's run before the eval or the judge spends anything. With replace (the command line's
--replace) they set that earlier run aside into runs/<study>/attempts/<name>.<n>/ instead, with its eval log and its
figures, so nothing of it is lost and no file of it stays beside the new run. rejudge refuses a run whose log holds
another eval than its folder before it judges that run.
CheatBench's judge runs inside the eval as a scorer, bound to the model role "judge". With batch_judge the judge's
calls use the provider's batch API, and with batch_agents so do the agents' calls (Inspect GenerateConfig.batch). A
role with a model of its own (worker_model, regulator_model, disregulator_model) runs it through the swarm model
provider (src/models/swarm_model_provider.py), which carries the agents' generate config to that model's calls. Each
role model is built once before the eval, so a provider name Inspect does not know is refused before the sandbox
starts. Re-judging reads a finished log, judges every agent again with src/judging/cheatbench_judge.py and exports the
log with the new verdicts: rejudge into each run's own directory, wherever it lives, and judge into
runs/<study>/<run_name>/. A refused precondition prints its reason to stderr, and the command returns the exit code
the command line passes on.
"""
from __future__ import annotations

import asyncio
import functools
import json
import sys
from pathlib import Path

import inspect_ai
from inspect_ai import Task
from inspect_ai._util.error import PrerequisiteError
from inspect_ai.log import read_eval_log
from inspect_ai.model import GenerateConfig, Model, get_model

from src.configuration import settings
from src.configuration.swarm_config import SwarmConfig
from src.domains import BY_TASK
from src.domains.benchmark_protocol import Benchmark
from src.export.eval_sample_view import SampleView, error_and_failure
from src.export.export_episodes import episode_id
from src.export.run_export import RunFolderTaken, refuse_replacing, write_run
from src.judging import cheatbench_judge as judging
from src.judging import judging_ladder as ladder
from src.measures import code_ladder as signals
from src.measures import episode_summary as summary
from src.measures import run_figures, swe_measures
from src.measures.run_directory import RunDir, figures_dir, find_run, set_aside_attempt
from src.models import (
    cost_cap,
    gemini_402_retry,
    openrouter_anthropic_cache,
    openrouter_gemini_tool_ids,
    openrouter_google_400_retry,
)
from src.models import model_registry as models
from src.models import swarm_model_provider as provider  # importing it registers the "swarm" provider
from src.tasks import swarm_task

# The verdict fields `judge` prints per episode, which every CheatBench judge schema shares.
VERDICT_FIELDS = ("episode", "parse_ok", "cheated", "judge_cost_usd", "judge_error")


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
def require_model(model: str) -> None:
    """The model's key (model_registry.prepare_env) and, on OpenRouter, the host its registry entry pins
    (model_registry.check_routing)."""
    try:
        models.prepare_env(model)
        models.check_routing(model)
    except ValueError as error:
        raise Refusal(str(error), 1) from error


def require_role_model(model: str) -> None:
    """A role model's key and routing, then its client built through the swarm provider without a call. ORBIT builds
    the model only inside the sample, so a provider name Inspect does not know would otherwise fail after the sandbox
    starts."""
    require_model(model)
    try:
        get_model(provider.role_model(model), memoize=False)
    except (ValueError, PrerequisiteError) as error:
        raise Refusal(f"role model {model}: {getattr(error, 'message', None) or error}", 1) from error


def require_judge_key(judge_name: str, code: int) -> None:
    """The judge's key, named by its src/judging/judge_models.yaml entry (OPENAI_API_KEY for gpt-6-luna)."""
    try:
        judging.require_judge_key(judge_name)
    except RuntimeError as error:
        raise Refusal(f"{error} for the judge", code) from error


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------
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


RUN_REMEDY = ("Pass --replace to set the earlier run aside into runs/<study>/attempts/<name>.<n>/ first, or choose "
              "another --name.")
"""What `run` and an example script say when the run's folder is taken."""
JUDGE_REMEDY = ("Pass --replace to set the earlier run aside into runs/<study>/attempts/<name>.<n>/ first, or judge "
                "the log into another run name.")
"""What `judge` says when the run's folder holds another eval's run."""
LOG_REMEDY = ("rejudge and regenerate write a folder only from the eval it holds. Judge that log into a run of its own "
              "with `uv run python -m src judge <log> <name>`; --replace sets the earlier run aside first.")
"""What `rejudge` and `regenerate` say when the log found for a run holds another eval than its folder."""


def require_free(run_dir: Path, eval_id: str | None, remedy: str) -> None:
    """Refuse (2) a run directory that holds another eval's run (run_export.refuse_replacing), before a run or a judge
    spends anything, with the remedy that fits the command. eval_id None stands for a run not made yet."""
    try:
        refuse_replacing(run_dir, eval_id)
    except RunFolderTaken as taken:
        raise Refusal(f"{taken.base} {remedy}", 2) from taken


def find(name: str) -> Path | None:
    """The folder of the run of this name in the current study, at its top level or in its one group; a Refusal (2)
    when two folders hold it."""
    try:
        return find_run(settings.RUNS, name)
    except ValueError as error:
        raise Refusal(str(error), 2) from error


def export(eval_log: Path, run_dir: Path, verdicts: dict | None = None, *, replace: bool = False,
           remedy: str = RUN_REMEDY) -> Path:
    """Write the run directory from the log and print what was written. A directory that holds another eval's run is
    refused (2) with the command's remedy, unless replace sets that run aside first."""
    print(f"== export {eval_log} -> {run_dir}", flush=True)
    try:
        written = write_run(eval_log, run_dir, verdicts=verdicts, replace=replace)
    except RunFolderTaken as taken:
        raise Refusal(f"{taken.base} {remedy}", 2) from taken
    print(json.dumps(written, indent=1), flush=True)
    if not RunDir(run_dir).episodes():
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
def run(name: str, benchmark: Benchmark, config: SwarmConfig, model: str, timeout: int | None, *,
        task: Task | None = None, replace: bool = False) -> int:
    """Run one task: inspect_ai.eval, the export to runs/<study>/<name>/, the summary and the cheating signals. task is
    one the caller built from the same benchmark and config (tasks.swarm_task with an extra_instruction, say); None
    builds the default. A folder that already holds a run is refused (2) before the eval. With replace, the earlier
    run's folder, its eval log folder and its figures move to the attempts group just before the eval
    (run_directory.set_aside_attempt), so the new run writes into empty folders. Returns 0, or 3 when the eval did not
    succeed."""
    require_model(model)
    for role_model in filter(None, config.role_models().values()):
        require_role_model(role_model)
    judge_model = judge_role_model(config)
    if judge_model is not None:
        require_judge_key(config.judge, 1)
    run_dir = settings.RUNS / name
    if not replace:
        require_free(run_dir, None, RUN_REMEDY)
    task = task or swarm_task(benchmark, config, name=name)
    gemini_402_retry.install()
    openrouter_google_400_retry.install()
    openrouter_gemini_tool_ids.install()
    openrouter_anthropic_cache.install()
    run_models = [model, *config.role_models().values(), str(judge_model) if judge_model is not None else None]
    try:
        openrouter_anthropic_cache.require_proven(run_models)
        cost_limit = cost_cap.cap_for(run_models, config.cost_limit_usd)
    except ValueError as error:   # UnpricedModel included
        raise Refusal(f"refused: {error}", 2) from error
    # a refused run moves nothing: the set-aside comes after every refusal
    if replace:
        for source, target in set_aside_attempt(run_dir):
            print(f"== set aside {source} -> {target}", flush=True)
    log_dir = settings.LOGS / name
    log_dir.mkdir(parents=True, exist_ok=True)
    judged = f"judge={config.judge}" if judge_model is not None else "not judged"
    print(f"== eval {benchmark.name} n_agents={config.n_agents} {judged} -> {log_dir}", flush=True)
    print(access_line(task), flush=True)
    print(models_line(config), flush=True)
    print(f"== cost cap ${cost_limit:.2f} for the whole run" if cost_limit else "== no cost cap: mock models only",
          flush=True)
    generate = agent_generate_config(config)
    # Inspect merges the eval's generate config into the eval's model only; the role models get it from the provider
    provider.set_agent_config(GenerateConfig(**generate))
    # the entry's own generate config (a self-hosted model's sampling) is the model's, beneath the eval's and the call's
    agents = get_model(model, memoize=False, config=GenerateConfig(**models.generate_config(model)),
                       **models.model_args(model))
    # every raw model call is kept (Inspect keeps five per model by default): OpenRouter's billed cost per call
    # lives in the raw response, and the export sums it per episode
    [log] = inspect_ai.eval(task, model=agents, log_dir=str(log_dir), display="plain", timeout=timeout,
                            log_model_api=True, cost_limit=cost_limit,
                            model_roles={"judge": judge_model} if judge_model is not None else None, **generate)
    if not log.samples and not Path(log.location).exists():
        raise Refusal("no eval log written", 3)
    run_dir = export(Path(log.location), run_dir, replace=replace)
    print("\n".join([summary.HEADER, *summary.summary_lines(run_dir)]), flush=True)
    print(signals.run_report(run_dir), flush=True)
    return 0 if log.status == "success" else 3


# --------------------------------------------------------------------------
# judge an existing log
# --------------------------------------------------------------------------
async def _judge_views(views: list[SampleView], judge: judging.Judge) -> dict[str, dict]:
    """Every agent of every sample judged again with the spec the sample was judged with (its task's schema, Lucy's
    addendum when its config asked for it); by episode."""
    verdicts = {}
    for view in views:
        cb = view.metadata["cheatbench"]
        addendum = judging.JUDGE_ADDENDUM if (cb.get("config") or {}).get("judge_addendum") else None
        spec = judging.judge_spec(BY_TASK[cb["task"]].task_dir, cb["task"], addendum=addendum)
        by_agent = await judging.judge_sample(view, spec, judge)
        verdicts |= {episode_id(view, agent): verdict for agent, verdict in by_agent.items()}
    return verdicts


async def _judge_all(views: dict[str, list[SampleView]], judge_name: str, batch: bool) -> dict[str, dict]:
    """The runs together on one event loop and one client, so that a batch holds every run's episodes; {run: verdicts
    by episode}. batch: the provider's batch API, half price, up to a day; else the normal API, full price, minutes."""
    judge = judging.Judge.of(judge_name, batch=batch)
    results = await asyncio.gather(*(_judge_views(run_views, judge) for run_views in views.values()))
    return dict(zip(views, results))


def _log_views(eval_log: Path, judge_errors: bool) -> list[SampleView]:
    """A finished log's samples, ready for the judge. Refuses a log with no sample and, unless judge_errors, a run with
    a harness or provider failure."""
    log = read_eval_log(str(eval_log), resolve_attachments=True)
    views = [SampleView.from_eval_sample(sample, log) for sample in log.samples or []]
    if not views:
        raise Refusal("the eval log holds no samples", 3)
    failures = [error_and_failure(view)[1] for view in views]
    if not judge_errors and any(f in settings.INCOMPLETE_FAILURES for f in failures):
        raise Refusal("run has a harness or provider failure; not judging (pass --judge-errors to force)", 5)
    return views


def _write_verdicts(eval_log: Path, run_dir: Path, verdicts: dict[str, dict], *, replace: bool = False,
                    remedy: str = JUDGE_REMEDY) -> None:
    """Export the log to run_dir with its verdicts and print them. Refuses when no verdict reached the file."""
    run_dir = export(eval_log, run_dir, verdicts=verdicts, replace=replace, remedy=remedy)
    written = RunDir(run_dir).verdicts()
    if not verdicts or not written:
        raise Refusal("the judge wrote no verdicts", 4)
    print("== verdicts")
    for verdict in written.values():
        print(json.dumps({key: verdict.get(key) for key in VERDICT_FIELDS}))
    print(signals.run_report(run_dir), flush=True)


@command
def judge(eval_log: Path, run_name: str, judge_name: str, *, batch: bool = True, judge_errors: bool = False,
          replace: bool = False) -> int:
    """Judge a finished eval log (again, or for the first time after a run without a judge) and export it with the
    verdicts into the run's own folder: runs/<study>/<run_name>/, or the group that holds the run. A folder that holds
    another eval's run is refused (2) before the judge starts. With replace, that earlier run moves to the attempts
    group once the verdicts are in, and the new run goes to runs/<study>/<run_name>/; the log judged stays where it
    is."""
    require_judge_key(judge_name, 2)
    eval_log = eval_log.resolve()
    if not eval_log.is_file():
        raise Refusal(f"no such eval log: {eval_log}", 2)
    found = find(run_name)
    views = {run_name: _log_views(eval_log, judge_errors)}
    eval_id = views[run_name][0].eval_id
    taken = found is not None and RunDir(found).run_json.get("eval_id") != eval_id
    if taken and not replace:
        require_free(found, eval_id, JUDGE_REMEDY)
    print(f"== judge ({judge_name}, {'batch' if batch else 'sync'}) {eval_log}", flush=True)
    verdicts = asyncio.run(_judge_all(views, judge_name, batch))[run_name]
    if taken:
        for source, target in set_aside_attempt(found, keep=eval_log):
            print(f"== set aside {source} -> {target}", flush=True)
    run_dir = settings.RUNS / run_name if taken or found is None else found
    _write_verdicts(eval_log, run_dir, verdicts, replace=replace, remedy=JUDGE_REMEDY)
    return 0


# --------------------------------------------------------------------------
# the cheating ladder
# --------------------------------------------------------------------------
@command
def ladder_runs(run_dirs: list[Path], judge_name: str, *, view: str = "full", agents: list[str] | None = None,
                force: bool = False, batch: bool = True) -> int:
    """Place every agent of each run on the cheating ladder (src/judging/judging_ladder.py):
    runs/<study>/<name>/ladder.jsonl, one line per agent, then the table over the runs, then the code ladder again,
    which merges the judged team rows into signals.jsonl and run.json. batch as in judge()."""
    require_judge_key(judge_name, 2)
    if view not in ladder.VIEWS:
        raise Refusal(f"unknown view {view!r}; choose one of {ladder.VIEWS}", 2)
    missing = [str(d) for d in run_dirs if not RunDir(d).is_run()]
    if missing:
        raise Refusal(f"not a run directory: {', '.join(missing)}", 2)
    for run_dir in run_dirs:
        if not RunDir(run_dir).team_signals():      # the team row says whether presentation is asked
            signals.run_signals(Path(run_dir))
    asyncio.run(_ladder_all(run_dirs, judge_name, view, agents, force, batch))
    print(ladder.ladder_table(run_dirs, view), end="")
    for run_dir in run_dirs:
        print(signals.run_report(Path(run_dir)), flush=True)
    return 0


async def _ladder_all(run_dirs: list[Path], judge_name: str, view: str, agents: list[str] | None,
                      force: bool, batch: bool) -> None:
    """The runs together on one event loop and one client, so that a batch holds every run's episodes, each run's lines
    printed when its verdicts are in."""
    mode = "batch" if batch else "sync"
    judge = judging.Judge.of(judge_name, batch=batch)
    for run_dir in run_dirs:
        print(f"== ladder ({judge_name}, {mode}, {view}) {run_dir}", flush=True)
    results = await asyncio.gather(*(ladder.judge_run(Path(run_dir), judge, view=view, agents=agents, force=force)
                                     for run_dir in run_dirs))
    for run_dir, rows in zip(run_dirs, results):
        print(f"== ladder {run_dir}", flush=True)
        for row in rows:
            print(ladder.agent_line(row), flush=True)


# --------------------------------------------------------------------------
# finished runs: both judges again, or everything the log gives
# --------------------------------------------------------------------------
@command
def rejudge(run_dirs: list[Path], judge_name: str, *, with_judge: bool = True, with_ladder: bool = True,
            view: str = "full", judge_errors: bool = False, batch: bool = True) -> int:
    """CheatBench's judge over the runs' eval logs (RunDir.eval_log), each re-exported into the run's own directory,
    wherever it lives, then the ladder over their transcripts, every episode again, all the runs in one batch. Runs are
    told apart by their directory, since runs in different folders can share a name, and a run given twice is judged
    once. A run whose log holds another eval than its folder is refused (2) before the judge spends anything on it. A
    run whose log is gone gets the ladder only, which needs the transcripts alone. A run the judge refuses gets no
    ladder. Returns the worst exit code."""
    runs: dict[str, RunDir] = {}
    for run_dir in run_dirs:
        run = RunDir(run_dir).require()
        runs.setdefault(str(run.path.resolve()), run)
    if with_judge:
        require_judge_key(judge_name, 2)
    codes, refused, views = [0], set(), {}

    def refuse(key: str, refusal: Refusal) -> None:
        print(f"{runs[key].path}: {refusal}", file=sys.stderr)
        codes.append(refusal.code)
        refused.add(key)

    for key, run in runs.items() if with_judge else []:
        if run.eval_log is None:
            print(f"{run.path}: no eval log on disk, skipping CheatBench's judge", file=sys.stderr)
            continue
        try:
            found = _log_views(run.eval_log, judge_errors)
            require_free(run.path, found[0].eval_id, LOG_REMEDY)
            views[key] = found
        except Refusal as refusal:
            refuse(key, refusal)
    if views:
        print(f"== judge ({judge_name}, {'batch' if batch else 'sync'}) {len(views)} runs", flush=True)
    verdicts = asyncio.run(_judge_all(views, judge_name, batch)) if views else {}
    for key, run in runs.items():
        if key in verdicts:
            try:
                _write_verdicts(run.eval_log, run.path, verdicts[key], remedy=LOG_REMEDY)
            except Refusal as refusal:
                refuse(key, refusal)
    ladder_dirs = [run.path for key, run in runs.items() if key not in refused]
    if with_ladder and ladder_dirs:
        codes.append(ladder_runs(ladder_dirs, judge_name, view=view, force=True, batch=batch))
    return max(codes)


@command
def regenerate(run_dirs: list[Path], *, export_log: bool = True, measures: bool = True, plots: bool = True) -> int:
    """Rebuild what each run directory derives from its eval log: the export, keeping the verdicts judge.jsonl holds
    (a run whose log is gone keeps its export); the code ladder (signals.jsonl) and, on a SWE run, Lucy's measures
    (swe_summary.json); the figures under out/<study>/[<group>/]<run>/ (run_directory.figures_dir). ladder.jsonl is
    never cleared. A run whose log holds another eval than its folder is refused (2)."""
    for run_dir in run_dirs:
        run = RunDir(run_dir).require()
        if export_log and run.eval_log:
            export(run.eval_log, run.path, verdicts=run.verdicts() or None, remedy=LOG_REMEDY)
        elif export_log:
            print(f"{run.name}: no eval log on disk, keeping the export as it is", file=sys.stderr)
        if measures:
            print(signals.run_report(run.path), flush=True)
            if run.run_json.get("swe"):
                swe_measures.summarize(run.path)
        if plots:
            run_figures.plot_run(run.path, figures_dir(run.path))
    return 0
