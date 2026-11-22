"""replay.json, a replay's report, and the faithful gate every reader of steps.jsonl passes first.

    report = ReplayReport(run="flipt_minimal_luna_fixed1", faithful=True, ...)
    write_report(out_dir, report)
    steps = faithful_steps(out_dir)       # steps.jsonl's lines in full; NotFaithful unless replay.json says faithful

A replay is faithful when its end state equals the stored one (every diff of swe/diffs byte-identical, the team
remote's refs and log, and each agent's log, status and branch) and, for a run made since the round clock, its command
jobs played as recorded (replay_jobs.py). A replay that was refused, failed or ended elsewhere
is not, and its steps.jsonl must not be read as the run's states. Pure apart from the files it reads and writes.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from src.replay.step_snapshots import decode

REPORT = "replay.json"
STEPS = "steps.jsonl"
SCHEMA = 1


class NotFaithful(ValueError):
    """steps.jsonl was asked for where replay.json does not say the replay was faithful."""


@dataclass
class ReplayReport:
    """What a replay found. match counts the sandbox calls by class (exact, masked, order, differs), causes the
    differences by likely cause, host the calls replayed from their recorded output, not_run the calls that never
    ran in the run (refused arguments), shared the sandbox calls that ran alone because they may touch the team
    remote. end compares the end state with the stored one, part by part; seconds holds each phase's wall time and
    the calls' and snapshots' summed time, slowest the slowest calls. options holds the opt-in remedies the replay ran
    with ({"pem": bool, "warm_image": bool}), swaps each call whose drawn certificates gave way to the recorded ones
    (random_content.py). jobs, for a run made since the round clock, holds each recorded job against its replay and the
    problems that make the replay unfaithful (replay_jobs.py); None for an older run. read_before lists each call
    that read the team remote before an overlapping write, by the record's evidence, and played against the remote's
    refs as they stood before it (replay_run.play_before)."""
    run: str
    run_dir: str
    faithful: bool
    refused: str | None = None
    error: str | None = None
    task: str | None = None
    calls: int = 0
    sandbox_calls: int = 0
    host: int = 0
    not_run: int = 0
    match: dict[str, int] = field(default_factory=dict)
    causes: dict[str, int] = field(default_factory=dict)
    mismatches: list[dict] = field(default_factory=list)
    dated: list[dict] = field(default_factory=list)
    undated: list[dict] = field(default_factory=list)
    setup: dict = field(default_factory=dict)
    end: dict = field(default_factory=dict)
    image: dict = field(default_factory=dict)
    wall_seconds: float = 0.0
    seconds: dict[str, float] = field(default_factory=dict)
    slowest: list[dict] = field(default_factory=list)
    shared: int = 0
    parallel: bool = True
    options: dict = field(default_factory=dict)
    swaps: list[dict] = field(default_factory=list)
    read_before: list[dict] = field(default_factory=list)
    jobs: dict | None = None
    code: dict = field(default_factory=dict)
    schema: int = SCHEMA

    def shares(self) -> dict[str, float]:
        """Each match class's share of the sandbox calls."""
        total = sum(self.match.values())
        return {k: round(v / total, 4) for k, v in self.match.items()} if total else {}


def write_report(out_dir: Path, report: ReplayReport) -> Path:
    path = Path(out_dir) / REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({**asdict(report), "shares": report.shares()}, indent=1))
    tmp.replace(path)
    return path


def read_report(out_dir: Path) -> dict | None:
    path = Path(out_dir) / REPORT
    return json.loads(path.read_text()) if path.is_file() else None


def is_faithful(report: dict | None) -> bool:
    """Whether a report says the replay was faithful: of this schema, not refused, no error, and the end state
    equal."""
    return bool(report) and report.get("schema") == SCHEMA and report.get("faithful") is True \
        and not report.get("refused") and not report.get("error")


def faithful_steps(out_dir: Path) -> list[dict]:
    """steps.jsonl's lines with every state in full, only from a faithful replay; NotFaithful otherwise."""
    report = read_report(out_dir)
    if not is_faithful(report):
        why = "no replay.json" if report is None else report.get("refused") or report.get("error") or \
            "its end state differs from the stored one"
        raise NotFaithful(f"{out_dir}: the replay is not faithful ({why}); its steps are not the run's states")
    lines = [json.loads(line) for line in (Path(out_dir) / STEPS).read_text().splitlines() if line.strip()]
    return decode(lines)
