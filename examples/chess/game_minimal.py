"""Three neutral agents on the chess position LR4pA, sharing one game: they move in turn and reset only together.
The endpoint takes a bearer credential cut in two: one agent holds the request tool and each of the others one half
of the credential, so a request it answers takes the board."""
from pathlib import Path

from examples.example_arguments import finish, parse_example_arguments
from src import run
from src.configuration.presets import build_config
from src.domains.chess import Chess


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__)
    # By default the midterm report's preset at the small scale (three agents, no regulator, access=split, the
    # turn budget), not judged (USE_JUDGE_AS_DEFAULT), then whatever --set says.
    config = build_config(args.preset, args.scale, args.settings)
    benchmark = Chess("LR4pA")
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout)
    return finish(args, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
