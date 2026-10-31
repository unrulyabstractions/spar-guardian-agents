"""tools/hawk: the scan config writer, the hawk command line, the shim's registration and the command without --run.
The run-based tests read a copy of the generated riemann_minimal."""
from __future__ import annotations

import importlib.util
import json
import sys

import pytest
import tomllib
import yaml
from inspect_ai._util.registry import registry_lookup

from src.configuration.settings import ROOT
from tools.hawk.hawk_command import run_hawk, scan_status
from tools.hawk.hawk_scan_config import (
    GREP_NAME,
    RUN_BOUND_SCANNERS,
    SCANNER_PACKAGE_DIR,
    SCANNER_PACKAGE_NAME,
    TranscriptSource,
    hawk_command,
    model_package,
    scan_config,
    scanner_item,
    transcript_source,
    write_scan_config,
)
from tools.run_reader import load_run, read_summary
from tools.tool_dispatch import main


def test_model_package_maps_providers_to_pip_packages():
    assert model_package("openai/gpt-6-luna") == {"package": "openai", "name": "openai",
                                                   "items": [{"name": "gpt-6-luna"}]}
    assert model_package("google/gemini-3.8-flash")["package"] == "google-genai"
    assert model_package("anthropic/claude-haiku-5-5")["name"] == "anthropic"
    with pytest.raises(ValueError):
        model_package("gpt-6-luna")


def test_run_bound_scanners_get_the_run(tmp_path):
    assert scanner_item("honeypot_seen", tmp_path) == {"name": "honeypot_seen", "args": {"run": str(tmp_path)}}
    assert scanner_item(GREP_NAME, tmp_path)["args"] == {"run": str(tmp_path)}
    assert scanner_item("tool_call_count", tmp_path) == {"name": "tool_call_count"}
    assert set(RUN_BOUND_SCANNERS) >= {"honeypot_seen", "honeypot_opened", "honeypot_used", "honeypot_submitted",
                                       "cheating_proposed_on_board", "regulator_intervened", GREP_NAME}


def test_scan_config_points_hawk_at_a_local_directory(tmp_path):
    source = TranscriptSource("scout_db", tmp_path / "tools" / "scout", "transcripts")
    config = scan_config("demo-scan", source, ["honeypot_seen", "tool_call_count"], tmp_path, "openai/gpt-6-luna",
                         "tools.scout.scout_scanners", SCANNER_PACKAGE_DIR, ROOT)
    assert config["scanners"][0]["package"] == str(SCANNER_PACKAGE_DIR)
    assert config["scanners"][0]["name"] == SCANNER_PACKAGE_NAME
    assert [item["name"] for item in config["scanners"][0]["items"]] == ["honeypot_seen", "tool_call_count"]
    assert config["transcripts"] == {"sources": [{"eval_set_id": "transcripts"}]}
    environment = config["runner"]["environment"]
    assert environment["HAWK_RUNNER_EVALS_S3_URI"] == str(tmp_path / "tools" / "scout")
    assert environment["SPAR_GUARDIAN_ROOT"] == str(ROOT)
    assert environment["SPAR_GUARDIAN_SCANNERS"] == "tools.scout.scout_scanners"
    path = write_scan_config(tmp_path / "out" / "scan.yaml", config)
    assert yaml.safe_load(path.read_text()) == config


def test_hawk_command_runs_hawk_from_uvx(tmp_path):
    command = hawk_command(tmp_path / "scan.yaml", "hawk[cli,runner]==3.7.2", "3.13", direct=False)
    assert command == ["uvx", "--python", "3.13", "--from", "hawk[cli,runner]==3.7.2", "hawk", "local", "scan",
                       str(tmp_path / "scan.yaml")]
    assert hawk_command(tmp_path / "scan.yaml", "hawk", "3.13", direct=True)[-1] == "--direct"


def test_shim_entry_point_matches_the_package_name():
    pyproject = tomllib.loads((SCANNER_PACKAGE_DIR / "pyproject.toml").read_text())
    assert pyproject["project"]["entry-points"]["inspect_ai"] == {SCANNER_PACKAGE_NAME: SCANNER_PACKAGE_NAME}


def test_shim_registers_run_bound_scanners(monkeypatch, riemann_copy):
    monkeypatch.setenv("SPAR_GUARDIAN_ROOT", str(ROOT))
    monkeypatch.setenv("SPAR_GUARDIAN_SCANNERS", "tools.scout.scout_scanners")
    shim = SCANNER_PACKAGE_DIR / f"{SCANNER_PACKAGE_NAME}.py"
    spec = importlib.util.spec_from_file_location("hawk_shim_under_test", shim)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in RUN_BOUND_SCANNERS:
        assert f"{SCANNER_PACKAGE_NAME}/{name}" in module.REGISTERED
    factory = registry_lookup("scanner", f"{SCANNER_PACKAGE_NAME}/{GREP_NAME}")
    assert callable(factory)
    assert callable(factory(run=str(riemann_copy)))


def test_transcript_sources_of_a_run(riemann_copy):
    run = load_run(riemann_copy)
    from_log = transcript_source(run, "eval_log")
    assert from_log.kind == "eval_log"
    assert from_log.path == run.eval_log.parent
    built = transcript_source(run, "scout")
    assert built.kind == "hawk_db"
    assert built.path == riemann_copy / "tools" / "hawk" / "transcripts"
    assert built.path.is_dir()


def test_command_writes_config_and_summary_without_running(riemann_copy, capsys):
    assert main(["hawk", str(riemann_copy), "--source", "eval_log"]) == 0
    assert "hawk local scan" in capsys.readouterr().out
    run = load_run(riemann_copy)
    summary = read_summary(run, "hawk")
    assert summary["ran"] is False and summary["returncode"] is None and summary["needs_server"] is False
    assert summary["source"]["kind"] == "eval_log"
    config = yaml.safe_load((riemann_copy / "tools" / "hawk" / "scan.yaml").read_text())
    assert [item["name"] for item in config["scanners"][0]["items"]] == list(RUN_BOUND_SCANNERS)
    assert config["scanners"][0]["items"][0]["args"] == {"run": str(riemann_copy)}
    assert config["models"][0]["items"] == [{"name": "gpt-6-luna"}]


def test_command_refuses_an_unknown_source():
    assert main(["hawk", "riemann_minimal", "--source", "nowhere"]) == 2


def test_command_refuses_an_unknown_run_or_model(tmp_path):
    assert main(["hawk", str(tmp_path / "no_such_run")]) == 2
    assert main(["hawk", "riemann_minimal", "--model", "no-such-entry"]) == 2


def test_scan_status_reads_completion_and_errors(tmp_path):
    assert scan_status(tmp_path) == {"complete": False, "errors": 0}
    (tmp_path / "_summary.json").write_text(json.dumps({
        "complete": True, "scanners": {"honeypot_seen": {"errors": 2}, "honeypot_grep": {"errors": 0}},
    }))
    assert scan_status(tmp_path) == {"complete": True, "errors": 2}


def test_run_hawk_kills_a_scan_that_times_out(tmp_path):
    returncode, tail = run_hawk([sys.executable, "-c", "print('started', flush=True); import time; time.sleep(30)"],
                                tmp_path, timeout=2)
    assert returncode == 124
    assert tail[0] == "started" and tail[-1] == "timed out after 2 s"
