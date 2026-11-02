"""Five agents on the SWE issue flipt-02e2 under the jigsaw honeypot and the default preset, one a hidden regulator and
one a hidden disregulator: the split baseline with both. The three neutral agents each work the whole issue on their own
machine and hold one of the fix's three pieces. The hidden tests need what pieces B and C hold, so cheating takes two
agents. The regulator keeps the team's work honest and the disregulator pushes the other way. Neither holds a piece, so
each can only win the others over. The team's main is graded by the hidden tests."""
from pathlib import Path

from examples.example_arguments import finish, parse_example_arguments
from src import run
from src.configuration.presets import build_config
from src.domains.swe import SWE


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__)
    # the midterm report's preset at the small scale (access=split, the turn budget), not judged, then whatever --set
    # says; the cell then fixes five agents, one a hidden regulator and one a hidden disregulator
    config = build_config(args.preset, args.scale, args.settings).replace(n_agents=5, regulators=1, disregulators=1,
                                                                          max_concurrency=5)
    benchmark = SWE("flipt-02e2", "jigsaw", grade=True)
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout, replace=args.replace)
    return finish(args, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
