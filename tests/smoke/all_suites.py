"""Every smoke suite at once, each in a process of its own. No model spend.

    uv run python -m tests.smoke.all_suites

Each suite's output goes to logs/study_smoke/smoke_all/<stamp>/<module>.log, such as openmath.log. A summary follows
the last one, and the exit code is 1 when any suite failed. A suite passed when it exited 0 with at least one [PASS]
line and no [FAIL] line, so a suite that reached no scenario has not passed. Every suite gets the combined run's stamp.
Its runs go to runs/study_smoke/<suite>/<stamp>/ and its eval logs to logs/study_smoke/<suite>/<stamp>/. The suites
share no directory.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from src.configuration import settings
from tests.smoke import LOGS, STAMP_VARIABLE, invocation_stamp

NOT_SUITES = {"__init__", "access_load", "all_suites", "common"}      # access_load fills the machine by itself
SUITES = sorted(path.stem for path in Path(__file__).parent.glob("*.py") if path.stem not in NOT_SUITES)
NAME = "smoke_all"
"""The combined run's folder under logs/study_smoke/."""


@dataclass
class Outcome:
    """How one suite ended."""
    suite: str
    returncode: int
    passed: int
    failed: int
    minutes: float
    log: Path

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.passed > 0 and self.failed == 0

    def line(self) -> str:
        result = "PASS" if self.ok else "FAIL"
        return (f"{self.suite:<16}{result:<8}{self.passed:>5}{self.failed:>6}{self.returncode:>5}{self.minutes:>9.1f}"
                f"  {self.log}")


def run_suite(name: str, command: list[str], logs: Path, env: dict[str, str] | None = None) -> Outcome:
    """Run one suite's command to its end under `env` (this process's when None), with its output in logs/<name>.log."""
    log = logs / f"{name}.log"
    started = time.monotonic()
    with log.open("w") as out:
        returncode = subprocess.run(command, cwd=settings.ROOT, stdout=out, stderr=subprocess.STDOUT,
                                    env=env).returncode
    lines = log.read_text(errors="replace").splitlines()
    return Outcome(name, returncode, sum(line.startswith("[PASS]") for line in lines),
                   sum(line.startswith("[FAIL]") for line in lines), (time.monotonic() - started) / 60, log)


def run_all(commands: dict[str, list[str]], logs: Path, env: dict[str, str] | None = None) -> list[Outcome]:
    """Run every command at once under `env` and wait for the last one."""
    logs.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=len(commands)) as pool:
        return list(pool.map(lambda item: run_suite(*item, logs, env), commands.items()))


def summary(outcomes: list[Outcome]) -> str:
    """One line per suite, then the count."""
    head = f"{'suite':<16}{'result':<8}{'pass':>5}{'fail':>6}{'rc':>5}{'minutes':>9}  log"
    passed = sum(o.ok for o in outcomes)
    return "\n".join([head, *(o.line() for o in outcomes), f"== {passed} of {len(outcomes)} suites passed"])


def main() -> int:
    """Every suite at once under one stamp: their outputs in logs/study_smoke/smoke_all/<stamp>/."""
    stamp = invocation_stamp()
    logs = LOGS / NAME / stamp
    print(f"== {len(SUITES)} suites at once, stamp {stamp}, logs under {logs}", flush=True)
    commands = {name: [sys.executable, "-m", f"tests.smoke.{name}"] for name in SUITES}
    outcomes = run_all(commands, logs, {**os.environ, STAMP_VARIABLE: stamp})
    print(summary(outcomes))
    return 0 if all(o.ok for o in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
