"""Command line for the peer-pressure experiment: argument parsing only. run, judge, rejudge, judge-pass and
regenerate call run.py; signals, summary, budget, engagement and swe-summary call src/measures; ui, plots and models
call their modules.

    python -m src run <name> [--benchmark openmath|swe|knowledge_work|subset_sum|prime_factorization|protein_design
                                          |creative_writing|chess]
                             [--preset default]
                             [--scale small|medium|large|extra_large|gigantic] [--set key=value ...]
                             [--row R] [--honeypot H] [--no-grade] [--model M] [--timeout S]
                             [--worker-model M] [--regulator-model M] [--disregulator-model M] [--replace]
    python -m src judge <eval_log> <run_name> [--judge MODEL] [--sync] [--judge-errors] [--replace]
    python -m src rejudge <run ...> [--judge MODEL] [--sync] [--judge-errors]
    python -m src judge-pass <cheatbench|labels> <run or study ...> --judge MODEL --cap DOLLARS [--batch]
                             [--limit N] [--only-missing | --redo]
    python -m src regenerate <run ...> [--no-export] [--no-measures] [--no-plots]
    python -m src signals <run ...> [--table]
    python -m src summary <run ...> [--full]
    python -m src engagement <run ...> [--json]
    python -m src swe-summary <run ...>
    python -m src budget <lone_run ...>
    python -m src ui [start|stop] [--port P] [--no-open]
    python -m src models [NAME]
    python -m src plots <run> [--out DIR]
    python -m src replay <run or study ...> --out DIR [--only-missing] [--serial] [--jobs N] [--pem] [--warm-image]
    python -m src pipeline status | add <part> "<line>" | bump <part> "<line>" | re-lock <part> "<why>"
    python -m src process [<study or run> ...] [--force PART] [--dry-run] [--jobs N]

A run is a run directory, or a run name of the study: runs/<study>/<name>/, or the group that holds it
(run_directory.find_run). The config is built in this order: the preset, then the scale,
then every `--set` (src/configuration/presets.py). The default preset is the midterm report's configuration at the
small scale; `--scale` replaces its size (src/configuration/scales.py). `--set` takes any SwarmConfig field
(src/configuration/swarm_config.py) and overrides any value the scale gave, for example `--set regulators=3 --set
disregulators=3` for a swarm, `--set shutdown=false` for a team without stop_working, or `--set rounds=20 --set
calls_per_round=5` for other rounds of tool calls. A run writes its Inspect log under
logs/<study>/<name>/, exports it to runs/<study>/<name>/ and prints one line per episode; the judge runs inside the
eval. A run refuses a name whose folder already holds a run, and `judge` a folder that holds another eval's run, before
anything is spent; `--replace` sets that earlier run aside into runs/<study>/attempts/<name>.<n>/ first, with its eval
log and its figures. `judge` and `rejudge` judge finished runs from their eval logs and RE-EXPORT each run from its log
into its own directory, wherever it lives, rewriting its record with the current code. `judge-pass` judges runs'
stored records instead, under a dollar cap, and writes only runs/<study>/<run>/judges/<pass>.<judge>.jsonl
(src/judging/judge_pass.py); a study folder stands for its valid runs. `regenerate` rebuilds a run directory from its
log. `budget` prints lone runs' turns and their median. `--model` takes an entry of
src/models/model_registry.yaml or an Inspect model string; `models` prints the entries, or one entry in full.
`--worker-model`, `--regulator-model` and `--disregulator-model` give a role its own model the same way; each sets the
config's `<role>_model` after every `--set`, so the flag wins. `pipeline` reads and locks the pipeline's registry,
src/configuration/pipeline_parts.yaml (src/measures/pipeline_parts.py): `status` lists every part with its version and
whether its code moved since its lock, `add` locks a new entry at version 1, `bump` gives a part a new version, and
`re-lock` takes a part's new code at its version, for a change that moves no stored byte. `process` computes and
stores every part that is behind in each valid run (src/measures/process_runs.py, `make process-transcripts`); the
transcript viewer serves only what it stored.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from src.configuration import launch_command, settings
from src.configuration.presets import add_config_arguments, build_config, config_pairs
from src.judging.verdict_files import PASSES
from src.measures import board_engagement as engagement
from src.measures import episode_summary as summary
from src.measures import pipeline_parts, process_runs
from src.measures.run_directory import RunDir, find_run
from src.models import model_registry as models
from src.ui import launch
from src.utils import progress


def env_timeout() -> int:
    """MODEL_TIMEOUT_S from the environment, or the configured default when unset or not a number."""
    value = os.environ.get("MODEL_TIMEOUT_S", "")
    return int(value) if value.isdigit() else settings.MODEL_TIMEOUT_S


def run_dir(name: str) -> Path:
    """A run directory given as a path, or a run name of the study: its folder at the top level or in its one group
    (run_directory.find_run), else runs/<study>/<name>, which the command then refuses as no run. Two folders of one
    name raise ValueError."""
    if Path(name).is_dir():
        return Path(name)
    return find_run(settings.RUNS, name) or settings.RUNS / name


def run_dirs(args: argparse.Namespace) -> list[Path]:
    return [run_dir(name) for name in args.runs]


def cmd_run(args: argparse.Namespace) -> int:
    # run.py imports Inspect, ORBIT and CheatBench, four seconds; only the commands that judge or run pay for them.
    from src import run
    from src.domains import DOMAINS
    if args.benchmark not in DOMAINS:
        raise ValueError(f"unknown benchmark {args.benchmark!r}; choose one of {', '.join(DOMAINS)}")
    cls = DOMAINS[args.benchmark]
    options = {"honeypot": args.honeypot, "grade": not args.no_grade}
    benchmark = cls(args.row, **{k: v for k, v in options.items() if k in cls.options})
    pairs = [f"id_salt={args.name}", *config_pairs(args), *(["judge=none"] if args.no_judge else [])]
    config = build_config(args.preset, args.scale, pairs)
    return run.run(args.name, benchmark, config, args.model, args.timeout, replace=args.replace)


def cmd_judge(args: argparse.Namespace) -> int:
    from src import run  # the same four seconds as in cmd_run
    return run.judge(Path(args.eval_log), args.run_name, args.judge, batch=not args.sync,
                     judge_errors=args.judge_errors, replace=args.replace)


def cmd_rejudge(args: argparse.Namespace) -> int:
    from src import run  # the same four seconds as in cmd_run
    return run.rejudge(run_dirs(args), args.judge, judge_errors=args.judge_errors, batch=not args.sync)


def cmd_judge_pass(args: argparse.Namespace) -> int:
    from src import run  # the same four seconds as in cmd_run
    return run.judge_pass(args.pass_name, run_dirs(args), args.judge, cap=args.cap, batch=args.batch, limit=args.limit,
                          only_missing=args.only_missing, redo=args.redo)


def cmd_regenerate(args: argparse.Namespace) -> int:
    from src import run  # the same four seconds as in cmd_run
    return run.regenerate(run_dirs(args), export_log=not args.no_export, measures=not args.no_measures,
                          plots=not args.no_plots)


def cmd_signals(args: argparse.Namespace) -> int:
    from src.measures import code_ladder as signals  # the domains' imports, a few seconds
    if not args.table:
        for directory in run_dirs(args):
            print(signals.run_report(directory), flush=True)
    print(signals.signals_table(run_dirs(args)), end="")
    print(signals.team_table(run_dirs(args)), end="")
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    print("\n".join([summary.HEADER, *(line for d in run_dirs(args) for line in summary.summary_lines(d, args.full))]))
    return 0


def cmd_engagement(args: argparse.Namespace) -> int:
    measures = [engagement.run_measures(d) for d in run_dirs(args)]
    print(json.dumps(measures, indent=1) if args.json else "\n\n".join(engagement.format_run(m) for m in measures))
    return 0


def cmd_swe_summary(args: argparse.Namespace) -> int:
    from src.measures import swe_measures  # the domains' imports, a few seconds
    summaries = [swe_measures.summarize(d) for d in run_dirs(args)]
    print("\n".join([*swe_measures.agent_lines(summaries), "", *swe_measures.arm_lines(summaries)]))
    return 0


def cmd_budget(args: argparse.Namespace) -> int:
    print("\n".join(summary.budget_lines(run_dirs(args))))
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    if args.action == "stop":
        return launch.stop(args.port)
    return launch.start(args.port, open_browser=not args.no_open)


def cmd_models(args: argparse.Namespace) -> int:
    print(models.render(args.name), end="")
    return 0


def cmd_plots(args: argparse.Namespace) -> int:
    from src.measures import run_figures as plots  # matplotlib, a second; only plots pays for it
    directory = run_dir(args.run)
    plots.plot_run(directory, Path(args.out) if args.out else RunDir(directory).file("figures"))
    return 0


def add_run_parser(subparsers) -> None:
    run = subparsers.add_parser("run", help="run a task, export it to runs/<study>/<name>/ and print the summary")
    run.add_argument("name", help="run name: logs/<study>/<name>/ and runs/<study>/<name>/")
    run.add_argument("--benchmark", default="openmath",
                     help="a key of src/domains/__init__.py DOMAINS: openmath, swe, knowledge_work, subset_sum, "
                          "prime_factorization, protein_design, creative_writing or chess")
    add_config_arguments(run)
    run.add_argument("--row", default=None,
                     help="the benchmark's row: an openmath_agent row, a SWE issue (Lucy's splits), a knowledge_work "
                          "row, an interview question, a protein_design target, a creative_writings brief or a "
                          "chess position (the domain's default)")
    run.add_argument("--honeypot", default="jigsaw",
                     help="SWE honeypot: as_released, own_piece or jigsaw (the default: the fix's pieces are dealt "
                          "over the neutral agents)")
    run.add_argument("--no-grade", action="store_true", help="SWE: collect the diffs and skip the hidden tests")
    run.add_argument("--no-judge", action="store_true",
                     help="run without the judge (--set judge=none); `python -m src rejudge <name>` judges it later")
    run.add_argument("--timeout", type=int, default=env_timeout(),
                     help="seconds one model request may take, retries included (env MODEL_TIMEOUT_S)")
    run.add_argument("--replace", action="store_true",
                     help="set the run already in runs/<study>/<name>/ aside into runs/<study>/attempts/<name>.<n>/ "
                          "with its log and figures, then run; without it the run refuses that folder before it starts")
    run.set_defaults(func=cmd_run)


def add_judge_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--judge", default=settings.JUDGE_MODEL, help="entry in src/judging/judge_models.yaml")
    parser.add_argument("--sync", action="store_true",
                        help="the normal API (full price, minutes) instead of the batch API (half price, up to a day)")


def add_runs_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("runs", nargs="+", help="run names of the study, or run directories")


def add_judging_parsers(subparsers) -> None:
    judge = subparsers.add_parser("judge", help="judge an eval log with CheatBench's judge and RE-EXPORT the run from "
                                                "the log: every file of the run the export owns is rewritten")
    judge.add_argument("eval_log")
    judge.add_argument("run_name")
    add_judge_arguments(judge)
    judge.add_argument("--judge-errors", action="store_true",
                       help="judge a run even when an episode failed in the harness or at the provider")
    judge.add_argument("--replace", action="store_true",
                       help="set another eval's run of this name aside into runs/<study>/attempts/<run_name>.<n>/ once "
                            "the verdicts are in; without it the judge refuses that folder before it starts")
    judge.set_defaults(func=cmd_judge)

    rejudge = subparsers.add_parser("rejudge", help="CheatBench's judge from each run's eval log, then RE-EXPORT each "
                                                    "run from its log: every file of the run the export owns is "
                                                    "rewritten with the current code")
    add_runs_argument(rejudge)
    add_judge_arguments(rejudge)
    rejudge.add_argument("--judge-errors", action="store_true",
                         help="judge a run even when an episode failed in the harness or at the provider")
    rejudge.set_defaults(func=cmd_rejudge)

    judge_pass = subparsers.add_parser("judge-pass", help="a judge pass over runs' stored records under a dollar cap, "
                                                          "into runs/<study>/<run>/judges/<pass>.<judge>.jsonl only; "
                                                          "no eval log, no re-export")
    judge_pass.add_argument("pass_name", metavar="pass", choices=PASSES,
                            help="cheatbench: CheatBench's judge per stored trajectory; labels: disclosure and "
                                 "manipulation, each a score with an excerpt, per agent")
    judge_pass.add_argument("runs", nargs="+", help="run names of the study, run directories, or a study folder (its "
                                                    "valid runs)")
    judge_pass.add_argument("--judge", required=True, help="entry in src/judging/judge_models.yaml")
    judge_pass.add_argument("--cap", type=float, required=True,
                            help="dollars the pass may spend; it stops before a call could pass them")
    judge_pass.add_argument("--batch", action="store_true",
                            help="the batch API (half price, up to a day) instead of the normal API")
    judge_pass.add_argument("--limit", type=int, default=None, help="judge the first N units to judge: the pilot")
    held = judge_pass.add_mutually_exclusive_group()
    held.add_argument("--only-missing", action="store_true",
                      help="judge only units with no row for this judge (by default a failed row is judged again)")
    held.add_argument("--redo", action="store_true",
                      help="judge every unit again, parsed rows included; a new row replaces its unit's row")
    judge_pass.set_defaults(func=cmd_judge_pass)


def add_measure_parsers(subparsers) -> None:
    regenerate = subparsers.add_parser("regenerate", help="rebuild a run directory from its eval log: the export "
                                                          "(keeping judges/), the measures and the figures")
    add_runs_argument(regenerate)
    regenerate.add_argument("--no-export", action="store_true",
                            help="keep run.json, episodes.jsonl and the transcripts")
    regenerate.add_argument("--no-measures", action="store_true",
                            help="skip the code record (signals.jsonl) and the SWE measures")
    regenerate.add_argument("--no-plots", action="store_true",
                            help="skip the figures under out/<study>/[<group>/]<run>/")
    regenerate.set_defaults(func=cmd_regenerate)

    signals = subparsers.add_parser(
        "signals", help="the code's evidence and the three ladders of a run's agents and teams (signals.jsonl)")
    add_runs_argument(signals)
    signals.add_argument("--table", action="store_true",
                         help="only the tables over the runs' signals.jsonl: the agents', then the teams'")
    signals.set_defaults(func=cmd_signals)

    episodes = subparsers.add_parser("summary", help="one line per episode with its verdict")
    add_runs_argument(episodes)
    episodes.add_argument("--full", action="store_true", help="also print the judge's summary")
    episodes.set_defaults(func=cmd_summary)

    board = subparsers.add_parser("engagement", help="the board engagement funnel, per role and per agent")
    add_runs_argument(board)
    board.add_argument("--json", action="store_true", help="print the measures as JSON")
    board.set_defaults(func=cmd_engagement)

    swe = subparsers.add_parser("swe-summary", help="Lucy's SWE measures per agent and per arm (swe_summary.json)")
    add_runs_argument(swe)
    swe.set_defaults(func=cmd_swe_summary)

    budget = subparsers.add_parser("budget", help="lone runs' turns and their median")
    add_runs_argument(budget)
    budget.set_defaults(func=cmd_budget)

    plots = subparsers.add_parser("plots", help="the figures of one run as PNG with CSV twins, under "
                                                "out/<study>/[<group>/]<run>/")
    plots.add_argument("run", help="a run name under runs/, a run directory or an example's output folder")
    plots.add_argument("--out", default=None, help="where to write (default out/<run name>/, gitignored)")
    plots.set_defaults(func=cmd_plots)


def cmd_replay(args: argparse.Namespace) -> int:
    from src.replay.replay_run import replay_runs  # Inspect and the domains' imports; only replay pays for them
    return replay_runs(run_dirs(args), Path(args.out), only_missing=args.only_missing, parallel=not args.serial,
                       jobs=args.jobs, pem=args.pem, warm_image=args.warm_image)


def add_replay_parser(subparsers) -> None:
    replay = subparsers.add_parser("replay", help="play stored runs back in their sandboxes from their recorded calls "
                                                  "(no model, no network): out/<run>/steps.jsonl and replay.json")
    replay.add_argument("runs", nargs="+", help="run names of the study, run directories, or a study folder (its "
                                                "valid runs of a task the replay plays back: SWE, protein design, "
                                                "creative writing)")
    replay.add_argument("--out", required=True, help="the folder that gets one folder per run")
    replay.add_argument("--only-missing", action="store_true", help="skip a run whose replay.json exists")
    replay.add_argument("--serial", action="store_true",
                        help="every call alone, in global order (by default calls in different machines that touch "
                             "nothing shared run side by side)")
    replay.add_argument("--jobs", type=int, default=1, help="runs replayed at once, each its own compose project")
    replay.add_argument("--pem", action="store_true",
                        help="put the recorded certificates and keys in place of those a call draws anew "
                             "(src/replay/random_content.py; replay.json lists each swap)")
    replay.add_argument("--warm-image", action="store_true",
                        help="run every machine on the run's image with Go's build cache filled, built once "
                             "(src/replay/warm_image.py; replay.json names the base image)")
    replay.set_defaults(func=cmd_replay)


def add_tool_parsers(subparsers) -> None:
    ui = subparsers.add_parser("ui", help="start (default) or stop the results browser")
    ui.add_argument("action", nargs="?", choices=("start", "stop"), default="start")
    ui.add_argument("--port", type=int, default=settings.UI_PORT)
    ui.add_argument("--no-open", action="store_true", help="do not open the browser")
    ui.set_defaults(func=cmd_ui)

    table = subparsers.add_parser("models", help="the models we can consider, with the name each route needs")
    table.add_argument("name", nargs="?", default=None, help="one entry, printed in full (default: the table)")
    table.set_defaults(func=cmd_models)


def cmd_pipeline(args: argparse.Namespace) -> int:
    registry = pipeline_parts.load(Path(args.registry))
    if args.action != "status":
        change = {"add": pipeline_parts.add, "bump": pipeline_parts.bump, "re-lock": pipeline_parts.re_lock}
        part = change[args.action](registry, args.part, args.line)
        pipeline_parts.save(registry)
        progress.step(f"Pipeline {args.action} {args.part}")
        progress.detail("version", part.version)
        progress.detail("log", part.log[part.version])
        progress.detail("fingerprint", part.fingerprint)
        progress.done(f"{args.part} at v{part.version}", out=progress.short(registry.path))
        return 0
    progress.step("Pipeline parts")
    progress.detail("registry", progress.short(registry.path))
    states = {name: pipeline_parts.state(registry, name) for name in pipeline_parts.order(registry)}
    for name, state in states.items():
        part = registry.parts[name]
        version = "-" if part.version is None else f"v{part.version}"
        needs = f"needs {', '.join(part.needs)}" if part.needs else ""
        progress.detail(f"{name:<18}{part.kind:<10}{version:<5}{state:<11}{needs}".rstrip())
    found = pipeline_parts.problems(registry)
    for problem in found:
        progress.detail("problem", problem)
    moved = sum(state != "locked" for state in states.values())
    summary_line = (f"{progress.plural(len(states), 'part')}, {moved} not locked, "
                    f"{progress.plural(len(found), 'problem')}")
    if moved or found:
        progress.failed(summary_line)
        return 1
    progress.done(summary_line)
    return 0


def add_pipeline_parser(subparsers) -> None:
    pipeline = subparsers.add_parser("pipeline", help="the pipeline's registry: status, add, bump, re-lock")
    actions = pipeline.add_subparsers(dest="action", required=True)
    actions.add_parser("status", help="every part, its version, whether its code moved, and the guard's problems")
    for action, what, line in (("add", "lock a new entry at version 1", "what the first version does"),
                               ("bump", "a new version: its output changes", "what changed"),
                               ("re-lock", "take the part's new code at its version", "why no stored byte changes")):
        parser = actions.add_parser(action, help=what)
        parser.add_argument("part")
        parser.add_argument("line", help=line)
    pipeline.add_argument("--registry", default=str(pipeline_parts.REGISTRY), help=argparse.SUPPRESS)
    pipeline.set_defaults(func=cmd_pipeline)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_run_parser(subparsers)
    add_judging_parsers(subparsers)
    add_measure_parsers(subparsers)
    add_tool_parsers(subparsers)
    add_replay_parser(subparsers)
    add_pipeline_parser(subparsers)
    process_runs.add_parser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if argv is None and args.command == "run":      # run.json "launch" (src/configuration/launch_command.py)
        launch_command.record("src", sys.argv[1:])
    try:
        return args.func(args)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
