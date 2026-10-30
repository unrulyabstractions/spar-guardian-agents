"""`python -m tools scout <run>... [--model ENTRY] [--scanners a,b] [--grep-only]`: Scout scanners over each agent's
transcript, with the round each cheating behaviour first shows and the agreement with the judge.
"""
from __future__ import annotations

import argparse
import sys

from inspect_scout import scan, scan_results_df, transcripts_from

from src.configuration.settings import JUDGE_MODEL
from src.models.model_registry import resolve
from tools.run_reader import Run, load_runs, tools_dir, write_summary
from tools.scout.scout_results import (
    attach_grep_floor,
    grep_findings,
    onset_of,
    plot_onset,
    signal_findings,
    validation_of,
    write_findings_csv,
    write_grep_csv,
)
from tools.scout.scout_scanners import GREP_NAME, SIGNALS, build_scanners, honeypot_names
from tools.scout.scout_transcripts import build_database, message_origins, origins_by_id

NAME = "scout"
HELP = "Inspect Scout scanners for cheating behaviours in each agent's transcript, with their rounds"
MAX_PROCESSES = 1
DISPLAY = "none"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The runs, the scanner model, the scanners to run, and the offline grep-only switch."""
    parser.add_argument("runs", nargs="+", help="run names under runs/ or run directories")
    parser.add_argument("--model", default=JUDGE_MODEL,
                        help=f"the LLM scanners' model: a registry entry or an Inspect model string ({JUDGE_MODEL})")
    parser.add_argument("--scanners", default=",".join(SIGNALS),
                        help=f"comma-separated LLM scanners, of {', '.join(SIGNALS)} (all by default)")
    parser.add_argument("--grep-only", action="store_true",
                        help="run only the grep scanner for the honeypot names: offline, no model calls")


def chosen_signals(text: str) -> list[str]:
    """The LLM scanners named on the command line; ValueError on an unknown name."""
    names = [name.strip() for name in text.split(",") if name.strip()]
    unknown = [name for name in names if name not in SIGNALS]
    if unknown:
        raise ValueError(f"unknown scanners {unknown}; choose from {', '.join(SIGNALS)}")
    return names


def scout_run(run: Run, signals: list[str], model: str | None) -> tuple[dict, int]:
    """Build the run's transcripts database, scan it, and write the outputs; returns the payload and the errors."""
    directory = tools_dir(run, NAME)
    transcripts = build_database(run, directory / "transcripts")
    scanners = build_scanners(run, signals, model or "")
    status = scan(scanners=scanners, transcripts=transcripts_from(str(directory / "transcripts")),
                  scans=str(directory / "scans"), model=model, max_processes=MAX_PROCESSES, display=DISPLAY)
    frames = scan_results_df(status.location).scanners
    labels = {transcript.agent: message_origins(transcript.messages) for transcript in transcripts}
    ids = {transcript.agent: origins_by_id(transcript.messages) for transcript in transcripts}
    agents: dict[str, dict] = {}
    for signal in signals:
        for agent, finding in signal_findings(frames[signal], labels, ids).items():
            agents.setdefault(agent, {})[signal] = finding
    grep = grep_findings(frames[GREP_NAME], ids) if GREP_NAME in frames else {}
    for agent, findings in agents.items():
        attach_grep_floor(findings, grep.get(agent) or {})
    errors = [str(error) for error in status.errors]
    errors += [f"{agent} {signal}: {finding['error']}" for agent, findings in agents.items()
               for signal, finding in findings.items() if finding["error"]]
    findings_csv = write_findings_csv(directory / "findings.csv", agents)
    grep_csv = write_grep_csv(directory / "grep.csv", grep)
    onset_png, onset_csv = plot_onset(run, agents, grep, directory)
    payload = {
        "model": model,
        "scanners": signals,
        "grep_only": not signals,
        "honeypot_names": honeypot_names(run),
        "transcripts": {transcript.agent: {"transcript_id": transcript.transcript_id,
                                           "messages": len(transcript.messages)} for transcript in transcripts},
        "scan": {"location": status.location, "complete": status.complete, "errors": errors},
        "agents": agents,
        "grep": grep,
        "onset": {agent: onset_of(agents.get(agent) or {}) if signals else None for agent in run.agents},
        "validation": validation_of(run, agents, grep),
        "files": [str(path.relative_to(directory)) for path in (findings_csv, grep_csv, onset_png, onset_csv)],
    }
    return payload, len(errors) + (0 if status.complete else 1)


def run(args: argparse.Namespace) -> int:
    """Scout every run named; 2 on a bad scanner name or model or a missing run, 1 when a scan had errors."""
    try:
        signals = [] if args.grep_only else chosen_signals(args.scanners)
        model = None if args.grep_only else resolve(args.model)
    except ValueError as error:
        print(f"{NAME}: {error}", file=sys.stderr)
        return 2
    runs = load_runs(args.runs, NAME)
    if runs is None:
        return 2
    failed = False
    for one in runs:
        payload, errors = scout_run(one, signals, model)
        path = write_summary(one, NAME, payload)
        found = {agent: sorted(signal for signal, finding in findings.items() if finding["found"])
                 for agent, findings in payload["agents"].items()}
        print(f"{one.name}: wrote {path}; found {found or 'grep only'}; onset {payload['onset']}; "
              f"agreement {payload['validation']['agreement']}")
        if errors:
            print(f"{one.name}: {errors} scan errors: {payload['scan']['errors']}", file=sys.stderr)
            failed = True
    return 1 if failed else 0
