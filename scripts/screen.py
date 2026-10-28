"""A screen: the example cells on one model, several samples each, as many runs at once as the machine holds.

    uv run python scripts/screen.py --model deepseek-flash-openrouter --samples 3 --jobs 9 [--suffix flash]
                                    [--cells openmath.riemann_solo ...] [--set key=value ...] [--summary-only]
                                    [--worker-model M] [--regulator-model M] [--disregulator-model M]

Each run is `python -m examples.<domain>.<cell> --model <entry> --name <cell>_<suffix><k>` for sample k, its output
in logs/screen/<name>.log; a run whose runs/<name>/signals.jsonl exists already is skipped, so a stopped screen resumes.
The default cells are the three per domain of the first experiment (solo, solo_cheat, minimal_cheat) on openmath,
knowledge work and SWE. --set pairs go to every run (`--set turn_budget=60 --set reasoning_effort=low`). A role model
flag (--worker-model, --regulator-model, --disregulator-model) goes to every run as a `<role>_model` pair after them.
The summary at the end, or alone with --summary-only, gives each run's rungs, turns, tokens and what the provider
billed (episodes.jsonl usage.billed_usd) beside the estimate at the registry's prices, and the totals. The estimate
prices every agent at --model's rates. The summary notes this when a role model is set.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.configuration import models, settings  # noqa: E402
from src.configuration.swarm_config import ROLES, role_model_pairs  # noqa: E402

CELLS = ["openmath.riemann_solo", "openmath.riemann_solo_cheat", "openmath.riemann_minimal_cheat",
         "knowledge_work.worktime_solo", "knowledge_work.worktime_solo_cheat", "knowledge_work.worktime_minimal_cheat",
         "swe.flipt_solo", "swe.flipt_solo_cheat", "swe.flipt_minimal_cheat"]
LOGS = settings.LOGS / "screen"


def run_name(cell: str, suffix: str, sample: int) -> str:
    return f"{cell.split('.', 1)[1]}_{suffix}{sample}"


def done(name: str) -> bool:
    return (settings.RUNS / name / "signals.jsonl").is_file()


def launch(cell: str, name: str, model: str, pairs: list[str]) -> tuple[str, int, float]:
    """One run as a subprocess, its output in LOGS/<name>.log; (name, exit code, minutes)."""
    LOGS.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", f"examples.{cell}", "--model", model, "--name", name]
    for pair in pairs:
        command += ["--set", pair]
    started = time.time()
    with (LOGS / f"{name}.log").open("w") as log:
        log.write(" ".join(command) + "\n")
        log.flush()
        code = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode
    return name, code, (time.time() - started) / 60


def usage_of(run_dir: Path) -> dict:
    """The run's tokens and billed cost summed over its episodes."""
    totals = {"prompt": 0, "cached": 0, "output": 0, "billed": 0.0, "billed_calls": 0}
    for line in (run_dir / "episodes.jsonl").read_text().splitlines():
        usage = json.loads(line).get("usage") or {}
        totals["prompt"] += usage.get("input", 0)
        totals["cached"] += usage.get("cache_read", 0)
        totals["output"] += usage.get("output", 0)
        totals["billed"] += usage.get("billed_usd", 0.0)
        totals["billed_calls"] += usage.get("billed_calls", 0)
    return totals


def estimate(model: str, usage: dict) -> float | None:
    """The run's cost at the registry's prices (cache reads at the cached rate), or None outside the registry."""
    price = models.usd_per_million(model)
    if price is None:
        return None
    cached = price.get("cached_input", price["input"])
    return ((usage["prompt"] - usage["cached"]) * price["input"] + usage["cached"] * cached
            + usage["output"] * price["output"]) / 1e6


def summary(names: list[str], model: str, role_pairs: list[str] | None = None) -> str:
    """One line per finished run: the agents' rungs, the calls, the tokens, the billed cost and the estimate. The
    estimate prices every agent at the model's rates; role_pairs are the role models the screen set."""
    lines = [f"{'run':34s} {'rungs':42s} {'calls':>6s} {'prompt':>8s} {'out':>6s} {'billed':>8s} {'estimate':>9s}"]
    billed_total = est_total = 0.0
    for name in names:
        run_dir = settings.RUNS / name
        if not done(name):
            lines.append(f"{name:34s} (not finished)")
            continue
        rows = [json.loads(line) for line in (run_dir / "signals.jsonl").read_text().splitlines()]
        usage = usage_of(run_dir)
        est = estimate(model, usage)
        billed_total += usage["billed"]
        est_total += est or 0.0
        rungs = " ".join(f"{r['agent']}:{r['rung']}" for r in rows)
        calls = "/".join(str(r.get("calls", "")) for r in rows)
        lines.append(f"{name:34s} {rungs:42s} {calls:>6s} {usage['prompt'] / 1e6:7.2f}M {usage['output'] / 1e3:5.0f}k "
                     f"${usage['billed']:7.3f} {'$%8.3f' % est if est is not None else '        -'}")
    lines.append(f"\nbilled ${billed_total:.3f} in all; ${est_total:.3f} at the registry's prices for {model}")
    if role_pairs:
        lines.append(f"the estimate assumes one model, {model}, for every agent; this screen also sets "
                     f"{', '.join(role_pairs)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=settings.DEFAULT_MODEL, help="a registry entry or an Inspect model string")
    parser.add_argument("--samples", type=int, default=1, help="runs per cell")
    parser.add_argument("--jobs", type=int, default=9, help="runs at once")
    parser.add_argument("--suffix", default="screen", help="run names are <cell>_<suffix><k>")
    parser.add_argument("--cells", nargs="*", default=CELLS, help="examples.<domain>.<cell> without the prefix")
    parser.add_argument("--set", dest="pairs", action="append", default=[], help="a SwarmConfig field for every run")
    for role in ROLES:
        parser.add_argument(f"--{role}-model", metavar="MODEL", default=None,
                            help=f"the {role}s' model for every run, a registry entry or an Inspect model string")
    parser.add_argument("--summary-only", action="store_true", help="print the summary of what exists and stop")
    args = parser.parse_args(argv)
    model = models.resolve(args.model)
    try:
        role_pairs = role_model_pairs({role: getattr(args, f"{role}_model") for role in ROLES})
    except ValueError as error:
        parser.error(str(error))
    pairs = args.pairs + role_pairs
    names = [run_name(cell, args.suffix, k) for k in range(1, args.samples + 1) for cell in args.cells]
    if not args.summary_only:
        todo = [(cell, run_name(cell, args.suffix, k)) for k in range(1, args.samples + 1) for cell in args.cells
                if not done(run_name(cell, args.suffix, k))]
        print(f"== screen {args.model}: {len(todo)} runs to go of {len(names)}, {args.jobs} at once", flush=True)
        failures = 0
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for name, code, minutes in pool.map(lambda job: launch(job[0], job[1], args.model, pairs), todo):
                failures += code != 0
                print(f"   {name:34s} rc={code} {minutes:5.1f} min", flush=True)
        if failures:
            print(f"== {failures} run(s) failed; their logs are under {LOGS}", flush=True)
    print(summary(names, model, role_pairs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
