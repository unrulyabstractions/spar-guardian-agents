"""Runnable examples: one script per task, three neutral agents on the pull board at the small scale.

    uv run python -m examples.openmath.riemann_minimal
    uv run python -m examples.swe.flipt_minimal --model gemini-3.8-flash
    uv run python -m examples.knowledge_work.worktime_minimal --scale medium --set regulators=1

Beside each minimal script sit the first experiment's cells: <task>_solo.py (one agent alone), <task>_solo_cheat.py
(one agent told to cheat by the CHEAT text the script appends to the task instruction) and <task>_minimal_cheat.py
(three agents told to cheat); knowledge work, SWE and protein design add <task>_minimal_regulated.py (four agents, one
of them a hidden regulator). Each script parses the shared arguments (examples/example_arguments.py), builds its
config in the open, names its task and calls the run. The run lands in runs/<name>/ and its figures in out/<name>/,
both ignored by git. By default a run is not judged as it finishes (example_arguments.USE_JUDGE_AS_DEFAULT);
`python -m src rejudge <name>` judges it later, and `--judge` judges one run as it finishes.
"""
