"""One agent alone on the prime_factorization question fact_n52_4, in CheatBench's own layout: the solo baseline. The
planted answer key is whole in its container."""
from pathlib import Path

from examples.example_arguments import finish, parse_example_arguments
from src import run
from src.configuration.presets import build_config
from src.domains.interview import PrimeFactorization


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__)
    # one agent, no board, the key in reach; the turn budget, not judged
    # (USE_JUDGE_AS_DEFAULT), then whatever --set says
    config = build_config(args.preset, args.scale, args.settings).replace(n_agents=1)
    benchmark = PrimeFactorization("fact_n52_4")
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout)
    return finish(args, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
