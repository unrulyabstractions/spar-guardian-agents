"""Three neutral agents on ian_gflipt: ian_flipt_minimal with check_fix, a tool that runs the customer's tests on
team/main and answers only accepted or not_accepted. The task, the pieces, the revealed exploit, the deals and the
final grade are ian_flipt's. The team's main is graded by the hidden tests."""
from pathlib import Path

from examples.example_arguments import finish, parse_example_arguments
from src import run
from src.configuration.presets import build_config
from src.domains.swe import SWE


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__)
    config = build_config(args.preset, args.scale, args.settings)
    benchmark = SWE("flipt-02e2", "jigsaw", grade=True, variant="ian_gflipt")
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout, replace=args.replace)
    return finish(args, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
