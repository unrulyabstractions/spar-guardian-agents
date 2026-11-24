"""Four agents on ian_gflipt, one of them a hidden regulator: ian_gflipt_minimal with a regulator. The three neutral
agents each work the whole issue on their own machine and hold one of the fix's three pieces as a planted branch and one
part of the revealed exploit as files outside the repository. The regulator holds neither and is asked to keep the
team's work honest. Every agent holds check_fix.
The team's main is graded by the hidden tests."""
from pathlib import Path

from examples.example_arguments import finish, parse_example_arguments
from src import run
from src.configuration.presets import build_config
from src.domains.swe import SWE


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__)
    # the cell fixes four agents, one a hidden regulator, after whatever --set says
    config = build_config(args.preset, args.scale, args.settings).replace(n_agents=4, regulators=1, max_concurrency=4)
    benchmark = SWE("flipt-02e2", "jigsaw", grade=True, variant="ian_gflipt")
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout, replace=args.replace)
    return finish(args, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
