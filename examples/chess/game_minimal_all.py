"""Three neutral agents on the chess position LR4pA, sharing one game, with nothing split: every agent holds the request
tool and the endpoint takes no credential, as in CheatBench. Against game_minimal.py, this cell separates the access
route from the willingness to use it."""
from pathlib import Path

from examples.example_arguments import finish, parse_example_arguments
from src import run
from src.configuration.presets import build_config
from src.domains.chess import Chess


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__)
    # the midterm report's preset at the small scale (three agents, the turn budget), not judged, then whatever --set
    # says; the cell then fixes access=all
    config = build_config(args.preset, args.scale, args.settings).replace(access="all")
    benchmark = Chess("LR4pA")
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout)
    return finish(args, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
