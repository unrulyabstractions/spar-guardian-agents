"""CheatBench's core package (submodules/cheatbench/core) on the import path, for the judge and the ATIF transcripts.

    from src.utils.cheatbench_core import judge, trajectory

`judge` is core/judge.py (the verdict schema, the prompt, the rendering), `trajectory` the ATIF models of
core/trajectory/.
"""
from __future__ import annotations

import sys

from src.configuration import settings

if str(settings.CHEATBENCH) not in sys.path:
    sys.path.insert(0, str(settings.CHEATBENCH))

from core import judge, trajectory  # noqa: E402

__all__ = ["judge", "trajectory"]
