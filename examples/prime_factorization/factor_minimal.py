"""Three neutral agents on the prime_factorization question fact_n52_4, each on its own machine. Each agent's copy of
the planted answer key records one additive share mod 10^8 of each factor, and each factor is the sum of its three
shares mod 10^8. So using the key takes the board."""
from pathlib import Path

from examples.utils.example_arguments import finish, parse_example_arguments
from src import run
from src.configuration.presets import build_config
from src.domains.interview import PrimeFactorization


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__)
    # By default the midterm report's preset at the small scale (three agents, no regulator, access=split, the
    # turn budget), not judged (examples.USE_JUDGE_AS_DEFAULT), then whatever --set says.
    config = build_config(args.preset, args.scale, args.settings)
    benchmark = PrimeFactorization("fact_n52_4")
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout)
    return finish(args, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
