"""src/measures/run_figures.py: the step kinds and positions, the board counts, and the figures of real run folders
(the smoke runs when they exist), each PNG with its CSV twin."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from src.cli import main
from src.measures import run_figures as plots
from src.measures.verdict_fields import FLAGS_BY_TASK
from src.swarm import turn_rules as rules
from tests.support.runs_on_disk import run_on_disk

PNG = b"\x89PNG\r\n\x1a\n"


def test_step_kinds_and_positions():
    assert plots.kind_of({"charge": "work", "tools": ["shell"]}) == "work"
    assert plots.kind_of({"charge": "refused", "tools": ["board_post"]}) == "board"
    assert plots.kind_of({"charge": "free", "tools": ["board_read"]}) == "board"
    assert plots.kind_of({"charge": "work", "tools": ["board_post"]}) == "board"
    assert plots.kind_of({"charge": "submit", "tools": ["submit"]}) == "submit"
    assert plots.kind_of({"charge": "stop", "tools": ["stop_working"], "ended": "stop_working"}) == "stop"
    # a refused submit is charged like work; ORBIT's own submit in an older log was a stop
    assert plots.kind_of({"charge": "refused", "tools": ["submit"]}) == "work"
    assert plots.kind_of({"charge": "submit", "tools": [], "ended": "submitted"}) == "stop"
    # ...and the agent's hand-in for a lone agent and in solo_task
    assert rules.orbit_submit_ending(1, "shared_task") == rules.orbit_submit_ending(3, "solo_task") == "submit"
    assert rules.orbit_submit_ending(3, "shared_task") == "stop_working"
    assert plots.kind_of({"charge": "work", "tools": [], "ended": "submitted"}, None, "submit") == "submit"
    # solo_task: the agent's own submit ends it; where every step is charged, the submission record names the step
    assert plots.kind_of({"charge": "work", "tools": ["submit"], "ended": "submit"}) == "submit"
    lone = {"agent": "a01", "round": 4}
    assert plots.kind_of({"agent": "a01", "step": "r4", "charge": "work", "tools": ["submit"]}, lone) == "submit"
    assert plots.kind_of({"agent": "a01", "step": "r3", "charge": "work", "tools": ["submit"]}, lone) == "work"
    assert plots.position_of({"step": "r3"}) == 3 and plots.position_of({"step": "r3-free2"}) == 3.4
    assert plots.position_of({"step": "r19-v1"}) == 19 and plots.position_of({"step": "r23-a01-v4"}) == 23
    assert plots.position_of({"step": "r3-a01-free2"}) == 3.4 and plots.position_of({"step": "plan1-a01"}) == 0
    counts = plots.board_counts([{"step": "r1", "tools": ["board_status"]},
                                 {"step": "r1-free1", "tools": ["board_read"]}, {"step": "r2", "tools": ["shell"]},
                                 {"step": "r2", "tools": ["board_post", "shell"]}])
    assert counts == {1: {"status": 1, "read": 1, "post": 0}, 2: {"status": 0, "read": 0, "post": 1}}


def test_a_board_run_gets_all_three_figures(tmp_path):
    source = run_on_disk("smoke", "discover")
    written = plots.plot_run(source, tmp_path / "discover")
    assert [p.name for p in written] == ["timeline.png", "board.png", "verdicts.png"]
    for path in written:
        assert path.read_bytes()[:8] == PNG and path.stat().st_size > 10_000, path
        assert path.with_suffix(".csv").is_file()
    timeline = json.loads((source / "run.json").read_text())["rules"]["timeline"]
    rows = list(csv.DictReader((tmp_path / "discover" / "timeline.csv").open()))
    assert len(rows) == len(timeline) and {r["kind"] for r in rows} <= set(plots.KINDS)
    board = list(csv.DictReader((tmp_path / "discover" / "board.csv").open()))
    assert sum(int(r["status"]) + int(r["read"]) + int(r["post"]) for r in board) == \
        sum(1 for e in timeline for t in e.get("tools") or [] if t in plots.BOARD_ACTIONS)
    verdicts = list(csv.DictReader((tmp_path / "discover" / "verdicts.csv").open()))
    assert [r["agent"] for r in verdicts] == ["a01", "a02", "a03"] and all(r["cheated"] == "False" for r in verdicts)


def test_a_run_without_board_actions_gets_no_board_figure(tmp_path):
    split = run_on_disk("smoke", "split_access")
    assert [p.name for p in plots.plot_run(split, tmp_path / "split")] == ["timeline.png", "verdicts.png"]


def test_the_verdict_flags_follow_the_task(tmp_path):
    plots.plot_run(run_on_disk("smoke_kw", "*", "split"), tmp_path / "kw")
    header = next(csv.reader((tmp_path / "kw" / "verdicts.csv").open()))
    assert header == ["agent", "role", *(flag for flag, _, _ in FLAGS_BY_TASK["knowledge_work"])]
    assert all(task in FLAGS_BY_TASK for task in ("openmath_agent", "software_engineering", "knowledge_work",
                                                  "subset_sum", "prime_factorization", "protein_design"))
    assert all(flags[-1][0] == "cheated" for flags in FLAGS_BY_TASK.values())


def test_the_plots_command_writes_under_out(tmp_path, capsys):
    source = run_on_disk("smoke", "discover")
    assert main(["plots", str(source), "--out", str(tmp_path / "cmd")]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert all(line.startswith("== plot ") for line in lines)
    paths = [Path(line.removeprefix("== plot ")) for line in lines]
    assert [p.name for p in paths] == ["timeline.png", "board.png", "verdicts.png"] and all(p.is_file() for p in paths)
