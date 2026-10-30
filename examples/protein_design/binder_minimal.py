"""Three neutral agents on the protein-design target alfalfa_chi, each on its own machine. Each holds one of three
segments of every design the colleague submitted, so a design the checker accepts takes the board."""
from pathlib import Path

from examples.utils.example_arguments import finish, parse_example_arguments
from src import run
from src.configuration.swarm_config import build_config
from src.domains.protein import ProteinDesign


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__)
    # By default the midterm report's preset at the small scale (three agents, no regulator, access=split, the
    # domain's turn budget), not judged (examples.USE_JUDGE_AS_DEFAULT), then whatever --set says.
    config = build_config(args.preset, args.scale, args.settings)
    benchmark = ProteinDesign("alfalfa_chi")
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout)
    return finish(args, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
