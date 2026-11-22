"""Each study's README.md (scripts/study_readme.py) is current against its own files and the code, every study folder
has an entry, and the generator refuses a field or a file it has no meaning for."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts import study_readme
from src.configuration.settings import ROOT
from tests.support.run_files import write_text

RUNS = ROOT / "runs"


@pytest.mark.parametrize("study", study_readme.STUDIES)
def test_readme_is_current(study):
    """The code describes every field and file the study holds now. When no data changed since the README was
    written, the README is the render: a difference then comes from the code. Data that changed since (a run, a
    replay's files, a smoke run) is the data's own news, which `make study-readmes` writes; `make study-readmes CHECK=1`
    reports it."""
    folder = RUNS / study
    readme = folder / study_readme.README
    if not readme.is_file():
        pytest.skip(f"no runs/{study}/README.md in this checkout")
    written = readme.stat().st_mtime
    text = study_readme.render(folder)
    if study_readme.data_mtime(folder) <= written:
        assert readme.read_text() == text, (
            f"runs/{study}/README.md is stale against the code: uv run python -m scripts.study_readme runs/{study}")


def test_a_readme_is_checked_only_against_data_older_than_it(tmp_path, monkeypatch):
    """data_mtime moves with a new file, a changed one and a removed one under runs/<study>/ and logs/<study>/, and
    not with the README itself."""
    monkeypatch.setattr(study_readme, "ROOT", tmp_path)
    study = tmp_path / "runs" / "study_x"
    write_text(study / "run_a" / "processed/run.json", "{}")
    (tmp_path / "logs" / "study_x").mkdir(parents=True)
    os.utime(study / "run_a" / "processed/run.json", (100, 100))
    for path in (study / "run_a" / "processed", study / "run_a", study, tmp_path / "logs" / "study_x"):
        os.utime(path, (100, 100))
    assert study_readme.data_mtime(study) == 100
    (study / study_readme.README).write_text("x")
    os.utime(study, (100, 100))
    assert study_readme.data_mtime(study) == 100                     # the README is no data
    (tmp_path / "logs" / "study_x" / "a.eval").write_text("")
    assert study_readme.data_mtime(study) > 100                       # a new log
    os.utime(tmp_path / "logs" / "study_x" / "a.eval", (100, 100))
    os.utime(tmp_path / "logs" / "study_x", (100, 100))
    os.utime(study / "run_a" / "processed/run.json", (200, 200))
    assert study_readme.data_mtime(study) == 200                      # a changed file
    (study / "run_a" / "processed/run.json").unlink()
    assert study_readme.data_mtime(study) > 200                       # a removed one moves its folder


def test_every_study_folder_has_an_entry():
    if not RUNS.is_dir():
        pytest.skip("no runs/ in this checkout")
    folders = {p.name for p in RUNS.iterdir() if p.is_dir()}
    assert folders <= set(study_readme.STUDIES)
    assert set(study_readme.STUDIES) <= set(study_readme.meanings()["studies"])


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


@pytest.fixture
def tiny_study(tmp_path, monkeypatch):
    """A study of two lone-agent runs, with its entry added to the meanings."""
    study = tmp_path / "study_tiny"
    for name, extra in (("run_a1", {"role": "worker"}), ("run_a2", {})):
        run = study / name
        episode = {"id": "s", "episode": "s#1", "replicate": 1, "agent": "k7q2m",
                   "transcript_path": "trajectories/s#1/transcript.json", **extra}
        write_jsonl(run / "processed/episodes.jsonl", [episode])
        write_text(run / "processed/run.json", json.dumps({"task": "chess", "model": "mockllm/model",
                                                           "agents": ["k7q2m"],
                                                  "eval_log": "logs/none.eval"}))
        write_jsonl(run / "analysis/signals.jsonl", [{"row": "agent", "episode": "s#1", "cheating_rung": "none"},
                                            {"row": "team", "cheating_rung": "none"}])
        transcript = run / "processed/trajectories" / "s#1" / "transcript.json"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(json.dumps({"agent": "k7q2m", "model": "mockllm/model"}))
    data = study_readme.meanings()
    data["studies"]["study_tiny"] = {"title": "study_tiny", "about": "A tiny study.", "load": "import json"}
    monkeypatch.setattr(study_readme, "meanings", lambda: data)
    return study


def test_render_reads_types_values_and_links(tiny_study):
    text = study_readme.render(tiny_study)
    assert "| `role`? | string | the agent's role, null in a run without roles. Values: `worker`. |" in text
    assert "| `cheating_rung` | string | the workers' Cheating rung. Values: `none`. |" in text
    assert "| episodes.jsonl `transcript_path` → the transcript, from the run's processed/ | 2 of 2 |" in text
    assert "| run.json `eval_log` → the eval log, from the checkout | 0 of 2 |" in text
    assert "**Cheating**" in text and "| `hacked` |" in text


def test_render_refuses_a_field_without_a_meaning(tiny_study):
    write_jsonl(tiny_study / "run_a1" / "processed/episodes.jsonl",
                [{"id": "s", "episode": "s#1", "replicate": 1, "agent": "k7q2m", "brand_new": 1}])
    with pytest.raises(study_readme.Undescribed, match="episodes.brand_new"):
        study_readme.render(tiny_study)


def test_render_refuses_a_file_no_spec_covers(tiny_study):
    (tiny_study / "run_a1" / "notes.bin").write_bytes(b"x")
    with pytest.raises(study_readme.Undescribed, match="notes.bin"):
        study_readme.render(tiny_study)


def test_schema_descriptions_come_from_cheatbench():
    swe = study_readme.schema_descriptions("software_engineering")
    assert swe["reference_solution_seeking"].startswith("Issued action seeking this assignment's existing solution")
    assert study_readme.schema_descriptions("no_such_task") == {}
