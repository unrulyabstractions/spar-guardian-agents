"""One agent alone on the knowledge-work row 2ea2e5b5, in CheatBench's own layout: the solo baseline. It holds the
review tool, which names the client's cached artifact, and the permission to read it."""
from pathlib import Path

from examples.example_arguments import finish, parse_example_arguments
from src import run
from src.configuration.presets import build_config
from src.domains.knowledge import KnowledgeWork


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__)
    # one agent, no board, the artifact in reach; the turn budget, not judged
    # (USE_JUDGE_AS_DEFAULT), then whatever --set says
    config = build_config(args.preset, args.scale, args.settings).replace(n_agents=1)
    benchmark = KnowledgeWork("2ea2e5b5")
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout)
    return finish(args, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
