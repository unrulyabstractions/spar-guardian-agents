"""Three neutral agents on ian_binder: the protein-design target alfalfa_chi with each agent's candidates file also in
the screening directory's backup on its own machine, and a client's request in place of CheatBench's sentence that the
work is for a manager to assess. No notes file is planted. Each agent holds one of three segments of every design by
either route, so a design the checker accepts still takes the board."""
from pathlib import Path

from examples.example_arguments import finish, parse_example_arguments
from src import run
from src.configuration.presets import build_config
from src.domains.protein import ProteinDesign


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__, default_row=ProteinDesign.default_row)
    config = build_config(args.preset, args.scale, args.settings)
    benchmark = ProteinDesign(args.row, variant="ian_binder")
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout, replace=args.replace)
    return finish(args, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
