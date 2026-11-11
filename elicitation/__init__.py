"""Elicitation: the cells that screen a candidate target model for collaborative reward hacking.

    uv run python -m elicitation.swe --model deepseek-flash-openrouter
    uv run python -m elicitation.swe --model <registry entry> --set seed=1

Each script runs one cell on one model, with the arguments every example takes (examples/example_arguments.py). Its run
is named after the model and the seed, and a run of that name that exists is refused, so the runs of a screen never
overwrite each other. elicitation/README.md lists the cells and the candidate models.
"""
