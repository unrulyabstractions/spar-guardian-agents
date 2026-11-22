"""scripts/replay_study.py: only a faithful replay's steps.jsonl and replay.json reach the run's folder; an unfaithful
one, a run that already holds a replay, and a replay with no run folder change nothing."""
import importlib.util
import json
from pathlib import Path

from src.replay.replay_report import REPORT, SCHEMA, STEPS
from tests.support.run_files import write_text

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "replay_study.py"
spec = importlib.util.spec_from_file_location("replay_study", SCRIPT)
rs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rs)


def files(folder: Path) -> list[str]:
    return sorted(path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file())


def replayed(out: Path, name: str, faithful: bool) -> None:
    d = out / name
    (d / "end").mkdir(parents=True)
    write_text(d / REPORT, json.dumps({"run": name, "schema": SCHEMA, "faithful": faithful}))
    write_text(d / STEPS, '{"n": 1}\n')
    (d / "compose.yaml").write_text("services: {}\n")


def test_only_faithful_steps_reach_the_run_folder(tmp_path):
    out, runs = tmp_path / "out", tmp_path / "runs"
    for name in ("good", "bad", "held"):
        (runs / name).mkdir(parents=True)
        write_text(runs / name / "processed/run.json", "{}")
    write_text(runs / "held" / "processed/replay/replay.json", '{"faithful": true, "old": 1}')
    replayed(out, "good", True)
    replayed(out, "bad", False)
    replayed(out, "held", True)
    replayed(out, "gone", True)
    done = rs.place(out, runs)
    assert done == {"placed": ["good"], "unfaithful": ["bad"], "skipped": ["gone", "held"]}
    assert files(runs / "good") == ["processed/replay/replay.json", "processed/replay/steps.jsonl",
                                    "processed/run.json"]
    assert files(runs / "bad") == ["processed/run.json"]
    assert json.loads((runs / "held" / "processed/replay/replay.json").read_text()) == {"faithful": True, "old": 1}


def test_runs_with_a_replay_are_not_played_again(tmp_path):
    runs = tmp_path / "runs"
    for name in ("a", "b"):
        (runs / name).mkdir(parents=True)
    write_text(runs / "a" / "processed/replay/replay.json", "{}")
    assert rs.to_replay(runs, ["a", "b"]) == [runs / "b"]
