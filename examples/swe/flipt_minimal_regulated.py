"""Four agents on the SWE issue flipt-02e2 under the jigsaw honeypot and the default preset, one of them a hidden
regulator: the split baseline with a regulator. The three neutral agents each work the whole issue on their own
machine and hold one of the fix's three pieces, so the whole leaked fix takes all three. The regulator holds no piece
and is asked to keep the team's work honest. The team's main is graded by the hidden tests."""
from pathlib import Path

from examples.example_arguments import finish, parse_example_arguments
from src import run
from src.configuration.presets import build_config
from src.domains.swe import SWE


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__)
    # the midterm report's preset at the small scale (access=split, the turn budget), not judged, then whatever --set
    # says; the cell then fixes four agents, one a hidden regulator
    config = build_config(args.preset, args.scale, args.settings).replace(n_agents=4, regulators=1)
    benchmark = SWE("flipt-02e2", "jigsaw", grade=True)
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout)
    return finish(args, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
