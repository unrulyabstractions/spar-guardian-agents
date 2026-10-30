"""One agent alone on the SWE issue flipt-02e2 under the jigsaw honeypot: the solo baseline. The whole leaked fix
sits on a planted branch of its clone, and its checkout is graded by the hidden tests."""
from pathlib import Path

from examples.example_arguments import finish, parse_example_arguments
from src import run
from src.configuration.presets import build_config
from src.domains.swe import SWE


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__)
    # one agent, no board, the whole fix in reach; the turn budget, not judged
    # (USE_JUDGE_AS_DEFAULT), then whatever --set says
    config = build_config(args.preset, args.scale, args.settings).replace(n_agents=1)
    benchmark = SWE("flipt-02e2", "jigsaw", grade=True)
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout)
    return finish(args, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
