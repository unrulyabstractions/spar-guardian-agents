"""Four agents on the knowledge-work row 2ea2e5b5 under the default preset, one of them a hidden regulator: the split
baseline with a regulator. The three neutral agents share one contract directory; two hold the review tool, which
names the client's cached artifact, and one holds the permission to read it, so opening that artifact takes the
board. The regulator holds neither and is asked to keep the team's work honest."""
from pathlib import Path

from examples.example_arguments import finish, parse_example_arguments
from src import run
from src.configuration.presets import build_config
from src.domains.knowledge import KnowledgeWork


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__)
    # the midterm report's preset at the small scale (access=split, the turn budget), not judged, then whatever --set
    # says; the cell then fixes four agents, one a hidden regulator
    config = build_config(args.preset, args.scale, args.settings).replace(n_agents=4, regulators=1, max_concurrency=4)
    benchmark = KnowledgeWork("2ea2e5b5")
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout)
    return finish(args, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
