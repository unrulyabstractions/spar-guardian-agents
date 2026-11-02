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

A stamp is the UTC time, such as 20261101T014617Z: ASCII digits that make a real date and time. SMOKE_STAMP is for a
parent to hand down; set by hand, it reuses a folder. smoke_load's launch refuses a stamp whose folder exists, and any
other suite would remove its earlier scenarios of the same name, so leave it unset in a shell.

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
    20261101T014617Z. A handed stamp that is not one raises ValueError: another shape, a digit outside ASCII, or a
    date or time that does not exist. No suite then writes outside its own folder."""
    handed = os.environ.get(STAMP_VARIABLE)
    if handed is None:
        return dt.datetime.now(dt.timezone.utc).strftime(STAMP_FORMAT)
    try:
        if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", handed):
            raise ValueError("not 8 and 6 ASCII digits")
        dt.datetime.strptime(handed, STAMP_FORMAT)
    except ValueError as error:
        raise ValueError(f"{STAMP_VARIABLE}={handed!r} is not a stamp such as 20261101T014617Z: {error}") from None
    return handed
