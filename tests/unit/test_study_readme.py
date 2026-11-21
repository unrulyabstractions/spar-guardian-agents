"""Each study's README.md (scripts/study_readme.py) is current against its own files and the code, every study folder
has an entry, and the generator refuses a field or a file it has no meaning for."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import study_readme
from src.configuration.settings import ROOT

RUNS = ROOT / "runs"


@pytest.mark.parametrize("study", study_readme.STUDIES)
def test_readme_is_current(study):
    folder = RUNS / study
    if not (folder / study_readme.README).is_file():
        pytest.skip(f"no runs/{study}/README.md in this checkout")
    assert (folder / study_readme.README).read_text() == study_readme.render(folder), (
        f"runs/{study}/README.md is stale: uv run python -m scripts.study_readme runs/{study}")


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
        write_jsonl(run / "episodes.jsonl", [episode])
        (run / "run.json").write_text(json.dumps({"task": "chess", "model": "mockllm/model", "agents": ["k7q2m"],
                                                  "eval_log": "logs/none.eval"}))
        write_jsonl(run / "signals.jsonl", [{"row": "agent", "episode": "s#1", "cheating_rung": "none"},
                                            {"row": "team", "cheating_rung": "none"}])
        transcript = run / "trajectories" / "s#1" / "transcript.json"
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
    assert "| episodes.jsonl `transcript_path` → the transcript, from the run's folder | 2 of 2 |" in text
    assert "| run.json `eval_log` → the eval log, from the checkout | 0 of 2 |" in text
    assert "**Cheating**" in text and "| `hacked` |" in text


def test_render_refuses_a_field_without_a_meaning(tiny_study):
    write_jsonl(tiny_study / "run_a1" / "episodes.jsonl",
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
