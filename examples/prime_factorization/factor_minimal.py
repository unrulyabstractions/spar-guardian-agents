"""Three neutral agents on the prime_factorization question fact_n52_4, each on its own machine. Each agent's copy of
the planted answer key records one additive share of each factor, and the factors are the sum of all three shares.
So using the key takes the board."""
from pathlib import Path

from examples.utils.cli import parse_example_arguments
from src import run
from src.benchmark.domains.interview import PrimeFactorization
from src.configuration import settings
from src.configuration.swarm_config import build_config
from src.ui import plots

OUT = settings.ROOT / "out"


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__)
    # By default the midterm report's preset at the small scale (three agents, no regulator, access=split, the
    # domain's turn budget), an hour for the agents, not judged (examples.USE_JUDGE_AS_DEFAULT), then whatever --set
    # says.
    config = build_config(args.preset, args.scale, args.settings)
    benchmark = PrimeFactorization("fact_n52_4")
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout)
    if exit_code == 0:
        plots.plot_run(settings.RUNS / args.name, OUT / args.name)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
