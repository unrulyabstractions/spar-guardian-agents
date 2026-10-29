"""Three neutral agents on the knowledge-work row 2ea2e5b5, one shared contract directory. Two hold the review tool,
which names the client's cached artifact; one holds the permission to read it. So opening that artifact takes the
board."""
from pathlib import Path

from examples.utils.example_arguments import parse_example_arguments
from src import run
from src.benchmark.domains.knowledge import KnowledgeWork
from src.configuration import settings
from src.configuration.swarm_config import build_config
from src.ui import run_figures as plots

OUT = settings.ROOT / "out"


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__)
    # By default the midterm report's preset at the small scale (three agents, no regulator, access=split, the
    # domain's turn budget), an hour for the agents and a synchronous judge, then whatever --set says.
    config = build_config(args.preset, args.scale, args.settings)
    benchmark = KnowledgeWork("2ea2e5b5")
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout)
    if exit_code == 0:
        plots.plot_run(settings.RUNS / args.name, OUT / args.name)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
