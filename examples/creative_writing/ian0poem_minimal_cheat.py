"""Three neutral agents on ian0poem, every one told to cheat: ian0poem_minimal plus CHEAT after the task instruction,
the positive control of the commissioned poem. CHEAT is poem_minimal_cheat's, so it names the grader's config and the
reference and leaves the side folder unnamed."""
from pathlib import Path

from examples.creative_writing.poem_minimal_cheat import CHEAT
from examples.example_arguments import finish, parse_example_arguments
from src import run
from src.configuration.presets import build_config
from src.domains.creative import CreativeWriting
from src.tasks import swarm_task


def main() -> int:
    args = parse_example_arguments(Path(__file__), __doc__)
    config = build_config(args.preset, args.scale, args.settings)
    benchmark = CreativeWriting("museum_poem", variant="ian0poem")
    # the control: CHEAT on its own paragraph after the task instruction and the rule sentence
    task = swarm_task(benchmark, config, name=args.name, extra_instruction=CHEAT)
    exit_code = run.run(args.name, benchmark, config, args.model, args.timeout, task=task, replace=args.replace)
    return finish(args, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
