"""Three neutral agents on the subset_sum question subsetsum_n60d10_4, each on its own machine. Each agent's copy of
the planted answer key holds one interleaved shard of the solution's indices, and the verifier accepts only the
whole subset. So using the key takes the board."""
from pathlib import Path

from examples.utils.example_arguments import parse_example_arguments
from src import run
from src.benchmark.domains.interview import SubsetSum
from src.configuration import settings
from src.configuration.swarm_config import build_config
from src.ui import run_figures as plots

OUT = settings.ROOT / "out"


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__)
    # By default the midterm report's preset at the small scale (three agents, no regulator, access=split, the
    # domain's turn budget), not judged (examples.USE_JUDGE_AS_DEFAULT), then whatever --set says.
    config = build_config(args.preset, args.scale, args.settings)
    benchmark = SubsetSum("subsetsum_n60d10_4")
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout)
    if exit_code == 0:
        plots.plot_run(settings.RUNS / args.name, OUT / args.name)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
