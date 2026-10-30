"""A screen: the example cells on one model, several samples each, as many runs at once as the machine holds.

    uv run python -m scripts.screen --model deepseek-flash-openrouter --samples 3 --jobs 9 [--suffix flash]
                                    [--cells openmath.riemann_solo ...] [--summary-only]
                                    [--preset P] [--scale S] [--set key=value ...]
                                    [--worker-model M] [--regulator-model M] [--disregulator-model M]

Each run is `python -m examples.<domain>.<cell> --model <model> --name <cell>_<suffix><k>` for sample k, its output
in logs/screen/<name>.log; a run whose runs/<name>/signals.jsonl exists already is skipped, so a stopped screen resumes.
The default cells are every example's solo, solo_cheat and minimal_cheat scripts (examples/<domain>/<stem>_<cell>.py).
The config flags are the command line's (presets.add_config_arguments) and go to every run: --preset and --scale as
they are, every --set (`--set turn_budget=60 --set reasoning_effort=low`) and then each role model flag as a
`<role>_model` pair.

The summary at the end, or alone with --summary-only, gives each finished run's rungs (signals.jsonl) and its ledger
(run.json ledger: the model calls, the tokens, what the provider billed and the cost at the registry's list prices,
each agent at its own model), and the totals. A run without a ledger shows none.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from src.configuration import settings
from src.configuration.presets import add_config_arguments, config_pairs
from src.utils.json_files import read_json, read_jsonl

CELL_KINDS = ("solo", "solo_cheat", "minimal_cheat")
CELLS = sorted(f"{path.parent.name}.{path.stem}" for kind in CELL_KINDS
               for path in (settings.ROOT / "examples").glob(f"*/*_{kind}.py"))
LOGS = settings.LOGS / "screen"


def run_name(cell: str, suffix: str, sample: int) -> str:
    return f"{cell.split('.', 1)[1]}_{suffix}{sample}"


def done(name: str) -> bool:
    return (settings.RUNS / name / "signals.jsonl").is_file()


def launch(cell: str, name: str, flags: list[str]) -> tuple[str, int, float]:
    """One run as a subprocess with the screen's config flags, its output in LOGS/<name>.log; (name, exit code,
    minutes)."""
    LOGS.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", f"examples.{cell}", "--name", name, *flags]
    started = time.time()
    with (LOGS / f"{name}.log").open("w") as log:
        log.write(" ".join(command) + "\n")
        log.flush()
        code = subprocess.run(command, cwd=settings.ROOT, stdout=log, stderr=subprocess.STDOUT).returncode
    return name, code, (time.time() - started) / 60


def money(value: float | None) -> str:
    return "-" if value is None else f"${value:.3f}"


def summary(names: list[str]) -> str:
    """One line per finished run: the agents' rungs and the run's ledger totals, then the sums over the runs."""
    lines = [f"{'run':34s} {'rungs':42s} {'calls':>6s} {'prompt':>8s} {'out':>6s} {'billed':>8s} {'list':>8s}"]
    billed = listed = 0.0
    for name in names:
        if not done(name):
            lines.append(f"{name:34s} (not finished)")
            continue
        run_dir = settings.RUNS / name
        rungs = " ".join(f"{row['agent']}:{row['rung']}" for row in read_jsonl(run_dir / "signals.jsonl"))
        ledger = (read_json(run_dir / "run.json") or {}).get("ledger")
        if ledger is None:
            lines.append(f"{name:34s} {rungs:42s} (no ledger)")
            continue
        total = ledger["total"]
        billed += total["billed_usd"] or 0.0
        listed += total["cost_usd"] or 0.0
        lines.append(f"{name:34s} {rungs:42s} {total['calls']:6d} {total['tokens']['input'] / 1e6:7.2f}M "
                     f"{total['tokens']['output'] / 1e3:5.0f}k {money(total['billed_usd']):>8s} "
                     f"{money(total['cost_usd']):>8s}{'' if ledger['complete'] else ' (incomplete)'}")
    lines.append(f"\nbilled ${billed:.3f} in all; ${listed:.3f} at the registry's list prices")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_config_arguments(parser)
    parser.add_argument("--samples", type=int, default=1, help="runs per cell")
    parser.add_argument("--jobs", type=int, default=9, help="runs at once")
    parser.add_argument("--suffix", default="screen", help="run names are <cell>_<suffix><k>")
    parser.add_argument("--cells", nargs="*", default=CELLS, help="examples.<domain>.<cell> without the prefix")
    parser.add_argument("--summary-only", action="store_true", help="print the summary of what exists and stop")
    args = parser.parse_args(argv)
    flags = ["--model", args.model, "--preset", args.preset, *(["--scale", args.scale] if args.scale else [])]
    flags += [arg for pair in config_pairs(args) for arg in ("--set", pair)]
    names = [run_name(cell, args.suffix, k) for k in range(1, args.samples + 1) for cell in args.cells]
    if not args.summary_only:
        todo = [(cell, run_name(cell, args.suffix, k)) for k in range(1, args.samples + 1) for cell in args.cells
                if not done(run_name(cell, args.suffix, k))]
        print(f"== screen {args.model}: {len(todo)} runs to go of {len(names)}, {args.jobs} at once", flush=True)
        failures = 0
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for name, code, minutes in pool.map(lambda job: launch(job[0], job[1], flags), todo):
                failures += code != 0
                print(f"   {name:34s} rc={code} {minutes:5.1f} min", flush=True)
        if failures:
            print(f"== {failures} run(s) failed; their logs are under {LOGS}", flush=True)
    print(summary(names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
