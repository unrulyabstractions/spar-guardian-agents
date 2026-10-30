"""Judge a finished run again: CheatBench's judge from the eval log, then the cheating ladder from the transcripts.

    uv run python scripts/rejudge.py <run> [<run> ...] [--judge gpt-6-luna] [--sync] [--no-judge]
                                     [--no-ladder] [--view full|actions] [--judge-errors]

CheatBench's judge (`python -m src judge`) needs the run's eval log, named in run.json; it re-exports runs/<name>/
with the new verdicts and keeps ladder.jsonl and the measures' files. The ladder (`python -m src ladder`) needs only
runs/<name>/trajectories/, so it also runs on a set-aside run whose log is gone. Both use the judge entry given, and
both call it through the provider's batch API (half price, up to a day) unless --sync (the normal API, minutes).
A run made with --no-judge gets its first verdicts here.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import run  # noqa: E402
from src.configuration import settings  # noqa: E402


def rejudge(name: str, judge: str, *, with_judge: bool, with_ladder: bool, view: str, judge_errors: bool,
            batch: bool) -> int:
    """CheatBench's judge over the run's eval log, then the ladder over its transcripts; the first failure's code."""
    run_dir = settings.RUNS / name
    if not (run_dir / "run.json").is_file():
        print(f"{name}: no run.json under {run_dir}", file=sys.stderr)
        return 2
    if with_judge:
        eval_log = json.loads((run_dir / "run.json").read_text()).get("eval_log")
        if not eval_log or not Path(eval_log).is_file():
            print(f"{name}: no eval log on disk, skipping CheatBench's judge", file=sys.stderr)
        else:
            code = run.judge(Path(eval_log), name, judge, batch=batch, judge_errors=judge_errors)
            if code:
                return code
    if with_ladder:
        return run.ladder_runs([run_dir], judge, view=view, force=True, batch=batch)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+", help="run names under runs/")
    parser.add_argument("--judge", default=settings.JUDGE_MODEL, help="entry in src/judging/judge_models.yaml")
    parser.add_argument("--sync", action="store_true",
                        help="the normal API (full price, minutes) instead of the batch API (half price, up to a day)")
    parser.add_argument("--no-judge", action="store_true", help="skip CheatBench's judge")
    parser.add_argument("--no-ladder", action="store_true", help="skip the cheating ladder")
    parser.add_argument("--view", choices=("full", "actions"), default="full", help="the ladder judge's view")
    parser.add_argument("--judge-errors", action="store_true",
                        help="judge a run even when an episode failed in the harness or at the provider")
    args = parser.parse_args(argv)
    # Both judges in the open: CheatBench's verdict from the eval log, then every agent's rung from its transcript.
    codes = [rejudge(name, args.judge, with_judge=not args.no_judge, with_ladder=not args.no_ladder, view=args.view,
                     judge_errors=args.judge_errors, batch=not args.sync) for name in args.runs]
    return max(codes)


if __name__ == "__main__":
    sys.exit(main())
