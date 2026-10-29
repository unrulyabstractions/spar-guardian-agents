"""Regenerate what a run's directory derives from its eval log: the export, the measures, the figures, and the
analysis tools' outputs. The results browser reads the directory live, so this also refreshes what it shows.

    uv run python scripts/regenerate.py <run> [<run> ...] [--no-export] [--no-measures] [--no-plots] [--tools]
                                        [--restart-ui]

The export rewrites run.json, episodes.jsonl and judge.jsonl from the log and keeps the verdicts already in
judge.jsonl, so a re-judged run keeps its re-judgement; ladder.jsonl and the measures' files are never cleared. The
measures are the board engagement funnel on every run, the cheating signals (src/measures/code_ladder.py, the code
ladder) on every run, and Lucy's SWE measures (src/benchmark/domains/swe/swe_measures.py) on SWE runs. The figures go
to out/<run>/. --tools runs every analysis
tool of tools/ in order (scout, graph, transect, contagion, failures, hawk) when tools/.venv exists. --restart-ui
stops and starts the results browser.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import run  # noqa: E402
from src.benchmark.domains.swe import swe_measures as swe_summary  # noqa: E402
from src.configuration import settings  # noqa: E402
from src.measures import board_engagement as engagement  # noqa: E402
from src.measures import code_ladder as signals  # noqa: E402
from src.ui import launch  # noqa: E402
from src.ui import run_figures as plots  # noqa: E402

TOOLS = ("scout", "graph", "transect", "contagion", "failures", "hawk")
TOOLS_PYTHON = ROOT / "tools" / ".venv" / "bin" / "python"


def export_again(run_dir: Path) -> int:
    """The run directory from its eval log, with the verdicts judge.jsonl already holds."""
    run_json = json.loads((run_dir / "run.json").read_text())
    eval_log = run_json.get("eval_log")
    if not eval_log or not Path(eval_log).is_file():
        print(f"{run_dir.name}: no eval log on disk, keeping the export as it is", file=sys.stderr)
        return 0
    judge_jsonl = run_dir / "judge.jsonl"
    verdicts = None
    if judge_jsonl.is_file():
        rows = [json.loads(line) for line in judge_jsonl.read_text().splitlines() if line.strip()]
        verdicts = {row["episode"]: row for row in rows}
    try:
        run.export(Path(eval_log), run_dir.name, verdicts=verdicts)
    except run.Refusal as refusal:
        print(f"{run_dir.name}: {refusal}", file=sys.stderr)
        return refusal.code
    return 0


def measures_again(run_dir: Path) -> None:
    """The engagement funnel on every run; the code ladder and Lucy's measures where they apply."""
    task = json.loads((run_dir / "run.json").read_text()).get("task")
    print(f"== engagement {run_dir}", flush=True)
    engagement.run_measures(run_dir)
    if task in signals.DOMAINS:
        print(f"== signals {run_dir}", flush=True)
        signals.run_signals(run_dir)
    if task == "software_engineering":
        print(f"== swe_summary {run_dir}", flush=True)
        swe_summary.main([str(run_dir)])


def tools_again(run_dir: Path) -> int:
    """Every analysis tool over the run, in the tools environment; the first failure's code."""
    if not TOOLS_PYTHON.is_file():
        print("tools/.venv is missing: make tools-setup first", file=sys.stderr)
        return 2
    for tool in TOOLS:
        print(f"== tools {tool} {run_dir.name}", flush=True)
        done = subprocess.run(["uv", "run", "--project", "tools", "python", "-m", "tools", tool, run_dir.name],
                              cwd=ROOT)
        if done.returncode:
            return done.returncode
    return 0


def regenerate(name: str, *, export: bool, measures: bool, figures: bool, tools: bool) -> int:
    run_dir = settings.RUNS / name
    if not (run_dir / "run.json").is_file():
        print(f"{name}: no run.json under {run_dir}", file=sys.stderr)
        return 2
    if export:
        code = export_again(run_dir)
        if code:
            return code
    if measures:
        measures_again(run_dir)
    if figures:
        plots.plot_run(run_dir, settings.ROOT / "out" / name)
    if tools:
        return tools_again(run_dir)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+", help="run names under runs/")
    parser.add_argument("--no-export", action="store_true", help="keep run.json, episodes.jsonl and judge.jsonl")
    parser.add_argument("--no-measures", action="store_true", help="skip the engagement funnel and the code ladder")
    parser.add_argument("--no-plots", action="store_true", help="skip the figures under out/<run>/")
    parser.add_argument("--tools", action="store_true", help="also run every analysis tool of tools/")
    parser.add_argument("--restart-ui", action="store_true", help="stop and start the results browser")
    args = parser.parse_args(argv)
    # The steps in the open: the export from the log, the measures over it, the figures, then the tools if asked.
    codes = [regenerate(name, export=not args.no_export, measures=not args.no_measures, figures=not args.no_plots,
                        tools=args.tools) for name in args.runs]
    if args.restart_ui:
        launch.stop(settings.UI_PORT)
        codes.append(launch.start(settings.UI_PORT, open_browser=False))
    return max(codes)


if __name__ == "__main__":
    sys.exit(main())
