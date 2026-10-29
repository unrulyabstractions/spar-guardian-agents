"""The scan config Hawk reads for one run, and the `hawk local scan` command line that runs it.

A local scan reads its transcripts from `$HAWK_RUNNER_EVALS_S3_URI/<eval_set_id>` for each source, and that URI may
be a local directory. The config sets it in `runner.environment`, so one directory becomes the one source: the run's
per-agent Scout transcripts database, or the directory of its eval log (one transcript for the whole swarm). The
scanners come from the shim distribution in scanner_package/, which Hawk installs and Inspect loads through its entry
point. Each run-bound scanner gets the run's directory as its `run` argument.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from tools.run_reader import Run
from tools.scout.scout_scanners import GREP_NAME, SIGNALS
from tools.scout.scout_transcripts import build_database

HAWK_DIR = Path(__file__).resolve().parent
SCANNER_PACKAGE_DIR = HAWK_DIR / "scanner_package"
SCANNER_PACKAGE_NAME = "spar_guardian_scanners"
SCANNER_MODULE = "tools.scout.scout_scanners"
RUN_BOUND_SCANNERS = (*SIGNALS, GREP_NAME)
SCOUT_TRANSCRIPTS = Path("tools") / "scout" / "transcripts"
HAWK_TRANSCRIPTS = Path("tools") / "hawk" / "transcripts"
SOURCES = ("scout", "eval_log")
HAWK_SPEC = "hawk[cli,runner]==3.7.2"
HAWK_PYTHON = "3.13"
PROVIDER_PACKAGES = {"openai": "openai", "anthropic": "anthropic", "google": "google-genai"}


@dataclass(frozen=True)
class TranscriptSource:
    """Where a local scan reads one run's transcripts: `root/eval_set_id`."""

    kind: str  # "scout_db" (the scout tool's database), "hawk_db" (built here) or "eval_log"
    root: Path  # the value of HAWK_RUNNER_EVALS_S3_URI
    eval_set_id: str  # the directory under root that holds the transcripts

    @property
    def path(self) -> Path:
        return self.root / self.eval_set_id


def transcript_source(run: Run, source: str) -> TranscriptSource | None:
    """The transcripts to scan: for "scout" the scout tool's database, else one built under tools/hawk/; for
    "eval_log" the eval log's directory. None when the run has neither transcripts nor the log asked for."""
    if source == "scout":
        scout_db = run.directory / SCOUT_TRANSCRIPTS
        if scout_db.is_dir():
            return TranscriptSource("scout_db", scout_db.parent, scout_db.name)
        if not run.transcripts:
            return None
        own_db = run.directory / HAWK_TRANSCRIPTS
        build_database(run, own_db)
        return TranscriptSource("hawk_db", own_db.parent, own_db.name)
    if source == "eval_log" and run.eval_log is not None:
        return TranscriptSource("eval_log", run.eval_log.parent.parent, run.eval_log.parent.name)
    return None


def model_package(model: str) -> dict:
    """The `models` entry Hawk expects for an Inspect model string such as openai/gpt-6-luna."""
    provider, _, name = model.partition("/")
    if not name:
        raise ValueError(f"not an Inspect model string with a provider: {model!r}")
    return {"package": PROVIDER_PACKAGES.get(provider, provider), "name": provider, "items": [{"name": name}]}


def scanner_item(scanner: str, run_directory: Path) -> dict:
    """One scanner item: a run-bound scanner gets the run's directory as its `run` argument."""
    if scanner in RUN_BOUND_SCANNERS:
        return {"name": scanner, "args": {"run": str(run_directory)}}
    return {"name": scanner}


def scan_config(
    name: str,
    source: TranscriptSource,
    scanners: list[str],
    run_directory: Path,
    model: str,
    scanner_module: str,
    package_dir: Path,
    repository_root: Path,
) -> dict:
    """The Hawk ScanConfig document for one run's transcripts."""
    return {
        "name": name,
        "scanners": [{
            "package": str(package_dir),
            "name": SCANNER_PACKAGE_NAME,
            "items": [scanner_item(scanner, run_directory) for scanner in scanners],
        }],
        "models": [model_package(model)],
        "transcripts": {"sources": [{"eval_set_id": source.eval_set_id}]},
        "runner": {"environment": {
            "HAWK_RUNNER_EVALS_S3_URI": str(source.root),
            "SPAR_GUARDIAN_ROOT": str(repository_root),
            "SPAR_GUARDIAN_SCANNERS": scanner_module,
        }},
    }


def write_scan_config(path: Path, config: dict) -> Path:
    """Write the config as YAML; returns the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def hawk_command(config_path: Path, hawk_spec: str, python: str, direct: bool) -> list[str]:
    """`hawk local scan` through uvx, so the runner extra is present without touching the installed hawk."""
    command = ["uvx", "--python", python, "--from", hawk_spec, "hawk", "local", "scan", str(config_path)]
    return command + ["--direct"] if direct else command
