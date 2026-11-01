"""The mock-model smoke suites, one module per domain and one for the access log, on the real sandbox and with no model
spend.

    uv run python -m tests.smoke.<domain> [--only a,b]      # one suite
    make smoke-all                                          # every suite at once

common.py holds what the suites share and all_suites.py runs them together.

Every suite writes to the study study_smoke, whatever the current study (settings.STUDY). A suite's runs go under
runs/study_smoke/<suite>/<stamp>/ and its eval logs under logs/study_smoke/<suite>/<stamp>/, with a fresh UTC stamp per
invocation. The suites are smoke_openmath, smoke_swe, smoke_kw, smoke_interview, smoke_protein, smoke_creative,
smoke_chess, smoke_access and smoke_load. `make smoke-all` keeps each suite's output in
logs/study_smoke/smoke_all/<stamp>/<module>.log. Every suite of that combined run writes under the same stamp. The
browser shows one invocation's runs: `uv run python -m src.ui --runs runs/study_smoke/<suite>/<stamp>`.

all_suites reads the names below from here, without importing Inspect or the domains.
"""
from __future__ import annotations

import datetime as dt
import os
import re

from src.configuration import settings

STUDY = "study_smoke"
RUNS = settings.ROOT / "runs" / STUDY
LOGS = settings.ROOT / "logs" / STUDY
STAMP_VARIABLE = "SMOKE_STAMP"
"""A parent hands its stamp to its children in this environment variable: all_suites to every suite, and access_load
to every sample."""
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def invocation_stamp() -> str:
    """The stamp of this invocation's directories: the one a parent handed down, else the UTC time now, such as
    20261101T014617Z. A handed stamp of another shape raises ValueError. No suite then writes outside its own folder."""
    handed = os.environ.get(STAMP_VARIABLE)
    if handed is None:
        return dt.datetime.now(dt.timezone.utc).strftime(STAMP_FORMAT)
    if not re.fullmatch(r"\d{8}T\d{6}Z", handed):
        raise ValueError(f"{STAMP_VARIABLE}={handed!r} is not a stamp such as 20261101T014617Z")
    return handed
