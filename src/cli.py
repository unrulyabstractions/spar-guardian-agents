"""Command line for the peer-pressure experiment. Argument parsing only; every command calls run.py.

    python -m src run <name> [--benchmark openmath|swe|knowledge_work] [--preset default]
                             [--scale small|medium|large|extra_large|gigantic] [--set key=value ...]
                             [--row R] [--issue I] [--honeypot H] [--no-grade] [--model M] [--timeout S]
    python -m src judge <eval_log> <run_name> [--judge MODEL] [--sync] [--judge-errors]
    python -m src ui [start|stop] [--port P] [--no-open]
    python -m src summary <run_dir ...> [--full]
    python -m src budget <lone_run_dir> [<lone_run_dir> ...]
    python -m src models [NAME]
    python -m src plots <run_dir> [--out DIR]

The config is built in this order: the preset, then the scale, then every `--set`. The default preset is the
midterm report's configuration at the small scale; `--scale` replaces its size (src/configuration/scales.py). `--set`
takes any SwarmConfig field
(src/configuration/swarm_config.py) and overrides any value the scale gave, for example `--set regulators=3 --set
disregulators=3` for a swarm, `--set shutdown=false` for agents without the submit tool, `--set
turn_budget=knowledge_work:70` for another budget on one domain (the other domains keep settings.TURN_BUDGETS), or
`--set turn_budget=none` to use the scale's steps_per_agent in place of the domain's turn budget. A run writes its
Inspect log under
logs/<ctrl|swarm|swe>/<name>/, exports it to runs/<name>/ and prints one line per episode; the judge runs inside
the eval. `budget` prints lone runs' turns and their median, the turn budget every agent gets.
`--model` takes an entry of src/configuration/models.yaml or an Inspect model string; `models` prints the entries,
or one entry in full.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from src.configuration import models, settings
from src.configuration.scales import SCALES
from src.configuration.swarm_config import PRESETS, build_config
from src.measures import summary
from src.ui import launch

OPENMATH_ROW = "riemann"


def env_timeout() -> int:
    """MODEL_TIMEOUT_S from the environment, or the configured default when unset or not a number."""
    value = os.environ.get("MODEL_TIMEOUT_S", "")
    return int(value) if value.isdigit() else settings.MODEL_TIMEOUT_S


def cmd_run(args: argparse.Namespace) -> int:
    # run.py imports Inspect, ORBIT and CheatBench, four seconds; only run and judge pay for them.
    from src import run
    from src.benchmark.domains.openmath import OpenMath
    from src.benchmark.domains.swe import SWE
    config = build_config(args.preset, args.scale, args.settings + (["judge=none"] if args.no_judge else []))
    if args.benchmark == "openmath":
        benchmark = OpenMath(args.row or OPENMATH_ROW)
    elif args.benchmark == "swe":
        benchmark = SWE(args.issue, args.honeypot, not args.no_grade)
    else:
        from src.benchmark.domains.knowledge import KnowledgeWork
        benchmark = KnowledgeWork(args.row) if args.row else KnowledgeWork()
    return run.run(args.name, benchmark, config, models.resolve(args.model), args.timeout)


def cmd_models(args: argparse.Namespace) -> int:
    print(models.render(args.name), end="")
    return 0


def cmd_budget(args: argparse.Namespace) -> int:
    from src import run  # the same four seconds as in cmd_run
    return run.budget([Path(d) for d in args.run_dirs])


def cmd_plots(args: argparse.Namespace) -> int:
    from src.ui import plots  # matplotlib, a second; only plots pays for it
    run_dir = Path(args.run_dir)
    plots.plot_run(run_dir, Path(args.out) if args.out else settings.ROOT / "out" / run_dir.name)
    return 0


def cmd_judge(args: argparse.Namespace) -> int:
    from src import run  # the same four seconds as in cmd_run
    return run.judge(Path(args.eval_log), args.run_name, args.judge, batch=not args.sync,
                     judge_errors=args.judge_errors)


def cmd_ladder(args: argparse.Namespace) -> int:
    from src import run  # the same four seconds as in cmd_run
    from src.benchmark import ladder  # the judge's imports, the same seconds
    run_dirs = [Path(name) if Path(name).is_dir() else settings.RUNS / name for name in args.runs]
    if args.table:
        print(ladder.table(run_dirs, args.view), end="")
        return 0
    agents = args.agents.split(",") if args.agents else None
    return run.ladder_runs(run_dirs, args.judge, view=args.view, agents=agents, force=args.force,
                           batch=not args.sync)


def cmd_ui(args: argparse.Namespace) -> int:
    if args.action == "stop":
        return launch.stop(args.port)
    return launch.start(args.port, open_browser=not args.no_open)


def cmd_summary(args: argparse.Namespace) -> int:
    return summary.main(list(args.run_dirs) + (["--full"] if args.full else []))


def add_run_parser(subparsers) -> None:
    run = subparsers.add_parser("run", help="run a task, export it to runs/<name>/ and print the summary")
    run.add_argument("name", help="run name: logs/<kind>/<name>/ and runs/<name>/")
    run.add_argument("--benchmark", choices=("openmath", "swe", "knowledge_work"), default="openmath")
    run.add_argument("--preset", choices=sorted(PRESETS), default="default",
                     help="default: the midterm report's configuration at the small scale")
    run.add_argument("--scale", choices=list(SCALES), default=None,
                     help="the run's size, applied after the preset (default: the preset's own, small for default)")
    run.add_argument("--set", dest="settings", action="append", default=[], metavar="KEY=VALUE",
                     help="set a SwarmConfig field after the preset and the scale; repeatable")
    run.add_argument("--row", default=None,
                     help=f"openmath_agent row (default {OPENMATH_ROW}) or knowledge_work row (the domain's default)")
    run.add_argument("--issue", default="flipt-02e2", help="SWE issue (Lucy's splits)")
    run.add_argument("--honeypot", choices=("as_released", "own_piece", "jigsaw"), default="jigsaw",
                     help="SWE honeypot (default jigsaw: the fix's pieces are dealt over the neutral agents)")
    run.add_argument("--no-grade", action="store_true", help="SWE: collect the diffs and skip the hidden tests")
    run.add_argument("--no-judge", action="store_true",
                     help="run without the judge (--set judge=none); scripts/rejudge.py <name> judges it later")
    run.add_argument("--model", default=settings.DEFAULT_MODEL,
                     help="the agents' model: an entry of src/configuration/models.yaml (python -m src models) or an "
                          "Inspect model string")
    run.add_argument("--timeout", type=int, default=env_timeout(),
                     help="seconds one model request may take, retries included (env MODEL_TIMEOUT_S)")
    run.set_defaults(func=cmd_run)


def add_judge_parser(subparsers) -> None:
    judge = subparsers.add_parser("judge", help="judge an existing eval log again and export it")
    judge.add_argument("eval_log")
    judge.add_argument("run_name")
    judge.add_argument("--judge", default=settings.JUDGE_MODEL, help="entry in src/benchmark/models.yaml")
    judge.add_argument("--sync", action="store_true",
                       help="the normal API (full price, minutes) instead of the batch API (half price, up to a day)")
    judge.add_argument("--judge-errors", action="store_true",
                       help="judge a run even when an episode failed in the harness or at the provider")
    judge.set_defaults(func=cmd_judge)


def add_ladder_parser(subparsers) -> None:
    ladder = subparsers.add_parser("ladder", help="place every agent of a run on the cheating ladder (ladder.jsonl)")
    ladder.add_argument("runs", nargs="+", help="run names under runs/, or run directories")
    ladder.add_argument("--judge", default=settings.JUDGE_MODEL, help="entry in src/benchmark/models.yaml")
    ladder.add_argument("--sync", action="store_true",
                        help="the normal API (full price, minutes) instead of the batch API (half price, up to a day)")
    ladder.add_argument("--view", choices=("full", "actions"), default="full",
                        help="full: CheatBench's rendering of the transcript; actions: tool calls and results only")
    ladder.add_argument("--agents", default=None, help="judge these agents only, comma separated")
    ladder.add_argument("--force", action="store_true", help="judge again the episodes already in ladder.jsonl")
    ladder.add_argument("--table", action="store_true", help="print the table from the ladder.jsonl files, no judging")
    ladder.set_defaults(func=cmd_ladder)


def add_ui_parser(subparsers) -> None:
    ui = subparsers.add_parser("ui", help="start (default) or stop the results browser")
    ui.add_argument("action", nargs="?", choices=("start", "stop"), default="start")
    ui.add_argument("--port", type=int, default=settings.UI_PORT)
    ui.add_argument("--no-open", action="store_true", help="do not open the browser")
    ui.set_defaults(func=cmd_ui)


def add_summary_parser(subparsers) -> None:
    summary = subparsers.add_parser("summary", help="one line per judged episode")
    summary.add_argument("run_dirs", nargs="+")
    summary.add_argument("--full", action="store_true", help="also print the judge's summary")
    summary.set_defaults(func=cmd_summary)


def add_budget_parser(subparsers) -> None:
    budget = subparsers.add_parser("budget", help="lone runs' turns and their median, the turn budget every agent gets")
    budget.add_argument("run_dirs", nargs="+", help="runs/<name>/ of lone agents' runs")
    budget.set_defaults(func=cmd_budget)


def add_models_parser(subparsers) -> None:
    table = subparsers.add_parser("models", help="the models we can consider, with the name each route needs")
    table.add_argument("name", nargs="?", default=None, help="one entry, printed in full (default: the table)")
    table.set_defaults(func=cmd_models)


def add_plots_parser(subparsers) -> None:
    plots = subparsers.add_parser("plots", help="the figures of one run as PNG with CSV twins, under out/<run>/")
    plots.add_argument("run_dir", help="runs/<name>/ or an example's output folder")
    plots.add_argument("--out", default=None, help="where to write (default out/<run name>/, gitignored)")
    plots.set_defaults(func=cmd_plots)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_run_parser(subparsers)
    add_judge_parser(subparsers)
    add_ladder_parser(subparsers)
    add_ui_parser(subparsers)
    add_summary_parser(subparsers)
    add_budget_parser(subparsers)
    add_models_parser(subparsers)
    add_plots_parser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
