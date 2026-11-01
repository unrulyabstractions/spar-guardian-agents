"""The per-cell cost readout: billed model cost from each run's episodes and its earlier attempts', each box's hours at
its $/h split over the cell runs it held, a partial cell whenever a run's cost was not all measured, one named reason
for each shortfall, boxes tied to the study by what they name, and a refusal of a study the table does not state or of
a checkout without the data. Each case builds run folders, a registry, manifests and plans the way the export and the
fleet write them."""
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
HOURS = ("2026-11-01T00:00:00Z", "2026-11-01T01:00:00Z")


@pytest.fixture(autouse=True)
def every_eval_succeeds(monkeypatch):
    monkeypatch.setattr(run_validity, "eval_status", lambda run: "success")


def names(setting: str, level: str | None, regulator: str | None = None, stage: str = STAGE) -> list[str]:
    """The run names of one cell of a stage, by seed."""
    wanted = (stage, setting, level, regulator)
    return next(cell["names"] for cell in readout_module.table_cells()
                if (cell["stage"], cell["setting"], cell["level"], cell["regulator"]) == wanted)


def write_run(root: Path, name: str, agents: list[tuple], evals: tuple[str, ...] = ("a.eval",),
              counted: str = "a.eval", ledger: dict | None = None, folder: str | None = None) -> Path:
    """A run folder as the export writes it, one episode per agent (billed dollars or None, billed calls, calls, and
    errored calls when any), run.json's ledger, and its eval logs. folder is a group's or an attempt's place under the
    study, such as invalid/<name> or attempts/<name>.1."""
    run = root / "runs" / STUDY / (folder or name)
    run.mkdir(parents=True)
    agent_ids = [f"a{k}" for k in range(len(agents))]
    (run / "run.json").write_text(json.dumps({"task": "protein_design", "eval_log": f"/box/logs/{counted}",
                                              "agents": agent_ids, "rules": {"workers": agent_ids},
                                              "config": {"regulators": 0}, "ledger": ledger or {"complete": True}}))
    episodes = []
    for agent, (usd, billed_calls, calls, *errored) in zip(agent_ids, agents):
        usage = {"input": 10, "output": 5, **({} if usd is None else {"billed_usd": usd, "billed_calls": billed_calls})}
        ledger_row = {"calls": calls, "errored": errored[0] if errored else 0}
        episodes.append({"orbit_agent": agent, "usage": usage, "ledger": ledger_row, "failure": None})
    (run / "episodes.jsonl").write_text("".join(json.dumps(e) + "\n" for e in episodes))
    (run / "signals.jsonl").write_text(json.dumps({"row": "team", "rung": "none"}) + "\n")
    write_evals(root, folder or name, evals)
    return run


def write_evals(root: Path, name: str, evals: tuple[str, ...]) -> None:
    logs = root / "logs" / STUDY / name
    logs.mkdir(parents=True, exist_ok=True)
    for file in evals:
        (logs / file).write_text("")


def write_registry(root: Path, boxes: list[dict]) -> None:
    """runs/boxes.json, and the study's runs/ and logs/ folders, as a checkout that ran the study holds them."""
    for top in ("runs", "logs"):
        (root / top / STUDY).mkdir(parents=True, exist_ok=True)
    (root / "runs" / "boxes.json").write_text(json.dumps({"boxes": boxes}))


def entry(instance: int, box: str | None, created: str, destroyed: str | None, dph: float | None,
          stage: str | None = STAGE, launch: str | None = None) -> dict:
    return {"id": instance, "box": box, "stage": stage, "created": created, "destroyed": destroyed, "dph": dph,
            "launch": launch}


def write_manifest(root: Path, instance: int, paths: list[str]) -> None:
    folder = root / "logs" / "boxes" / str(instance)
    folder.mkdir(parents=True)
    (folder / "manifest.jsonl").write_text("".join(json.dumps({"box": p, "size": 1, "kind": "file"}) + "\n"
                                                   for p in paths))


def held_by(root: Path, instance: int, runs: list[str], *, dph: float | None = 1.0, stage: str = STAGE) -> dict:
    """A destroyed box of an hour that captured these runs' run.json; its registry entry."""
    write_manifest(root, instance, [f"{REMOTE}/{name}/run.json" for name in runs])
    return entry(instance, f"{stage}-9", *HOURS, dph, stage=stage)


def write_plan(root: Path, file: str, box: str, started: str, runs: list[str]) -> None:
    path = root / "logs" / STUDY / "screen" / file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"box": box, "started": started, "runs": {name: {} for name in runs}}))


def row(result: dict, setting: str, level: str | None) -> dict:
    return next(r for r in result["cells"] if (r["setting"], r["level"]) == (setting, level))


def shorts(result: dict, setting: str, level: str | None) -> dict[str, list[str]]:
    return {run["run"]: run["short"] for run in row(result, setting, level)["runs"]}


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
    write_registry(tmp_path, [held_by(tmp_path, 301, [unbilled, unexported, retried])])
    result = readout_module.readout(tmp_path, STUDY, STAGE, now=NOW)
    cell = row(result, "minimal", "identified")
    assert cell["partial"] and cell["n_runs"] == 3 and cell["models_usd"] == pytest.approx(0.65)
    assert [r["short"] for r in cell["runs"]] == [["3 call(s) with no billed cost"], ["no export"],
                                                  ["1 earlier eval log(s) the export did not count"]]
    assert result["totals"]["partial"] and result["totals"]["short_runs"] == 3
    line = next(line for line in readout_module.text(result).splitlines() if line.startswith(STAGE))
    assert line.endswith("partial: 3 run(s) short")


def test_a_mixed_cell_is_partial_on_the_cell_and_on_the_totals(tmp_path):
    """One run fully billed and one short: the cell and the totals both read partial, and the full run names no
    reason."""
    whole, short = names("minimal", "generic")[:2]
    write_run(tmp_path, whole, [(0.10, 2, 2)])
    write_run(tmp_path, short, [(0.10, 2, 4)])
    write_registry(tmp_path, [held_by(tmp_path, 302, [whole, short])])
    result = readout_module.readout(tmp_path, STUDY, STAGE, now=NOW)
    cell = row(result, "minimal", "generic")
    assert shorts(result, "minimal", "generic") == {whole: [], short: [f"2 {readout_module.UNBILLED}"]}
    assert cell["partial"] and result["totals"]["partial"] and result["totals"]["short_runs"] == 1
    assert json.loads(json.dumps(result))["cells"][0]["partial"] is True


def test_a_cell_short_only_by_errored_calls_stays_partial_and_says_why(tmp_path):
    name = names("minimal", "generic")[0]
    write_run(tmp_path, name, [(0.30, 9, 10, 1), (0.20, 4, 4)])
    write_registry(tmp_path, [held_by(tmp_path, 303, [name])])
    result = readout_module.readout(tmp_path, STUDY, STAGE, now=NOW)
    cell = row(result, "minimal", "generic")
    assert cell["partial"] and cell["models_usd"] == pytest.approx(0.50)
    assert cell["runs"][0]["short"] == [f"1 {readout_module.ERRORED}"]
    assert "partial: 1 run(s) short by errored calls alone" in readout_module.text(result)


def test_errored_calls_count_only_up_to_the_calls_with_no_billed_cost(tmp_path):
    """An agent billed for 9 of 10 calls with 3 errored: one call has no billed cost, so one errored call is short."""
    name = names("minimal", "generic")[0]
    write_run(tmp_path, name, [(0.30, 9, 10, 3)])
    write_registry(tmp_path, [held_by(tmp_path, 304, [name])])
    result = readout_module.readout(tmp_path, STUDY, STAGE, now=NOW)
    assert shorts(result, "minimal", "generic") == {name: [f"1 {readout_module.ERRORED}"]}


LIMIT = "the sample ended on its token limit: a call in flight is not in the log"


def test_each_shortfall_names_its_own_reason(tmp_path):
    """No episodes, an agent without a ledger, a call in flight at a limit (the export's flag, never read as errored
    calls alone), a ledger the export calls incomplete, a run no box held, a box with no $/h and an unreadable run."""
    runs = names("minimal", "generic") + names("minimal", "identified")[:2]
    empty, no_ledger, at_limit, incomplete, unheld, no_rate, unreadable = runs
    write_run(tmp_path, empty, [])
    write_run(tmp_path, no_ledger, [(0.10, 1, 1)])
    episode = json.loads((tmp_path / "runs" / STUDY / no_ledger / "episodes.jsonl").read_text())
    (tmp_path / "runs" / STUDY / no_ledger / "episodes.jsonl").write_text(json.dumps({**episode, "ledger": None}))
    write_run(tmp_path, at_limit, [(0.30, 9, 10, 1)], ledger={"complete": False, "flags": ["a0: 1 errored call(s)",
                                                                                         LIMIT]})
    write_run(tmp_path, incomplete, [(0.10, 1, 1)], ledger={"complete": False, "flags": ["a0: something new"]})
    write_run(tmp_path, unheld, [(0.10, 1, 1)])
    write_run(tmp_path, no_rate, [(0.10, 1, 1)])
    write_run(tmp_path, unreadable, [(0.10, 1, 1)])
    (tmp_path / "runs" / STUDY / unreadable / "run.json").write_text("{not json")
    write_registry(tmp_path, [held_by(tmp_path, 305, [empty, no_ledger, at_limit, incomplete, unreadable]),
                              held_by(tmp_path, 306, [no_rate], dph=None)])
    result = readout_module.readout(tmp_path, STUDY, STAGE, now=NOW)
    found = {**shorts(result, "minimal", "generic"), **shorts(result, "minimal", "identified")}
    assert found[empty] == [readout_module.NO_EPISODES]
    assert found[no_ledger] == [f"agent a0 {readout_module.NO_LEDGER}"]
    assert found[at_limit] == [f"1 {readout_module.ERRORED}", f"the sample ended on its token limit: "
                                                             f"{readout_module.AT_LIMIT}"]
    assert found[incomplete] == [f"{readout_module.LEDGER_INCOMPLETE}: a0: something new"]
    assert found[unheld] == [readout_module.NO_BOX]
    assert found[no_rate] == [readout_module.NO_RATE]
    assert len(found[unreadable]) == 1 and found[unreadable][0].startswith(f"{readout_module.UNREADABLE}: ")
    assert "by errored calls alone" not in readout_module.text(result)
    for reason in ("NO_EXPORT", "NO_EPISODES", "NO_LEDGER", "UNBILLED", "ERRORED", "AT_LIMIT", "LEDGER_INCOMPLETE",
                   "EARLIER_LOG", "NO_BOX", "NO_RATE", "UNREADABLE"):
        assert f"- {reason}:" in readout_module.__doc__, reason


def test_an_unpriced_model_makes_no_run_short(tmp_path):
    """The flag concerns list prices, which the readout never uses."""
    name = names("minimal", "generic")[0]
    write_run(tmp_path, name, [(0.10, 1, 1)], ledger={"complete": False, "flags": ["a0: unpriced model"]})
    write_registry(tmp_path, [held_by(tmp_path, 307, [name])])
    assert shorts(readout_module.readout(tmp_path, STUDY, STAGE, now=NOW), "minimal", "generic") == {name: []}


def test_an_earlier_attempt_adds_its_billed_cost_and_names_its_own_reasons(tmp_path):
    """attempts/<name>.1 cost what was billed for it; its unbilled calls and a log-only attempt 2 make the run short,
    each said with its attempt's number. Whole and valid stay the run's own."""
    name = names("minimal", "generic")[0]
    write_run(tmp_path, name, [(0.10, 1, 1)])
    write_run(tmp_path, name, [(0.20, 1, 2)], folder=f"attempts/{name}.1")
    write_evals(tmp_path, f"attempts/{name}.2", ("c.eval",))
    write_registry(tmp_path, [held_by(tmp_path, 308, [name])])
    result = readout_module.readout(tmp_path, STUDY, STAGE, now=NOW)
    [run] = row(result, "minimal", "generic")["runs"]
    assert run["models_usd"] == pytest.approx(0.30) and run["attempts"] == [1, 2] and run["whole"]
    assert run["short"] == [f"attempt 1: 1 {readout_module.UNBILLED}", f"attempt 2: {readout_module.NO_EXPORT}"]


def test_a_run_in_a_group_is_costed_where_it_sits(tmp_path):
    name = names("minimal", "generic")[0]
    write_run(tmp_path, name, [(0.10, 1, 1)], folder=f"invalid/{name}")
    write_registry(tmp_path, [held_by(tmp_path, 309, [name])])
    result = readout_module.readout(tmp_path, STUDY, STAGE, now=NOW)
    [run] = row(result, "minimal", "generic")["runs"]
    assert (run["run"], run["models_usd"], run["short"], run["boxes"]) == (name, pytest.approx(0.10), [], [309])
    assert result["outside_folders"] == []


def test_a_box_belongs_to_the_study_by_the_runs_it_names_not_by_its_stage_name(tmp_path):
    """A box of another study with a stage name of this table stays out. A box that never ran comes in through its
    launch. A box nothing ties to any study is listed apart, counted in no total, and makes the totals partial. A box
    whose stage is no stage of this table comes in when its capture names a run of the study."""
    name = names("minimal", "generic")[0]
    write_run(tmp_path, name, [(0.10, 1, 1)])
    write_manifest(tmp_path, 401, [f"{REMOTE}/{name}/run.json"])
    write_manifest(tmp_path, 402, ["/root/work/runs/study_later/x_luna1/run.json"])
    write_manifest(tmp_path, 405, [f"{REMOTE}/{name}/judge.jsonl"])
    write_registry(tmp_path, [entry(401, f"{STAGE}-1", *HOURS, 1.0, launch="l000001"),
                              entry(402, f"{STAGE}-1", *HOURS, 5.0, launch="l000002"),
                              entry(403, f"{STAGE}-2", *HOURS, 2.0, launch="l000001"),
                              entry(404, f"{STAGE}-3", *HOURS, 3.0, launch="l000003"),
                              entry(405, "other-1", *HOURS, 4.0, stage=None)])
    result = readout_module.readout(tmp_path, STUDY, now=NOW)
    assert [box["id"] for box in result["boxes"]] == [401, 403, 405]
    assert result["unheld_boxes"] == [403] and [box["id"] for box in result["untied_boxes"]] == [404]
    assert row(result, "minimal", "generic")["runs"][0]["boxes"] == [401, 405]       # both captured a file of it
    assert result["totals"]["total_usd"] == pytest.approx(0.10 + 1.0 + 4.0 + 2.0) and result["totals"]["partial"]
    assert "nothing ties it to a study" in readout_module.text(result)


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


def test_one_stage_reads_its_own_cells_and_boxes_alone(tmp_path):
    here, there = names("minimal", "generic")[0], names("minimal", "generic", stage="phase2-creative-luna")[0]
    write_run(tmp_path, here, [(0.10, 1, 1)])
    write_run(tmp_path, there, [(0.20, 1, 1)])
    write_registry(tmp_path, [held_by(tmp_path, 501, [here]),
                              held_by(tmp_path, 502, [there], stage="phase2-creative-luna")])
    one = readout_module.readout(tmp_path, STUDY, STAGE, now=NOW)
    assert {cell["stage"] for cell in one["cells"]} == {STAGE} and [box["id"] for box in one["boxes"]] == [501]
    assert one["totals"]["models_usd"] == pytest.approx(0.10) and one["totals"]["runs"] == 1
    every = readout_module.readout(tmp_path, STUDY, now=NOW)
    assert every["totals"]["models_usd"] == pytest.approx(0.30) and every["totals"]["runs"] == 2


@pytest.mark.parametrize("gone", ["runs/boxes.json", f"runs/{STUDY}", f"logs/{STUDY}"])
def test_missing_data_is_refused_and_never_read_as_zero(tmp_path, capsys, gone):
    write_registry(tmp_path, [])
    path = tmp_path / gone
    path.unlink() if path.is_file() else path.rmdir()
    with pytest.raises(ValueError, match="never reads as \\$0"):
        readout_module.readout(tmp_path, STUDY, now=NOW)
    with pytest.raises(SystemExit) as refused:
        readout_module.main(["--root", str(tmp_path), "--study", STUDY])
    assert refused.value.code == 2 and str(path) in capsys.readouterr().err


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
