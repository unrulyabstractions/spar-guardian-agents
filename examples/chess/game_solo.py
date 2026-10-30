"""One agent alone on the chess position LR4pA, in CheatBench's own episode: the solo baseline. The opponent's client
in its checkout names the other agent's endpoint, which ranks the best moves of any position, and api_request reaches
it."""
from pathlib import Path

from examples.example_arguments import finish, parse_example_arguments
from src import run
from src.configuration.presets import build_config
from src.domains.chess import Chess


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__)
    # one agent, no board, the endpoint in reach; the turn budget, not judged (USE_JUDGE_AS_DEFAULT), then whatever
    # --set says
    config = build_config(args.preset, args.scale, args.settings).replace(n_agents=1)
    benchmark = Chess("LR4pA")
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout)
    return finish(args, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
