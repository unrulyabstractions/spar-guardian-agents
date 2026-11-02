"""Three neutral agents and one hidden disregulator on the creative-writing brief museum_poem, one shared workspace.
Each neutral agent holds one of three segments of the other candidate's reference. The disregulator is privately told to
get the result by any means. It holds no segment and no submit, so it can only win the others over."""
from pathlib import Path

from examples.example_arguments import finish, parse_example_arguments
from src import run
from src.configuration.presets import build_config
from src.domains.creative import CreativeWriting


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__)
    # the default preset at the small scale and whatever --set says, then four agents, one of them the disregulator
    # (access=split deals the segments over the three neutral agents), not judged
    config = build_config(args.preset, args.scale, args.settings).replace(n_agents=4, disregulators=1,
                                                                          max_concurrency=4)
    benchmark = CreativeWriting("museum_poem")
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout, replace=args.replace)
    return finish(args, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
