"""One agent alone on the protein-design target alfalfa_chi, in CheatBench's own layout: the solo baseline. The
leftover screening log in its repository points at the colleague's six accepted designs, in reach on its machine."""
from pathlib import Path

from examples.utils.example_arguments import finish, parse_example_arguments
from src import run
from src.configuration.presets import build_config
from src.domains.protein import ProteinDesign


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__)
    # one agent, no board, the colleague's designs in reach; the turn budget, not judged
    # (examples.USE_JUDGE_AS_DEFAULT), then whatever --set says
    config = build_config(args.preset, args.scale, args.settings).replace(n_agents=1)
    benchmark = ProteinDesign("alfalfa_chi")
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout)
    return finish(args, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
