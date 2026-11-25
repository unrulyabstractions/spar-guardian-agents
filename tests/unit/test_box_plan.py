"""A plan on boxes without a box: the assignment (gated chains on one box, groups spread by agents, caps summed, the
agents at once), the refusals before a rental, the shell scripts' usage, and box_run.sh with stand-ins for uv and ssh:
the launcher's command, the key over standard input alone, and nothing sent when a check refuses."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.box import fleet_plan
from scripts.box.fleet_layout import ROOT
from scripts.study import gated_study

BOX_FOLDER = ROOT / "scripts" / "box"
PLANS, GATED = ROOT / "studies" / "plans", ROOT / "studies" / "gated"
SCALE = (PLANS / "regulator_scale_1.yaml", GATED / "regulator_scale_1.yaml")
CELL = "studies.cells.swe.ian_flipt_minimal"


def write_plan(folder: Path, targets: dict, needs: dict | None = None, **plan) -> Path:
    """A plan file under folder with these targets, every run on ian_flipt without a pack."""
    body = {"study": "study_x", "seeds": [1], "settings": ["time_limit_s=18000"],
            "targets": {name: {"module": CELL, "model": "deepseek-flash-openrouter", "packs": ["none"], **spec}
                        for name, spec in targets.items()},
            "limits": {"needs": needs if needs is not None else {"swe": {"memory_gb": 0.5, "per": "agent"}}}, **plan}
    path = folder / "study_x.yaml"
    path.write_text(yaml.safe_dump(body))
    return path


@pytest.fixture
def here(tmp_path, monkeypatch):
    """A checkout's root in tmp_path, so a plan written there is one the box's clone would hold."""
    monkeypatch.setattr(fleet_plan, "ROOT", tmp_path)
    return tmp_path


# ---- the assignment ----

def test_a_gate_keeps_its_chain_on_one_box_and_every_arm_runs_somewhere_once():
    assignment = fleet_plan.assign(*SCALE, boxes=4)
    spec = gated_study.load_spec(SCALE[1])
    held = {target: box.name for box in assignment.boxes for target in box.targets}
    names = [box_target for box in assignment.boxes for box_target in box.targets]
    assert len(names) == len(set(names)) == len(spec.cells()) * len(spec.arms) == 40
    for stem, worker in spec.cells():
        for arm in spec.arms:
            for earlier in arm.when:
                later_name = spec.target(stem, worker, arm)[0]
                earlier_name = spec.target(stem, worker, next(a for a in spec.arms if a.name == earlier))[0]
                assert held[later_name] == held[earlier_name], (later_name, earlier_name)
    chains = [group for box in assignment.boxes for group in box.groups if len(group) > 1]
    assert ("ian_flipt_deepseek_n5_reg20_weak", "ian_flipt_deepseek_n5_reg20_strong",
            "ian_flipt_deepseek_n5_reg20_top") in chains and len(chains) == 12


def test_the_groups_go_heaviest_first_to_the_box_with_fewest_agents(here):
    """Weights 20, 10, 10, 5 and 3 agents on two boxes: 20 on the first, both tens on the second, the five on the
    first at the tie of 20 and 20, the three on the second: 25 and 23."""
    plan = write_plan(here, {name: {"settings": [f"n_agents={n}", f"max_concurrency={n}"]}
                             for name, n in (("a", 3), ("b", 10), ("c", 20), ("d", 5), ("e", 10))})
    assignment = fleet_plan.assign(plan, boxes=2)
    assert [(box.groups, box.agents) for box in assignment.boxes] == [((("c",), ("d",)), 25),
                                                                      ((("a",), ("b",), ("e",)), 23)]
    assert [box.name for box in assignment.boxes] == ["study-x-1", "study-x-2"]


def test_a_box_s_caps_are_its_runs_and_the_study_s_their_sum():
    assignment = fleet_plan.assign(PLANS / "disregulator_existence_2.yaml", boxes=2)
    assert [round(box.cap_usd, 2) for box in assignment.boxes] == [45.0, 45.0]
    assert assignment.cap_usd == 6 * 5 + 4 * 15
    scale = fleet_plan.assign(*SCALE, boxes=4)
    assert scale.cap_usd == sum(run.cap_usd(list(scale.boxes[0].settings)) for box in scale.boxes for run in box.runs)
    assert scale.cap_usd == 500.0                                          # the gated arms count, open or not


def test_the_agents_at_once_are_the_smaller_of_cores_and_memory(here):
    plan = write_plan(here, {"a": {}, "b": {}})
    limit = fleet_plan.assign(plan).boxes[0].max_agents
    assert limit == min(int(1.4 * 32), int(0.6 * 64 / 0.5)) == 44
    tight = write_plan(here, {"a": {}, "b": {}}, needs={"swe": {"memory_gb": 1.5, "per": "agent"}})
    assert fleet_plan.assign(tight).boxes[0].max_agents == int(0.6 * 64 / 1.5) == 25
    per_run = write_plan(here, {"a": {}, "b": {}}, needs={"swe": {"memory_gb": 4.5}})     # a run of 3 agents
    assert fleet_plan.assign(per_run).boxes[0].max_agents == int(0.6 * 64 / 1.5)
    assert fleet_plan.assign(plan, max_agents=12).boxes[0].max_agents == 12
    with pytest.raises(ValueError, match="no memory need per agent"):
        fleet_plan.assign(write_plan(here, {"a": {}}, needs={}))
    assert fleet_plan.assign(write_plan(here, {"a": {}}, needs={}), max_agents=8).boxes[0].max_agents == 8


@pytest.mark.parametrize("boxes, max_agents, words", [(3, None, "2 groups of targets for 3 boxes"),
                                                      (0, None, "positive number"), (1, 0, "positive number")])
def test_an_assignment_that_cannot_be_made_is_refused(here, boxes, max_agents, words):
    with pytest.raises(ValueError, match=words):
        fleet_plan.assign(write_plan(here, {"a": {}, "b": {}}), boxes=boxes, max_agents=max_agents)


def test_a_closed_plan_and_a_plan_outside_the_checkout_are_refused(here, tmp_path_factory):
    with pytest.raises(ValueError, match="closed"):
        fleet_plan.assign(write_plan(here, {"a": {}}, closed=True))
    with pytest.raises(ValueError, match="outside this checkout"):
        fleet_plan.assign(write_plan(tmp_path_factory.mktemp("elsewhere"), {"a": {}}))


def test_a_spec_of_another_study_is_refused():
    with pytest.raises(ValueError, match="is of study regulator_scale_1"):
        fleet_plan.assign(PLANS / "disregulator_existence_2.yaml", GATED / "regulator_scale_1.yaml")


def test_every_model_of_a_run_needs_the_box_s_key(here):
    """A role's model counts like the workers': a regulator on OpenAI's own key is refused before any rental."""
    plan = write_plan(here, {"a": {"settings": ["regulators=1", "regulator_model=gpt-6-luna"]}, "b": {}})
    [box] = fleet_plan.assign(plan).boxes
    assert fleet_plan.run_models(box.runs[0], box.settings) == {"deepseek-flash-openrouter", "gpt-6-luna"}
    assert fleet_plan.key_problems(box) == ["gpt-6-luna needs OPENAI_API_KEY, and a box holds only OPENROUTER_API_KEY"]


def test_the_printed_assignment_names_each_box_its_chains_and_the_total():
    text = "\n".join(fleet_plan.lines(fleet_plan.assign(*SCALE, boxes=4)))
    assert "regulator-scale-1-1: 10 runs, 115 agents in all, 44 at once; $125.00 at the runs' caps" in text
    assert "ian_flipt_deepseek_n5_reg20_weak > ian_flipt_deepseek_n5_reg20_strong > ian_flipt_deepseek_n5_reg20_top" \
        in text
    assert "1.4 per core (44; the Mac's figure, no box has measured it)" in text
    assert text.endswith("== the study: 40 runs, $500.00 at the runs' caps")


def test_box_for_reads_a_box_back_and_refuses_a_cut_chain():
    assignment = fleet_plan.assign(*SCALE, boxes=4)
    box = assignment.boxes[0]
    again = fleet_plan.box_for(*SCALE, box.name, box.targets, box.max_agents)
    assert (again.groups, [run.name for run in again.runs], again.cap_usd) == (
        box.groups, [run.name for run in box.runs], box.cap_usd)
    with pytest.raises(ValueError, match="cut"):
        fleet_plan.box_for(*SCALE, box.name, ["ian_flipt_deepseek_n5_reg20_weak"], 44)


# ---- the shell scripts ----

@pytest.mark.parametrize("script", sorted(path.name for path in BOX_FOLDER.glob("*.sh") if "sed -n '2," in
                                          path.read_text()))
def test_a_script_prints_its_whole_header_as_usage(script):
    lines = (BOX_FOLDER / script).read_text().splitlines()
    last = int(next(re.search(r"sed -n '2,(\d+)p'", line).group(1) for line in lines if "sed -n '2," in line))
    assert lines[last - 1].startswith("#") and not lines[last].startswith("#")


def shell_code(path: Path) -> str:
    """A script without its comments."""
    return "\n".join(line for line in path.read_text().splitlines() if not line.lstrip().startswith("#"))


def test_no_script_rents_a_box_or_starts_a_launcher_on_its_own():
    assert not re.search(r"fleet create|box_run\.sh", "\n".join(shell_code(path) for path in BOX_FOLDER.glob("*.sh")
                                                                if path.name != "box_run.sh"))
    callers = [path.name for path in BOX_FOLDER.glob("*.py") if "cmd_create" in path.read_text()]
    assert sorted(callers) == ["fleet.py", "fleet_create.py"]


# ---- box_run.sh ----

LAUNCHER = ["scripts.study.run_study", "studies/plans/study_x.yaml", "--yes", "--only", "t a", "--limit",
            "max_agents=44"]
STAND_IN_UV = """#!/bin/sh
case "$*" in
  *run-args*)
    [ ! -f "$STAND_IN/refuse" ] || { echo "refused: gpt-6-luna needs OPENAI_API_KEY" >&2; exit 1; }
    cat "$STAND_IN/launcher"
    case "$*" in *--mock*) echo --mock ;; esac ;;
  *entry*) echo OPENROUTER_API_KEY ;;
  *box_key*)
    printf '%s\\n' "$*" >> "$STAND_IN/key_checks"
    case "$*" in
      *--main-key*) ;;
      *) [ -n "$BOX_OPENROUTER_API_KEY" ] || { echo "refused: no box key"; exit 1; } ;;
    esac
    [ ! -f "$STAND_IN/key_refuse" ] || { echo "refused: the key has no credit limit"; exit 1; }
    echo "== BOX_OPENROUTER_API_KEY: limit \\$50.00" ;;
esac
"""
STAND_IN_SSH = """#!/bin/sh
printf '%s\\n' "$*" >> "$STAND_IN/ssh_arguments"
cat >> "$STAND_IN/ssh_input"
"""


@pytest.fixture
def box_run(tmp_path):
    """Runs box_run.sh in a throwaway checkout, with uv and ssh replaced by scripts that record what they get."""
    root, stand_in = tmp_path / "repo", tmp_path / "stand_in"
    (root / "scripts" / "box").mkdir(parents=True)
    for name in ("box_run.sh", "box_ssh.sh"):
        shutil.copy2(BOX_FOLDER / name, root / "scripts" / "box" / name)
    (root / "logs" / "boxes" / "7").mkdir(parents=True)
    (root / "logs" / "boxes" / "7" / "ssh_config").write_text("Host box\n")
    stand_in.mkdir()
    (stand_in / "launcher").write_text("\n".join(LAUNCHER) + "\n")
    for name, text in (("uv", STAND_IN_UV), ("ssh", STAND_IN_SSH)):
        (stand_in / name).write_text(text)
        (stand_in / name).chmod(0o755)

    def run(*flags: str, **env: str) -> subprocess.CompletedProcess:
        keep = {name: value for name, value in os.environ.items() if not name.endswith("OPENROUTER_API_KEY")}
        environment = keep | env | {"PATH": f"{stand_in}{os.pathsep}{os.environ['PATH']}", "STAND_IN": str(stand_in)}
        return subprocess.run(["bash", str(root / "scripts" / "box" / "box_run.sh"), "7", *flags], env=environment,
                              capture_output=True, text=True, timeout=30)

    run.sent = lambda name: (stand_in / name).read_text() if (stand_in / name).exists() else ""
    run.stand_in = stand_in
    return run


SCOPED, MAIN = "sk-or-scoped-key-with-a-spend-cap", "sk-or-main-key-of-the-account-0000"


def test_box_run_starts_the_launcher_from_run_args_under_setsid_with_the_box_env(box_run):
    result = box_run(BOX_OPENROUTER_API_KEY=SCOPED, OPENROUTER_API_KEY=MAIN)
    assert result.returncode == 0, result.stdout + result.stderr
    last = box_run.sent("ssh_arguments").splitlines()[-1]
    assert last.endswith(" box bash -s -- 7 scripts.study.run_study studies/plans/study_x.yaml --yes --only t\\ a "
                         "--limit max_agents=44 ")
    started = box_run.sent("ssh_input")
    assert "nohup setsid bash -c" in started and "UV_NO_SYNC=1" in started and "/root/.local/bin" in started
    assert ". /root/.box_env" in started and "echo $$ > /root/launcher.pid" in started
    assert 'exec .venv/bin/python -m "$@"' in started and ">> /root/launcher.log" in started
    assert "refused: a launcher is alive" in started and "refused: /root/box_id names" in started


def test_box_run_sends_the_scoped_key_over_standard_input_alone(box_run):
    result = box_run(BOX_OPENROUTER_API_KEY=SCOPED, OPENROUTER_API_KEY=MAIN)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"export OPENROUTER_API_KEY={SCOPED}" in box_run.sent("ssh_input")
    assert SCOPED not in box_run.sent("ssh_arguments") and SCOPED not in result.stdout + result.stderr
    assert MAIN not in box_run.sent("ssh_input") + box_run.sent("ssh_arguments") + result.stdout
    assert "umask 077" in box_run.sent("ssh_arguments") and "mv /root/.box_env.new /root/.box_env" in box_run.sent(
        "ssh_arguments")
    assert "box_key check OPENROUTER_API_KEY --box-id 7" in box_run.sent("key_checks")
    assert SCOPED not in box_run.sent("key_checks") and MAIN not in box_run.sent("key_checks")


def test_box_run_refuses_without_a_box_key_and_never_sends_the_main_key(box_run):
    result = box_run(OPENROUTER_API_KEY=MAIN)
    assert result.returncode == 1 and "refused: no box key" in result.stdout
    assert box_run.sent("ssh_arguments") == "" and MAIN not in box_run.sent("ssh_input")


def test_box_run_sends_the_main_key_only_with_its_flag_over_standard_input_after_the_check(box_run):
    result = box_run("--main-key", OPENROUTER_API_KEY=MAIN)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"export OPENROUTER_API_KEY={MAIN}" in box_run.sent("ssh_input")
    assert MAIN not in box_run.sent("ssh_arguments") + box_run.sent("key_checks") + result.stdout + result.stderr
    assert "box_key check OPENROUTER_API_KEY --box-id 7 --main-key" in box_run.sent("key_checks")
    assert "--main-key" not in box_run.sent("ssh_arguments")


@pytest.mark.parametrize("flags", [("--main-key",), ()])
def test_box_run_sends_nothing_when_the_key_check_refuses(box_run, flags):
    (box_run.stand_in / "key_refuse").write_text("")
    result = box_run(*flags, BOX_OPENROUTER_API_KEY=SCOPED, OPENROUTER_API_KEY=MAIN)
    assert result.returncode == 1 and "no credit limit" in result.stdout
    assert box_run.sent("ssh_input") == "" and box_run.sent("ssh_arguments") == ""


def test_box_run_refuses_an_unknown_flag_and_sends_nothing(box_run):
    result = box_run("--allow-main-key", OPENROUTER_API_KEY=MAIN, BOX_OPENROUTER_API_KEY=SCOPED)
    assert result.returncode == 2 and "refused: box_run.sh takes --mock and --main-key" in result.stdout
    assert box_run.sent("ssh_arguments") == "" and box_run.sent("ssh_input") == ""


def test_box_run_sends_no_key_with_mock_and_passes_mock_to_the_launcher(box_run):
    result = box_run("--mock", BOX_OPENROUTER_API_KEY=SCOPED)
    assert result.returncode == 0, result.stdout + result.stderr
    assert box_run.sent("ssh_arguments").splitlines()[-1].endswith("max_agents=44 --mock ")
    assert SCOPED not in box_run.sent("ssh_input") and ".box_env.new" not in box_run.sent("ssh_arguments")
    assert box_run.sent("key_checks") == ""


def test_box_run_sends_nothing_when_run_args_refuses(box_run):
    (box_run.stand_in / "refuse").write_text("")
    result = box_run(BOX_OPENROUTER_API_KEY=SCOPED)
    assert result.returncode == 1 and "OPENAI_API_KEY" in result.stderr
    assert box_run.sent("ssh_arguments") == "" and box_run.sent("ssh_input") == ""
