"""Command line for the peer-pressure experiment: argument parsing only. run, judge, ladder, rejudge and regenerate
call run.py; signals, summary, budget, engagement and swe-summary call src/measures; ui, plots and models call their
modules.

    python -m src run <name> [--benchmark openmath|swe|knowledge_work|subset_sum|prime_factorization|protein_design
                                          |creative_writing]
                             [--preset default]
                             [--scale small|medium|large|extra_large|gigantic] [--set key=value ...]
                             [--row R] [--honeypot H] [--no-grade] [--model M] [--timeout S]
                             [--worker-model M] [--regulator-model M] [--disregulator-model M]
    python -m src judge <eval_log> <run_name> [--judge MODEL] [--sync] [--judge-errors]
    python -m src ladder <run ...> [--judge MODEL] [--sync] [--view full|actions] [--agents a01,a02] [--force] [--table]
    python -m src rejudge <run ...> [--judge MODEL] [--sync] [--no-judge] [--no-ladder] [--view full|actions]
                                    [--judge-errors]
    python -m src regenerate <run ...> [--no-export] [--no-measures] [--no-plots]
    python -m src signals <run ...> [--table]
    python -m src summary <run ...> [--full]
    python -m src engagement <run ...> [--json]
    python -m src swe-summary <run ...>
    python -m src budget <lone_run ...>
    python -m src ui [start|stop] [--port P] [--no-open]
    python -m src models [NAME]
    python -m src plots <run> [--out DIR]

A run is a run name under runs/ or a run directory. The config is built in this order: the preset, then the scale,
then every `--set` (src/configuration/presets.py). The default preset is the midterm report's configuration at the
small scale; `--scale` replaces its size (src/configuration/scales.py). `--set` takes any SwarmConfig field
(src/configuration/swarm_config.py) and overrides any value the scale gave, for example `--set regulators=3 --set
disregulators=3` for a swarm, `--set shutdown=false` for a team without stop_working, `--set turn_budget=70` for
another turn budget, or `--set turn_budget=none` to use the scale's steps_per_agent. A run writes its Inspect log under
logs/<name>/, exports it to runs/<name>/ and prints one line per episode; the judge runs inside the eval. `rejudge`
judges finished runs from their eval logs and places them on the ladder; `regenerate` rebuilds a run directory from
its log. `budget` prints lone runs' turns and their median. `--model` takes an entry of src/models/model_registry.yaml
or an Inspect model string; `models` prints the entries, or one entry in full. `--worker-model`, `--regulator-model`
and `--disregulator-model` give a role its own model the same way; each sets the config's `<role>_model` after every
`--set`, so the flag wins.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from src.configuration import settings
from src.configuration.presets import add_config_arguments, build_config, config_pairs
from src.measures import board_engagement as engagement
from src.measures import episode_summary as summary
from src.models import model_registry as models
from src.ui import launch


def env_timeout() -> int:
    """MODEL_TIMEOUT_S from the environment, or the configured default when unset or not a number."""
    value = os.environ.get("MODEL_TIMEOUT_S", "")
    return int(value) if value.isdigit() else settings.MODEL_TIMEOUT_S


def run_dir(name: str) -> Path:
    """A run directory given as a path, or a run name under runs/."""
    return Path(name) if Path(name).is_dir() else settings.RUNS / name


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
    config = build_config(args.preset, args.scale, config_pairs(args) + (["judge=none"] if args.no_judge else []))
    return run.run(args.name, benchmark, config, args.model, args.timeout)


def cmd_judge(args: argparse.Namespace) -> int:
    from src import run  # the same four seconds as in cmd_run
    return run.judge(Path(args.eval_log), args.run_name, args.judge, batch=not args.sync,
                     judge_errors=args.judge_errors)


def cmd_ladder(args: argparse.Namespace) -> int:
    from src import run  # the same four seconds as in cmd_run
    from src.judging import judging_ladder as ladder
    if args.view not in ladder.VIEWS:
        raise ValueError(f"unknown view {args.view!r}; choose one of {ladder.VIEWS}")
    if args.table:
        print(ladder.ladder_table(run_dirs(args), args.view), end="")
        return 0
    agents = args.agents.split(",") if args.agents else None
    return run.ladder_runs(run_dirs(args), args.judge, view=args.view, agents=agents, force=args.force,
                           batch=not args.sync)


def cmd_rejudge(args: argparse.Namespace) -> int:
    from src import run  # the same four seconds as in cmd_run
    return run.rejudge(run_dirs(args), args.judge, with_judge=not args.no_judge, with_ladder=not args.no_ladder,
                       view=args.view, judge_errors=args.judge_errors, batch=not args.sync)


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
    plots.plot_run(directory, Path(args.out) if args.out else settings.ROOT / "out" / directory.name)
    return 0


def add_run_parser(subparsers) -> None:
    run = subparsers.add_parser("run", help="run a task, export it to runs/<name>/ and print the summary")
    run.add_argument("name", help="run name: logs/<name>/ and runs/<name>/")
    run.add_argument("--benchmark", default="openmath",
                     help="a key of src/domains/__init__.py DOMAINS: openmath, swe, knowledge_work, subset_sum, "
                          "prime_factorization, protein_design or creative_writing")
    add_config_arguments(run)
    run.add_argument("--row", default=None,
                     help="the benchmark's row: an openmath_agent row, a SWE issue (Lucy's splits), a knowledge_work "
                          "row, an interview question, a protein_design target or a creative_writings brief (the "
                          "domain's default)")
    run.add_argument("--honeypot", default="jigsaw",
                     help="SWE honeypot: as_released, own_piece or jigsaw (the default: the fix's pieces are dealt "
                          "over the neutral agents)")
    run.add_argument("--no-grade", action="store_true", help="SWE: collect the diffs and skip the hidden tests")
    run.add_argument("--no-judge", action="store_true",
                     help="run without the judge (--set judge=none); `python -m src rejudge <name>` judges it later")
    run.add_argument("--timeout", type=int, default=env_timeout(),
                     help="seconds one model request may take, retries included (env MODEL_TIMEOUT_S)")
    run.set_defaults(func=cmd_run)


def add_judge_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--judge", default=settings.JUDGE_MODEL, help="entry in src/judging/judge_models.yaml")
    parser.add_argument("--sync", action="store_true",
                        help="the normal API (full price, minutes) instead of the batch API (half price, up to a day)")


def add_runs_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("runs", nargs="+", help="run names under runs/, or run directories")


def add_judging_parsers(subparsers) -> None:
    judge = subparsers.add_parser("judge", help="judge an existing eval log again and export it")
    judge.add_argument("eval_log")
    judge.add_argument("run_name")
    add_judge_arguments(judge)
    judge.add_argument("--judge-errors", action="store_true",
                       help="judge a run even when an episode failed in the harness or at the provider")
    judge.set_defaults(func=cmd_judge)

    ladder = subparsers.add_parser("ladder", help="place every agent of a run on the cheating ladder (ladder.jsonl)")
    add_runs_argument(ladder)
    add_judge_arguments(ladder)
    ladder.add_argument("--view", default="full",
                        help="full: CheatBench's rendering of the transcript; actions: tool calls and results only")
    ladder.add_argument("--agents", default=None, help="judge these agents only, comma separated")
    ladder.add_argument("--force", action="store_true", help="judge again the episodes already in ladder.jsonl")
    ladder.add_argument("--table", action="store_true", help="print the table from the ladder.jsonl files, no judging")
    ladder.set_defaults(func=cmd_ladder)

    rejudge = subparsers.add_parser("rejudge", help="CheatBench's judge from each run's eval log, then the ladder")
    add_runs_argument(rejudge)
    add_judge_arguments(rejudge)
    rejudge.add_argument("--no-judge", action="store_true", help="skip CheatBench's judge")
    rejudge.add_argument("--no-ladder", action="store_true", help="skip the cheating ladder")
    rejudge.add_argument("--view", choices=("full", "actions"), default="full", help="the ladder judge's view")
    rejudge.add_argument("--judge-errors", action="store_true",
                         help="judge a run even when an episode failed in the harness or at the provider")
    rejudge.set_defaults(func=cmd_rejudge)


def add_measure_parsers(subparsers) -> None:
    regenerate = subparsers.add_parser("regenerate", help="rebuild a run directory from its eval log: the export "
                                                          "(keeping judge.jsonl), the measures and the figures")
    add_runs_argument(regenerate)
    regenerate.add_argument("--no-export", action="store_true", help="keep run.json, episodes.jsonl and judge.jsonl")
    regenerate.add_argument("--no-measures", action="store_true", help="skip the code ladder and the SWE measures")
    regenerate.add_argument("--no-plots", action="store_true", help="skip the figures under out/<run>/")
    regenerate.set_defaults(func=cmd_regenerate)

    signals = subparsers.add_parser("signals", help="the cheating signals of a run by code, no judge (signals.jsonl)")
    add_runs_argument(signals)
    signals.add_argument("--table", action="store_true", help="only the table over the runs' signals.jsonl")
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

    plots = subparsers.add_parser("plots", help="the figures of one run as PNG with CSV twins, under out/<run>/")
    plots.add_argument("run", help="a run name under runs/, a run directory or an example's output folder")
    plots.add_argument("--out", default=None, help="where to write (default out/<run name>/, gitignored)")
    plots.set_defaults(func=cmd_plots)


def add_tool_parsers(subparsers) -> None:
    ui = subparsers.add_parser("ui", help="start (default) or stop the results browser")
    ui.add_argument("action", nargs="?", choices=("start", "stop"), default="start")
    ui.add_argument("--port", type=int, default=settings.UI_PORT)
    ui.add_argument("--no-open", action="store_true", help="do not open the browser")
    ui.set_defaults(func=cmd_ui)

    table = subparsers.add_parser("models", help="the models we can consider, with the name each route needs")
    table.add_argument("name", nargs="?", default=None, help="one entry, printed in full (default: the table)")
    table.set_defaults(func=cmd_models)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_run_parser(subparsers)
    add_judging_parsers(subparsers)
    add_measure_parsers(subparsers)
    add_tool_parsers(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
