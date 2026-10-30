"""Three neutral agents and one regulator on the creative-writing brief museum_poem, one shared workspace. Each
neutral agent holds one of three segments of the other candidate's reference; the regulator holds none and no
submit."""
from pathlib import Path

from examples.example_arguments import finish, parse_example_arguments
from src import run
from src.configuration.presets import build_config
from src.domains.creative import CreativeWriting


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__)
    # the default preset at the small scale and whatever --set says, then four agents, one of them the regulator
    # (access=split deals the segments over the three neutral agents), not judged
    config = build_config(args.preset, args.scale, args.settings).replace(n_agents=4, regulators=1)
    benchmark = CreativeWriting("museum_poem")
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout)
    return finish(args, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
