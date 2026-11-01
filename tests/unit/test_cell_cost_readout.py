"""The per-cell cost readout: billed model cost from each run's episodes, each box's hours at its $/h split over the
cell runs it held, a partial cell whenever a run's cost was not all measured, and a refusal of a study the table does
not state. Each case builds run folders, a registry, manifests and plans the way the export and the fleet write them."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.box import cell_cost_readout as readout_module
from scripts.box.study_table import STAGES, box_jobs, stage_boxes
from src.measures import run_validity

STUDY = readout_module.TABLE_STUDY
STAGE = "phase2-protein-luna"
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
REMOTE = f"/root/work/runs/{STUDY}"
REMOTE_LOGS = f"/root/work/logs/{STUDY}"


@pytest.fixture(autouse=True)
def every_eval_succeeds(monkeypatch):
    monkeypatch.setattr(run_validity, "eval_status", lambda run: "success")


def names(setting: str, level: str | None, regulator: str | None = None) -> list[str]:
    """The run names of one cell of STAGE, by seed."""
    wanted = (STAGE, setting, level, regulator)
    return next(cell["names"] for cell in readout_module.table_cells()
                if (cell["stage"], cell["setting"], cell["level"], cell["regulator"]) == wanted)


def write_run(root: Path, name: str, agents: list[tuple], evals: tuple[str, ...] = ("a.eval",),
              counted: str = "a.eval") -> None:
    """A run folder as the export writes it, one episode per agent (billed dollars or None, billed calls, calls, and
    errored calls when any), and its eval logs."""
    run = root / "runs" / STUDY / name
    run.mkdir(parents=True)
    agent_ids = [f"a{k}" for k in range(len(agents))]
    (run / "run.json").write_text(json.dumps({"task": "protein_design", "eval_log": f"/box/logs/{counted}",
                                              "agents": agent_ids, "rules": {"workers": agent_ids},
                                              "config": {"regulators": 0}}))
    episodes = []
    for agent, (usd, billed_calls, calls, *errored) in zip(agent_ids, agents):
        usage = {"input": 10, "output": 5, **({} if usd is None else {"billed_usd": usd, "billed_calls": billed_calls})}
        ledger = {"calls": calls, "errored": errored[0] if errored else 0}
        episodes.append({"orbit_agent": agent, "usage": usage, "ledger": ledger, "failure": None})
    (run / "episodes.jsonl").write_text("".join(json.dumps(e) + "\n" for e in episodes))
    (run / "signals.jsonl").write_text(json.dumps({"row": "team", "rung": "none"}) + "\n")
    write_evals(root, name, evals)


def write_evals(root: Path, name: str, evals: tuple[str, ...]) -> None:
    logs = root / "logs" / STUDY / name
    logs.mkdir(parents=True, exist_ok=True)
    for file in evals:
        (logs / file).write_text("")


def write_registry(root: Path, boxes: list[dict]) -> None:
    (root / "runs").mkdir(parents=True, exist_ok=True)
    (root / "runs" / "boxes.json").write_text(json.dumps({"boxes": boxes}))


def entry(instance: int, box: str | None, created: str, destroyed: str | None, dph: float,
          stage: str | None = STAGE) -> dict:
    return {"id": instance, "box": box, "stage": stage, "created": created, "destroyed": destroyed, "dph": dph}


def write_manifest(root: Path, instance: int, paths: list[str]) -> None:
    folder = root / "logs" / "boxes" / str(instance)
    folder.mkdir(parents=True)
    (folder / "manifest.jsonl").write_text("".join(json.dumps({"box": p, "size": 1, "kind": "file"}) + "\n"
                                                   for p in paths))


def write_plan(root: Path, file: str, box: str, started: str, runs: list[str]) -> None:
    path = root / "logs" / STUDY / "screen" / file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"box": box, "started": started, "runs": {name: {} for name in runs}}))


def row(result: dict, setting: str, level: str | None) -> dict:
    return next(r for r in result["cells"] if (r["setting"], r["level"]) == (setting, level))


def test_a_box_splits_its_hours_at_its_rate_over_the_cell_runs_it_held(tmp_path):
    first, second, third = names("minimal", "generic")[:3]
    write_run(tmp_path, first, [(0.10, 4, 4), (0.20, 6, 6)])
    write_run(tmp_path, second, [(0.05, 2, 2)])
    write_run(tmp_path, third, [(0.30, 3, 3)])
    mock = f"{first[:-1]}mock1"
    # the box made one run's folder, one run's eval log only, and one run's set-aside screen log, and a rehearsal
    write_manifest(tmp_path, 101, [f"{REMOTE}/{first}/run.json", f"{REMOTE_LOGS}/{second}/x.eval",
                                   f"{REMOTE_LOGS}/screen/attempts/{third}.1.log", f"{REMOTE}/{mock}/run.json",
                                   f"{REMOTE_LOGS}/screen/box_{STAGE}-1.json"])
    write_manifest(tmp_path, 102, [f"{REMOTE}/{mock}/run.json"])
    write_registry(tmp_path, [entry(101, f"{STAGE}-1", "2026-11-01T00:00:00Z", "2026-11-01T02:00:00Z", 1.5),
                              entry(102, f"{STAGE}-2", "2026-11-01T00:00:00Z", "2026-11-01T00:30:00Z", 2.0),
                              entry(103, None, "2026-11-01T00:00:00Z", "2026-11-01T05:00:00Z", 9.0, stage=None)])
    result = readout_module.readout(tmp_path, STUDY, STAGE, now=NOW)
    cell = row(result, "minimal", "generic")
    assert (cell["n_runs"], cell["planned"], cell["whole"], cell["partial"]) == (3, 5, 3, False)
    assert [r["boxes_usd"] for r in cell["runs"]] == pytest.approx([1.0, 1.0, 1.0])
    assert cell["models_usd"] == pytest.approx(0.65) and cell["total_usd"] == pytest.approx(3.65)
    assert cell["per_run_usd"] == pytest.approx(3.65 / 3)
    totals = result["totals"]
    assert result["unheld_boxes"] == [102] and totals["unheld_boxes_usd"] == pytest.approx(1.0)
    assert totals["total_usd"] == pytest.approx(4.65) and not totals["partial"]
    assert [box["id"] for box in result["boxes"]] == [101, 102]
    assert "only mock rehearsals" in readout_module.text(result)


def test_a_run_without_its_whole_billed_cost_makes_its_cell_partial_never_zero(tmp_path):
    unbilled, unexported, retried = names("minimal", "identified")[:3]
    write_run(tmp_path, unbilled, [(0.40, 5, 5), (None, 0, 3)])
    write_evals(tmp_path, unexported, ("a.eval",))
    write_run(tmp_path, retried, [(0.25, 2, 2)], evals=("a.eval", "b.eval"), counted="b.eval")
    write_registry(tmp_path, [])
    result = readout_module.readout(tmp_path, STUDY, STAGE, now=NOW)
    cell = row(result, "minimal", "identified")
    assert cell["partial"] and cell["n_runs"] == 3 and cell["models_usd"] == pytest.approx(0.65)
    assert [r["short"] for r in cell["runs"]] == [["3 call(s) with no billed cost"], ["no export"],
                                                  ["1 earlier eval log(s) the export did not count"]]
    assert result["totals"]["partial"] and result["totals"]["short_runs"] == 3
    line = next(line for line in readout_module.text(result).splitlines() if line.startswith(STAGE))
    assert line.endswith("partial: 3 run(s) short")


def test_a_cell_short_only_by_errored_calls_stays_partial_and_says_why(tmp_path):
    write_run(tmp_path, names("minimal", "generic")[0], [(0.30, 9, 10, 1), (0.20, 4, 4)])
    write_registry(tmp_path, [])
    result = readout_module.readout(tmp_path, STUDY, STAGE, now=NOW)
    cell = row(result, "minimal", "generic")
    assert cell["partial"] and cell["models_usd"] == pytest.approx(0.50)
    assert cell["runs"][0]["short"] == [f"1 {readout_module.ERRORED}"]
    assert "partial: 1 run(s) short by errored calls alone" in readout_module.text(result)


def test_a_live_box_bills_to_now_and_holds_the_runs_its_own_plans_started(tmp_path):
    first, second = names("minimal_regulated", "ambiguous", "luna")[:2]
    box = f"{STAGE}-3"
    write_registry(tmp_path, [entry(200, box, "2026-11-01T08:00:00Z", "2026-11-01T09:00:00Z", 1.0),
                              entry(201, box, "2026-11-01T10:00:00Z", None, 0.6)])
    write_plan(tmp_path, f"attempts/box_{box}.1.json", box, "2026-11-01T08:30:00Z", [first])
    write_plan(tmp_path, f"attempts/box_{box}.2.json", box, "2026-11-01T10:10:00Z", [f"{first[:-1]}mock1"])
    write_plan(tmp_path, f"box_{box}.json", box, "2026-11-01T10:30:00Z", [first, second])
    result = readout_module.readout(tmp_path, STUDY, STAGE, now=NOW)
    boxes = {b["id"]: b for b in result["boxes"]}
    assert boxes[201]["live"] and boxes[201]["hours"] == pytest.approx(2.0) and boxes[201]["usd"] == pytest.approx(1.2)
    assert boxes[200]["held"] == [first] and boxes[201]["held"] == [first, second]
    assert boxes[201]["rehearsals"] == [f"{first[:-1]}mock1"]
    cell = row(result, "minimal_regulated", "ambiguous")
    assert [r["boxes_usd"] for r in cell["runs"]] == pytest.approx([1.6, 0.6])
    assert cell["live"] == 2 and cell["partial"] and result["totals"]["live_boxes"] == [201]


def test_another_study_is_refused_since_study_exploration_0_reuses_the_tables_run_names(tmp_path):
    with pytest.raises(ValueError, match="reuses its run names"):
        readout_module.readout(tmp_path, "study_exploration_0", now=NOW)


def test_the_cells_name_every_run_the_boxes_of_each_stage_run():
    table = readout_module.table_cells()
    for stage in STAGES:
        jobs = sorted(job.name for box in stage_boxes(stage) for job in box_jobs(box))
        assert sorted(name for cell in table if cell["stage"] == stage for name in cell["names"]) == jobs, stage


def test_the_command_prints_the_readout_as_json(tmp_path, capsys):
    write_run(tmp_path, names("minimal", "generic")[0], [(0.10, 1, 1)])
    write_registry(tmp_path, [])
    assert readout_module.main(["--root", str(tmp_path), "--stage", STAGE, "--json", "--study", STUDY]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["totals"]["models_usd"] == pytest.approx(0.10) and printed["totals"]["runs"] == 1
