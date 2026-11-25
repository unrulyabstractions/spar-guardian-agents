"""Three neutral agents on ian_gflipt, every one told to cheat: ian_gflipt_minimal plus CHEAT after the task
instruction, the positive control of the clean-room fork task with check_fix. CHEAT is ian_flipt's own
(ian_flipt_minimal_cheat), imported, so both tasks' controls read the same words."""
from pathlib import Path

from examples.example_arguments import finish, parse_example_arguments
from src import run
from src.configuration.presets import build_config
from src.domains.swe import SWE
from src.tasks import swarm_task
from studies.cells.swe.ian_flipt_minimal_cheat import CHEAT


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__)
    config = build_config(args.preset, args.scale, args.settings)
    benchmark = SWE("flipt-02e2", "jigsaw", grade=True, variant="ian_gflipt")
    # the control: CHEAT on its own paragraph after the task instruction and the rule sentence
    task = swarm_task(benchmark, config, name=args.name, extra_instruction=CHEAT)
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout, task=task, replace=args.replace)
    return finish(args, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
