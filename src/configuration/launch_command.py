"""The command line that launched a run, for run.json "launch", so a run can be launched again as it was.

    launch_command.record("examples.swe.flipt_minimal", sys.argv[1:])     # examples/example_arguments.py, src/cli.py
    launch_command.current()       # {"module", "argv", "study", "command"}, or None in a process that recorded none

The example scripts (examples/example_arguments.py parse_example_arguments) and `python -m src run` record their module
and their arguments as given, before any default is added. tasks.record_run copies the record into the sample's
metadata, and the export writes it to run.json "launch". `command` is the line to type again from the repository root:
the study as an environment variable, then `uv run python -m <module> <argv>`. A key never reaches it: the key lives in
the environment (~/.zshrc), and any argument that looks like one, or a `<name>=<value>` whose name says key, token,
secret or password, is written as REDACTED.
"""
from __future__ import annotations

import re
import shlex

from src.configuration import settings

REDACTED = "REDACTED"
SECRET = re.compile(r"\b(sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,}|hf_[A-Za-z0-9]{8,})")
SECRET_NAME = re.compile(r"(key|token|secret|password)", re.I)

_LAUNCH: dict | None = None


def redact(arg: str) -> str:
    """The argument with any key in it replaced by REDACTED."""
    name, sep, value = arg.partition("=")
    if sep and SECRET_NAME.search(name) and value:
        return f"{name}={REDACTED}"
    return SECRET.sub(REDACTED, arg)


def record(module: str, argv: list[str], study: str | None = None) -> dict:
    """Keep this process's launch: the module run with `python -m`, its arguments (redacted) and the study."""
    global _LAUNCH
    args = [redact(a) for a in argv]
    study = study or settings.STUDY
    _LAUNCH = {"module": module, "argv": args, "study": study,
               "command": f"STUDY={shlex.quote(study)} uv run python -m {module} {shlex.join(args)}".rstrip()}
    return dict(_LAUNCH)


def current() -> dict | None:
    """This process's launch, or None when nothing recorded one (a test, a notebook, a script of its own)."""
    return dict(_LAUNCH) if _LAUNCH else None
