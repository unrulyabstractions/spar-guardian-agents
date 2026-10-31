"""Three neutral agents on the knowledge-work row 2ea2e5b5, one shared contract directory, with no split: each holds the
review tool, which names the client's cached artifact, and the permission to read it. Beside worktime_minimal this cell
separates access from willingness: opening that artifact takes no teammate here."""
from pathlib import Path

from examples.example_arguments import finish, parse_example_arguments
from src import run
from src.configuration.presets import build_config
from src.domains.knowledge import KnowledgeWork


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__)
    # the midterm report's preset at the small scale (three agents, no regulator, the turn budget), not judged, then
    # whatever --set says; the cell then fixes access=all
    config = build_config(args.preset, args.scale, args.settings).replace(access="all")
    benchmark = KnowledgeWork("2ea2e5b5")
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout)
    return finish(args, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
