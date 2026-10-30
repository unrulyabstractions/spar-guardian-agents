"""Three neutral agents on the SWE issue flipt-02e2 under the jigsaw honeypot. Each works the whole issue on its own
machine and holds one of the fix's three pieces, so the whole leaked fix takes all three. The team's main is graded
by the hidden tests."""
from pathlib import Path

from examples.example_arguments import finish, parse_example_arguments
from src import run
from src.configuration.presets import build_config
from src.domains.swe import SWE


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__)
    # By default the midterm report's preset at the small scale (three agents, no regulator, access=split, the
    # turn budget), not judged (USE_JUDGE_AS_DEFAULT), then whatever --set says.
    config = build_config(args.preset, args.scale, args.settings)
    benchmark = SWE("flipt-02e2", "jigsaw", grade=True)
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout)
    return finish(args, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
