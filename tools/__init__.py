"""The analysis tools over the runs: one package per tool, run as `uv run --project tools python -m tools <tool>`."""

DEFAULT_MODEL = "gpt-6-luna"      # the model the LLM-backed tools (scout, hawk, failures) default to: the judge's
