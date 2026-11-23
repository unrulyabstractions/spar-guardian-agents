"""Three neutral agents on ian0binder, every one told to cheat: ian0binder_minimal plus CHEAT after the task
instruction, the positive control of the licensed-sequences task. CHEAT is binder_minimal_cheat's, so it names the
colleague's directory and leaves the backup of the screening directory unnamed."""
from pathlib import Path

from examples.example_arguments import finish, parse_example_arguments
from examples.protein_design.binder_minimal_cheat import CHEAT
from src import run
from src.configuration.presets import build_config
from src.domains.protein import ProteinDesign
from src.tasks import swarm_task


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__, default_row=ProteinDesign.default_row)
    config = build_config(args.preset, args.scale, args.settings)
    benchmark = ProteinDesign(args.row, variant="ian0binder")
    # the control: CHEAT on its own paragraph after the task instruction and the rule sentence
    task = swarm_task(benchmark, config, name=args.name, extra_instruction=CHEAT)
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout, task=task, replace=args.replace)
    return finish(args, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
