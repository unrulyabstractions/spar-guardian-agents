"""scripts/migrate_run_layout.py local: a run folder of the older layout moves into processed/ and analysis/, its
figures from out/ and an apart control's eval/ to logs/, every byte kept; a target that exists stops the plan. It must
name the older layout's files, so tests/unit/test_run_paths.py exempts it."""
from __future__ import annotations

import pytest

from scripts import migrate_run_layout as migrate
from src.measures.run_directory import RunDir
from tests.support.run_files import write_text

OLDER = {
    "run.json": '{"run_id": "r1", "eval_log": "logs/study_x/r1/a.eval"}',
    "episodes.jsonl": "{}\n",
    "signals.jsonl": '{"row": "team"}\n',
    "trajectories/s#1@a01/transcript.json": "{}",
    "communication/s#1.json": "{}",
    "judges/cheatbench.luna.jsonl": "{}\n",
    "tools/board_reads.json": "{}",
    "tools/graphs/summary.json": "{}",
    "steps.jsonl": "{}\n",
    "replay.json": "{}",
    "swe/diffs/team.diff": "diff",
    "judge.jsonl": "{}\n",
    "notes.txt": "kept",
    ".DS_Store": "junk",
    "processed/versions.json": '{"parts": {}}',
}
LAYOUT = {
    "processed/run.json", "processed/episodes.jsonl", "analysis/signals.jsonl",
    "processed/trajectories/s#1@a01/transcript.json", "processed/communication/s#1.json",
    "analysis/judges/cheatbench.luna.jsonl", "processed/board_reads.json", "analysis/tools/graphs/summary.json",
    "processed/replay/steps.jsonl", "processed/replay/replay.json", "processed/swe/diffs/team.diff",
    "analysis/judge.jsonl", "processed/notes.txt", "processed/versions.json", "analysis/figures/timeline.png",
}


def older_run(root, rel: str, files: dict[str, str]):
    for name, text in files.items():
        write_text(root / "runs" / rel / name, text)
    return root / "runs" / rel


def tree(folder) -> dict[str, bytes]:
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in folder.rglob("*") if path.is_file()}


@pytest.fixture
def backups(tmp_path, monkeypatch):
    monkeypatch.setattr(migrate, "BACKUPS", tmp_path / "backups")
    return tmp_path / "backups"


def test_a_run_moves_into_the_layout_with_its_figures_and_every_byte(tmp_path, backups):
    run = older_run(tmp_path, "study_x/r1", OLDER)
    write_text(tmp_path / "out" / "study_x" / "r1" / "timeline.png", "png")
    apart = older_run(tmp_path, "apart_controls/ds/binder/cheat/s1", {"run.json": "{}", "eval/x.eval": "log"})
    before = {**{f"r1/{k}": v.encode() for k, v in OLDER.items() if k != ".DS_Store"}}
    assert migrate.main(["local", str(tmp_path / "runs"), "--dry-run"]) == 0
    assert (run / "run.json").is_file() and not (tmp_path / "backups").exists()
    assert migrate.main(["local", str(tmp_path / "runs")]) == 0
    assert set(tree(run)) == LAYOUT and not (tmp_path / "out").exists()
    moved = {f"r1/{k}": v for k, v in tree(run).items()}
    assert sorted(moved.values()) == sorted([*before.values(), b"png"])
    assert RunDir(run).is_run() and RunDir(run).run_json["run_id"] == "r1"
    assert set(tree(apart)) == {"processed/run.json"}
    assert (tmp_path / "logs" / "apart_controls" / "ds" / "binder" / "cheat" / "s1" / "x.eval").read_text() == "log"
    [record] = backups.glob("*/moves.tsv")
    assert len(record.read_text().splitlines()) == len(OLDER) - 1 + 1 + 2
    assert migrate.main(["local", str(tmp_path / "runs")]) == 0                  # a second pass moves nothing


def test_a_target_that_exists_stops_the_plan_before_any_move(tmp_path, backups):
    run = older_run(tmp_path, "study_x/r1", {"run.json": "{}", "episodes.jsonl": "{}\n",
                                             "processed/episodes.jsonl": "other"})
    before = tree(tmp_path)
    with pytest.raises(SystemExit):
        migrate.main(["local", str(tmp_path / "runs")])
    assert tree(tmp_path) == before and (run / "run.json").is_file()


def test_a_figure_with_no_run_stops_the_plan(tmp_path, backups):
    older_run(tmp_path, "study_x/r1", {"run.json": "{}"})
    write_text(tmp_path / "out" / "study_x" / "gone" / "timeline.png", "png")
    with pytest.raises(SystemExit):
        migrate.main(["local", str(tmp_path / "runs")])


def test_a_hub_path_maps_to_its_place_here_before_and_after_the_move():
    assert migrate.hub_to_local("study_x/runs/r1/run.json", "") == "runs/study_x/r1/run.json"
    assert migrate.hub_to_local("ian_x/logs/r1/a.eval", "OLD") == "OLD/logs/ian_x/r1/a.eval"
    assert migrate.hub_to_local("apart_controls/ds/b/c/s1/run.json", "OLD") == \
        "OLD/runs/apart_controls/ds/b/c/s1/run.json"
    assert migrate.local_to_hub("OLD/runs/apart_controls/ds/b/c/s1/processed/run.json", "apart_controls", "OLD") == \
        "apart_controls/runs/ds/b/c/s1/processed/run.json"
    assert migrate.local_to_hub("logs/study_x/r1/a.eval", "study_x", "") == "study_x/logs/r1/a.eval"
