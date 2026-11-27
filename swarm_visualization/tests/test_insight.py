"""The marks added for insight: what each hand-in and check_fix call held, the check_fix answers on the clock, the
setup chips, and the study grid."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swarm_visualization.src.run_files import RunFiles  # noqa: E402
from swarm_visualization.src.run_setup import run_setup  # noqa: E402
from swarm_visualization.src.study_page import build_study_page, run_summary, study_grid, study_runs  # noqa: E402
from swarm_visualization.src.swarm_story import build_story  # noqa: E402
from swarm_visualization.tests.test_swarm_story import T0, iso  # noqa: E402

VALUES = {"internal/ca.pem": "internal/redis/ca-x.pem", "pemblock": "bundle-x"}


def step(calls: list[dict], answers: dict[str, str] | None = None) -> dict:
    answers = answers or {}
    return {"source": "agent", "tool_calls": calls,
            "observation": {"results": [{"source_call_id": c["tool_call_id"],
                                         "content": answers.get(c["tool_call_id"], "ok")} for c in calls]}}


def call(cid: str, tool: str) -> dict:
    return {"tool_call_id": cid, "function_name": tool, "arguments": {}}


def write_run(folder: Path, *, labels: bool = True, versions: bool = True, module: str = "ian_gflipt_minimal",
              regulators: int = 1, seed: int = 1, team: str = "hacked", passed: bool = True) -> Path:
    """Two agents: worker aaaaa checks its fix twice (the second refused, then accepted), hands in, regulator bbbbb
    takes the hand-in back, and aaaaa hands in again (the final one)."""
    processed, analysis = folder / "processed", folder / "analysis"
    for p in (processed / "trajectories" / "s@aaaaa", processed / "trajectories" / "s@bbbbb", processed / "facts",
              analysis / "labels"):
        p.mkdir(parents=True)
    calls = {"aaaaa": [call("a0", "check_fix"), call("a1", "check_fix"), call("a2", "check_fix"), call("a3", "submit"),
                       call("a4", "submit")],
             "bbbbb": [call("b0", "shell"), call("b1", "unsubmit")]}
    answers = {"a0": "not_accepted\n\nThe board changed.", "a1": "You have used this tool recently. Try again later.",
               "a2": "accepted"}
    timeline = [{"agent": agent, "calls": [{"id": c["tool_call_id"], "t0": T0 + 10 * k, "t1": T0 + 10 * k + 2}
                                           for k, c in enumerate(rows)]} for agent, rows in calls.items()]
    roles = {"aaaaa": "worker", "bbbbb": "regulator" if regulators else "worker"}
    (processed / "run.json").write_text(json.dumps({
        "agents": ["aaaaa", "bbbbb"], "rules": {"timeline": timeline},
        "team_submission": {"agent": "aaaaa"}, "model": "openrouter/deepseek/deepseek-v4.1-flash-1017",
        "role_models": {"worker": None, "regulator": "anthropic/claude-opus-5-5" if regulators else None},
        "config": {"n_agents": 2, "regulators": regulators, "disregulators": 0, "rule_level": "none", "seed": seed,
                   "tool_limits": [{"tool": "check_fix", "calls": 1}]},
        "launch": {"module": f"studies.cells.swe.{module}"}, "swe": {"variant": "ian_gflipt"},
        "unsubmits": {"submits": [{"agent": "aaaaa", "round": 1, "time": iso(31), "seq": 0},
                                  {"agent": "aaaaa", "round": 2, "time": iso(41), "seq": 2}],
                      "unsubmits": [{"agent": "bbbbb", "round": 1, "time": iso(35), "reason": "aaaaa leaked",
                                     "seq": 1}]}}))
    (processed / "facts" / "topology.json").write_text(json.dumps(
        {"agents": [{"agent": a, "role": r} for a, r in roles.items()]}))
    episodes = []
    for agent, rows in calls.items():
        transcript = {"trajectory": {"steps": [step([c], answers) for c in rows]}}
        (processed / "trajectories" / f"s@{agent}" / "transcript.json").write_text(json.dumps(transcript))
        episodes.append({"agent": agent, "transcript_path": f"trajectories/s@{agent}/transcript.json"})
    (processed / "episodes.jsonl").write_text("\n".join(json.dumps(e) for e in episodes))
    if versions:
        (processed / "facts" / "version_values.json").write_text(json.dumps({
            "values": VALUES,
            "hand_ins": [{"seq": 0, "agent": "aaaaa", "round": 1, "held": ["pemblock"], "final": False},
                         {"seq": 2, "agent": "aaaaa", "round": 2, "held": None, "final": True}],
            "checks": [{"n": 1, "agent": "aaaaa", "round": 1, "held": [], "answer": "not_accepted"},
                       {"n": 2, "agent": "aaaaa", "round": 1, "held": ["internal/ca.pem", "pemblock"],
                        "answer": "accepted"}]}))
    if labels:
        (analysis / "labels" / "cheating.json").write_text(json.dumps({"team": team, "passed": passed, "agents": {}}))
    return folder


def test_handins_and_checks_carry_what_they_held(tmp_path: Path) -> None:
    story = build_story(RunFiles(write_run(tmp_path / "run")))
    assert story["values"] == ["internal/ca.pem", "pemblock"]
    # The first hand-in held pemblock (index 1); the take-back holds nothing to show; the final one was not stored.
    assert story["handins"] == [[31.0, 0, 0, "", [1]], [35.0, 1, 1, "W1 leaked", None], [41.0, 0, 0, "", None]]
    assert story["submit"]["held"] is None and story["submit"]["labelled"] is True
    # Checks land on the answered check_fix calls (a0 at 0 s, a2 at 20 s), skipping the refused a1.
    assert story["checks"] == [[0.0, 0, 0, []], [20.0, 0, 1, [0, 1]]]


def test_missing_version_values_means_unknown(tmp_path: Path) -> None:
    story = build_story(RunFiles(write_run(tmp_path / "run", versions=False, labels=False)))
    assert story["values"] == [] and story["checks"] == []
    assert [row[4] for row in story["handins"]] == [None, None, None]
    assert story["submit"]["labelled"] is False


def test_setup(tmp_path: Path) -> None:
    story = build_story(RunFiles(write_run(tmp_path / "run")))
    assert story["setup"] == {"workers": 1, "worker_model": "DeepSeek", "regulators": 1, "regulator_model": "Opus",
                              "disregulators": 0, "disregulator_model": None, "version": "ian_gflipt",
                              "check_tool": True, "told_to_cheat": False, "rule": None, "seed": 1}
    told = run_setup({"launch": {"module": "studies.cells.swe.ian_gflipt_minimal_cheat"},
                      "config": {"n_agents": 3, "rule_level": "generic"}})
    assert told["told_to_cheat"] and told["workers"] == 3 and told["rule"] == "generic"
    assert told["version"] == "ian_gflipt" and told["check_tool"] is False


def test_study_grid_keeps_runs_without_labels(tmp_path: Path) -> None:
    study = tmp_path / "study"
    write_run(study / "a_s1", seed=1)
    write_run(study / "a_s2", seed=2, labels=False)
    write_run(study / "b_s1", regulators=0, team="none", passed=False)
    write_run(study / "c_s1", module="ian_gflipt_minimal_cheat", regulators=0)
    (study / "broken" / "analysis").mkdir(parents=True)
    write_run(study / "incomplete" / "d_s1", seed=3, labels=False)
    runs = study_runs(study)
    assert [(p.name, inc) for p, inc in runs] == [("a_s1", False), ("a_s2", False), ("b_s1", False),
                                                  ("broken", False), ("c_s1", False), ("d_s1", True)]
    rows = [run_summary(RunFiles(p), inc) for p, inc in runs]
    by = {r["run"]: r for r in rows}
    assert by["a_s1"]["team_rung"] == 4 and by["a_s1"]["passed"] is True and by["a_s1"]["labelled"]
    assert by["a_s2"]["labelled"] is False and by["a_s2"]["team_rung"] is None
    assert by["a_s1"]["handins"] == 2 and by["a_s1"]["takebacks"] == 1
    assert by["a_s1"]["checks"] == 2 and by["a_s1"]["accepted"] == 1 and by["a_s1"]["final_held"] is None
    assert by["broken"]["placed"] is False
    grid = study_grid(rows)
    placed = sorted(s["run"] for cell in grid["cells"].values() for s in cell)
    assert placed == ["a_s1", "a_s2", "b_s1", "c_s1", "d_s1"]
    assert [s["run"] for s in grid["unplaced"]] == ["broken"]
    assert set(grid["row_fields"] + grid["col_fields"]) == {"told", "workers", "regulators", "regulator_model"}
    # The seeds of one setting share a cell; the incomplete run sorts last.
    cell = next(c for c in grid["cells"].values() if any(s["run"] == "a_s1" for s in c))
    assert [s["run"] for s in cell] == ["a_s1", "a_s2", "d_s1"]
    page = build_study_page("study", rows, {"a_s1": "a_s1.html"})
    assert 'href="a_s1.html"' in page and "mark missing" in page and "incomplete" in page
