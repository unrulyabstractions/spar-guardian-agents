"""One agent alone on the creative-writing brief museum_poem, in CheatBench's own layout: the solo baseline. The
grader's logs name its config, and the config names the other candidate's reference, in reach on its machine."""
from pathlib import Path

from examples.example_arguments import finish, parse_example_arguments
from src import run
from src.configuration.presets import build_config
from src.domains.creative import CreativeWriting


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__)
    # one agent, no board, the reference in reach; the turn budget, not judged (USE_JUDGE_AS_DEFAULT), then whatever
    # --set says
    config = build_config(args.preset, args.scale, args.settings).replace(n_agents=1)
    benchmark = CreativeWriting("museum_poem")
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout)
    return finish(args, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
